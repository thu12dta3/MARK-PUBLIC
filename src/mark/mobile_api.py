
from __future__ import annotations

import base64
import hmac
import copy
import json
import logging
import os
import queue
import re
import secrets
import signal
import stat
import subprocess
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .cognition.integration import direct_response
from .free_ai import try_public_answer
from .brain import answer
from .audio import transcribe_temporary
from .memory import SharedMemory, ShortMemory
from .core.context import CoreContext
from .core.specializations import default_specializations
from .model_state import ModelSession
from .agenda import Agenda
from .agenda import handle_command as handle_agenda_command
from .intent import control_command, sleep_command
from .tts import speech_wav
from .device_auth import (
    DeviceRegistry,
    SessionRegistry,
    assert_not_llm_approved,
    verify_owner_confirmation,
    CRITICAL_ACTIONS,
)
from . import wear_telemetry
from .web import diagnostics as web_diagnostics

log = logging.getLogger("mark.mobile_api")
TOKEN_FILE = Path.home() / ".config/mark/mobile.token"
MAX_BODY = 12 * 1024 * 1024
MAX_TEXT = 4096
from .attachment_limits import (MAX_ATTACHMENTS as MAX_ATTACHMENT_COUNT,
    MAX_ATTACHMENT_BYTES as MAX_IMAGE_BYTES, MAX_ATTACHMENTS_TOTAL as MAX_IMAGES_TOTAL, MAX_ATTACHMENT_TEXT)
MAX_VOICE_BODY = 1_000_000
HEARTBEAT_SECONDS = 5
CALL_PAGE = Path(__file__).resolve().parents[2] / "web/mobile_call.html"
REASONING_PROFILES = {
    "instant": {"think": False, "num_ctx": 4096, "num_predict": 512},
    "medium": {"think": False, "num_ctx": 6144, "num_predict": 2048},
    "high": {"think": False, "num_ctx": 8192, "num_predict": 4096},
}


def mobile_token(path: Path = TOKEN_FILE) -> str:

    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        token = secrets.token_urlsafe(32)
        create_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        create_flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        with os.fdopen(os.open(path, create_flags, 0o600), "w", encoding="utf-8") as stream:
            stream.write(token + "\n")
        return token
    with os.fdopen(fd, "r", encoding="utf-8") as stream:
        details = os.fstat(stream.fileno())
        if not stat.S_ISREG(details.st_mode) or details.st_uid != os.getuid() or details.st_mode & 0o077:
            raise PermissionError("A chave móvel precisa ser um arquivo privado do usuário (0600).")
        token = stream.read(256).strip()
    if not token:
        raise ValueError("A chave móvel está vazia.")
    return token


def _message_content(content: object, allow_images: bool = False) -> tuple[str, list[str]]:
    if isinstance(content, str):
        return content.strip(), []
    if isinstance(content, list):
        parts = []
        images = []
        image_total = 0
        for item in content:
            if not isinstance(item, dict):
                raise ValueError("Anexos de imagem/áudio do celular ainda não são aceitos nesta API.")
            if item.get("type") == "text" and isinstance(item.get("text"), str):
                parts.append(item["text"])
                continue
            if item.get("type") == "image_url" and allow_images:
                source = item.get("image_url")
                source = source.get("url") if isinstance(source, dict) else source
                if not isinstance(source, str):
                    raise ValueError("Imagem anexada inválida.")
                match = re.fullmatch(r"data:image/(jpeg|png|webp);base64,([A-Za-z0-9+/=]+)", source)
                if not match:
                    raise ValueError("Use imagens JPEG, PNG ou WebP locais; links externos não são aceitos.")
                try:
                    data = base64.b64decode(match.group(2), validate=True)
                except ValueError:
                    raise ValueError("A imagem anexada está corrompida.") from None
                image_total += len(data)
                if len(data) > MAX_IMAGE_BYTES or image_total > MAX_IMAGES_TOTAL:
                    raise ValueError("As imagens passam do limite de 6 MB por arquivo e 8 MB no total.")
                if len(images) >= MAX_ATTACHMENT_COUNT:
                    raise ValueError("Anexe no máximo cinco arquivos por mensagem.")
                from .vision_quality import inspect_frame
                inspect_frame(data)
                images.append(base64.b64encode(data).decode("ascii"))
                continue
            raise ValueError("Tipo de anexo inválido nesta mensagem.")
        return "\n".join(parts).strip(), images
    raise ValueError("Mensagem sem texto válido.")


def _message_text(content: object) -> str:
    text, images = _message_content(content)
    if images:
        raise ValueError("Imagens só podem aparecer na mensagem mais recente.")
    return text


def _conversation(payload: dict, cfg: dict) -> tuple[str, ShortMemory, list[str], str]:



    model_str = payload.get("model")
    if not isinstance(model_str, str) or not model_str:
        raise ValueError("Selecione 'mark' ou 'mark-call' no aplicativo.")
    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages or len(messages) > 32:
        raise ValueError("Histórico de conversa inválido ou longo demais.")
    last = messages[-1]
    if not isinstance(last, dict) or last.get("role") != "user":
        raise ValueError("A última mensagem precisa ser do usuário.")
    command, images = _message_content(last.get("content"), allow_images=True)
    if not command or len(command) > MAX_TEXT:
        raise ValueError("A pergunta está vazia ou longa demais.")

    files = payload.get("mark_attachments", [])
    if not isinstance(files, list) or len(files) > MAX_ATTACHMENT_COUNT:
        raise ValueError("Anexe no máximo cinco arquivos por mensagem.")
    if len(files) + len(images) > MAX_ATTACHMENT_COUNT:
        raise ValueError("Anexe no máximo cinco arquivos por mensagem.")
    snippets = []
    text_size = 0
    for file in files:
        if not isinstance(file, dict) or not isinstance(file.get("name"), str) or not isinstance(file.get("text"), str):
            raise ValueError("Conteúdo de arquivo anexado inválido.")
        name, text = file["name"][:200], file["text"]
        text_size += len(text)
        if text_size > MAX_ATTACHMENT_TEXT:
            raise ValueError("O texto extraído dos anexos passou de 400 mil caracteres.")
        snippets.append(f"Arquivo: {name}\n{text}")
    attachment_context = "\n\n".join(snippets)

    memory = ShortMemory(int(cfg["memory"]["max_messages"]))
    pending = None
    for message in messages[:-1]:
        if not isinstance(message, dict):
            continue
        if message.get("role") == "user":
            pending = _message_text(message.get("content"))[:600]
        elif message.get("role") == "assistant" and pending:
            memory.add(pending, _message_text(message.get("content"))[:800])
            pending = None
    return command, memory, images, attachment_context


class _DesktopConversationMemory(ShortMemory):


    def __init__(self, conversation: ShortMemory, shared: SharedMemory):
        super().__init__(conversation.items.maxlen)
        for turn in conversation.history():
            self.add(turn["user"], turn["assistant"], turn.get("source", "desktop-chat"))
        self.shared = shared

    def recall_facts(self, *args, **kwargs):
        return self.shared.recall_facts(*args, **kwargs)


def _core_context(payload: dict, origin: str) -> CoreContext:

    supplied = payload.get("mark_context", {})
    if not isinstance(supplied, dict) or set(supplied) - {"session_id", "project_id", "task_id", "specialization"}:
        raise ValueError("Contexto do MARK inválido")
    specialization = supplied.get("specialization", "general")
    if not isinstance(specialization, str):
        raise ValueError("Especialização inválida")
    default_specializations().resolve(specialization)
    source = {"pc": "desktop-chat", "celular": "mobile-chat",
              "relógio": "wear-chat", "cliente": "api-chat"}.get(origin, "api-chat")
    return CoreContext.create(
        session_id=supplied.get("session_id"),
        project_id=supplied.get("project_id"),
        task_id=supplied.get("task_id"),
        specialization=specialization,
        source=source,
    )


def _completion(reply: str, request_id: str, created: int) -> dict:
    return {"id": request_id, "object": "chat.completion", "created": created, "model": "mark",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": reply},
                         "finish_reason": "stop"}]}


class MobileServer(ThreadingHTTPServer):
    daemon_threads = False
    request_queue_size = 32

    def __init__(self, cfg: dict, token: str, port: int = 8765,
                 memory: SharedMemory | ShortMemory | None = None,
                 devices: DeviceRegistry | None = None, mission_store=None):
        self.cfg = cfg
        self.token = token
        self.busy = threading.BoundedSemaphore(1)
        self.connection_slots = threading.BoundedSemaphore(32)
        self.model_cfgs = {}
        for profile, model in cfg.get("models", {}).items():
            selected = copy.deepcopy(cfg)
            selected["ollama"]["model"] = model
            selected["ollama"]["think"] = bool(cfg.get("thinking", {}).get(profile, False))
            selected["interaction_mode"] = profile
            self.model_cfgs[profile] = selected
        self.model_sessions = {name: ModelSession(selected) for name, selected in self.model_cfgs.items()}
        self.model_session = self.model_sessions.get("call", ModelSession(cfg))
        self.memory = memory if memory is not None else SharedMemory(int(cfg["memory"]["max_messages"]))
        self.agenda = Agenda()


        self.mission_store = mission_store
        self.godseye_manager = None
        self._godseye_lock = threading.Lock()
        self.devices = devices if devices is not None else DeviceRegistry()
        self.sessions = SessionRegistry(self.devices)
        self._owns_devices = devices is None
        self.reminder_stop = threading.Event()
        self.reminder_thread = threading.Thread(target=self._reminder_loop, name="mark-reminders", daemon=True)
        super().__init__(("127.0.0.1", port), MobileHandler)
        self.reminder_thread.start()

    def process_request(self, request, client_address) -> None:


        if not self.connection_slots.acquire(blocking=False):
            try:
                request.sendall(b"HTTP/1.1 503 Service Unavailable\r\n"
                                b"Content-Length: 0\r\nConnection: close\r\n\r\n")
            except OSError:
                pass
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.connection_slots.release()
            raise

    def process_request_thread(self, request, client_address) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.connection_slots.release()

    def _reminder_loop(self) -> None:
        while not self.reminder_stop.wait(15):
            try:
                for item in self.agenda.due_unnotified():
                    result = subprocess.run(
                        ["notify-send", "--app-name=MARK", "MARK", item["title"]],
                        capture_output=True, timeout=5, check=False,
                    )
                    if result.returncode == 0:
                        self.agenda.mark_notified(item["id"])
            except (OSError, subprocess.TimeoutExpired):

                continue

    def _godseye(self):
        with self._godseye_lock:
            if self.godseye_manager is None:
                from .gods_eye_view import GodsEyeManager
                self.godseye_manager = GodsEyeManager()
            return self.godseye_manager

    def _godseye_mcp(self):
        from .mcp_gods_eye import GodsEyeMCPBridge
        return GodsEyeMCPBridge(manager=self._godseye())

    def server_close(self) -> None:
        try:
            self.reminder_stop.set()
            self.reminder_thread.join(timeout=2)
            self.sessions.clear()
            super().server_close()
            if self._owns_devices:
                self.devices.close()
        finally:
            if self.godseye_manager is not None:
                self.godseye_manager.close()
            for session in self.model_sessions.values():
                if session.awake:
                    session.sleep()
            if not self.model_sessions:
                self.model_session.sleep()


class MobileHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: MobileServer

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(15)

    def parse_request(self) -> bool:
        if not super().parse_request():
            return False

        protected = ("Authorization", "Content-Length", "X-Mark-Device", "X-Mark-Session",
                     "X-Mark-Time", "X-Mark-Nonce", "X-Mark-Signature", "X-Mark-Request-Id",
                     "X-Mark-Owner-Time", "X-Mark-Owner-Nonce", "X-Mark-Owner-Signature")
        if self.headers.get("Transfer-Encoding") or any(len(self.headers.get_all(key, [])) > 1 for key in protected):
            self._error(400, "Cabeçalhos ambíguos não são aceitos.")
            return False
        return True

    def log_message(self, _format: str, *_args) -> None:

        return

    def _json(self, status: int, body: dict) -> None:
        if status < 300 and getattr(self, "_authenticated", False) and not self._active_device():
            status, body = 403, {"error": {"message": "Acesso revogado ou bloqueado.", "type": "invalid_request_error"}}
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)
        self.close_connection = True

    def _error(self, status: int, message: str) -> None:
        log.info("API móvel: pedido recusado com HTTP %d.", status)
        self._json(status, {"error": {"message": message, "type": "invalid_request_error"}})

    def _reject_plain_http(self) -> bool:
        forwarded_proto = self.headers.get("X-Forwarded-Proto", "").strip().lower()
        forwarded_port = self.headers.get("X-Forwarded-Port", "").strip()
        forwarded_hdr = self.headers.get("Forwarded", "").strip().lower()
        if (
            (forwarded_proto and forwarded_proto != "https")
            or forwarded_port == "80"
            or "proto=http;" in forwarded_hdr
            or forwarded_hdr.endswith("proto=http")
            or "proto=http," in forwarded_hdr
        ):
            self._error(403, "Tráfego HTTP plano (porta 80) desativado. Use Tailscale Serve HTTPS com TLS 1.3.")
            return True
        return False

    def _authorized(self) -> bool:

        if self.server.devices.lockdown():
            self._error(403, "Acesso móvel bloqueado no computador.")
            return False
        if self._reject_plain_http():
            return False
        device_id = self.headers.get("X-Mark-Device", "")
        if device_id:
            path = urlsplit(self.path).path
            session_token = self.headers.get("X-Mark-Session", "")
            request_id = self.headers.get("X-Mark-Request-Id", "")

            if self.server.devices.status(device_id) != "authorized":
                self._error(401, "Aparelho não autorizado.")
                return False
            if path not in {"/v1/session", "/v1/session/renew"} and not self.server.sessions.valid(device_id, session_token):
                self._error(401, "Sessão expirada ou aparelho não autorizado.")
                return False
            if path == "/v1/session" and (session_token or request_id):
                self._error(401, "A abertura de sessão requer assinatura do aparelho.")
                return False
            if path == "/v1/session/renew" and not session_token:
                self._error(401, "A renovação de sessão requer token de sessão ativo.")
                return False
            body = b""
            if self.command == "POST":
                try:
                    length = int(self.headers.get("Content-Length", ""))
                except ValueError:
                    self._error(411, "Envie Content-Length válido.")
                    return False
                if length < 0 or length > MAX_BODY:
                    self._error(413, "Requisição longa demais.")
                    return False
                body = self.rfile.read(length)
                if len(body) != length:
                    self._error(400, "Corpo incompleto.")
                    return False
            self._verified_body = body
            valid = self.server.devices.verify(device_id, self.command, path, body,
                                               self.headers.get("X-Mark-Time", ""),
                                               self.headers.get("X-Mark-Nonce", ""),
                                               self.headers.get("X-Mark-Signature", ""),
                                               request_id, session_token)
            if valid:
                self._device_id = device_id
                self._authenticated = True
                return True
            self._error(401, "Aparelho não autorizado ou assinatura inválida.")
            return False
        proxy_or_tailscale_headers = (
            "Tailscale-User-Login",
            "Tailscale-User-Name",
            "Tailscale-Headers-Info",
            "X-Forwarded-For",
            "X-Forwarded-Host",
            "X-Real-IP",
            "Forwarded",
        )
        if any(self.headers.get(h) for h in proxy_or_tailscale_headers):
            self._error(
                401,
                "Tokens Bearer estáticos legados foram desativados no Tailscale. Use chave ECDSA rotativa vinculada ao aparelho.",
            )
            return False
        client_ip = str(self.client_address[0]) if getattr(self, "client_address", None) else ""
        if client_ip and client_ip not in {"127.0.0.1", "::1", "::ffff:127.0.0.1"}:
            self._error(401, "Token local restrito exclusivamente ao loopback (127.0.0.1).")
            return False
        provided = self.headers.get("Authorization", "")
        expected = "Bearer " + self.server.token
        valid = bool(self.server.token) and hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))
        log.info("API móvel: rota conhecida, chave %s.", "aceita" if valid else "rejeitada")
        if valid:
            self._authenticated = True
            return True
        self._error(401, "Chave de acesso inválida.")
        return False

    def _active_device(self) -> bool:
        if self.server.devices.lockdown():
            return False
        device_id = getattr(self, "_device_id", None)
        return device_id is None or self.server.devices.status(device_id) == "authorized"

    def do_GET(self) -> None:
        if self._reject_plain_http():
            return
        path = urlsplit(self.path).path
        if path == "/v1/pair/status":
            device_id = self.headers.get("X-Mark-Device", "")
            if not device_id or len(device_id) > 64:
                self._error(400, "Identidade inválida.")
                return
            self._json(200, {"status": self.server.devices.status(device_id)})
            return
        if path == "/":
            self.send_response(302)
            self.send_header("Location", "/call")
            self.send_header("Content-Length", "0")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            return
        if path == "/call":
            data = CALL_PAGE.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; media-src 'self' blob:")
            self.end_headers()
            self.wfile.write(data)
            self.close_connection = True
            return
        if path not in {"/v1/models", "/health", "/v1/agenda", "/v1/history", "/v1/wear/summary"}:
            log.info("API móvel: app consultou uma rota GET não suportada.")
            self._error(404, "Rota não encontrada.")
            return
        if not self._authorized():
            return
        if path == "/v1/wear/summary":
            wear_telemetry.get_summary(self)
            return
        if path == "/health":
            from .plugins import enabled as plugin_enabled
            web_enabled = (self.server.cfg.get("web", {}).get("enabled", True)
                           and plugin_enabled("web", self.server.cfg))
            self._json(200, {"status": "ok", "internet": web_diagnostics(web_enabled)})
        elif path == "/v1/history":
            self._json(200, {"items": self.server.memory.history(30)})
        elif path == "/v1/agenda":
            self._json(200, {"items": self.server.agenda.list_items()})
        else:
            self._json(200, {"object": "list", "data": [{"id": "mark", "object": "model",
                                                              "created": 0, "owned_by": "local"},
                                                {"id": "mark-call", "object": "model",
                                                 "created": 0, "owned_by": "local"}]})

    def do_POST(self) -> None:
        if self._reject_plain_http():
            return
        path = urlsplit(self.path).path
        if path == "/v1/pair/enroll":
            try:
                length = int(self.headers.get("Content-Length", ""))
                if not 2 <= length <= 2048:
                    raise ValueError("pedido grande demais")
                data = json.loads(self.rfile.read(length))
                device_id = self.server.devices.enroll(data["code"], data["name"],
                                                        data["public_key"], data["proof"])
            except (ValueError, KeyError, TypeError, RecursionError):
                self._error(400, "Pareamento inválido, expirado ou indisponível.")
                return
            self._json(202, {"device_id": device_id, "status": "pending"})
            return
        if path == "/v1/session":
            if not self.headers.get("X-Mark-Device"):
                self._error(401, "A abertura de sessão requer identidade do aparelho.")
                return
            if not self._authorized():
                return
            if getattr(self, "_verified_body", b""):
                self._error(400, "A abertura de sessão não aceita dados.")
                return
            try:
                token, expiry = self.server.sessions.issue(self._device_id)
            except ValueError:
                self._error(401, "Aparelho não autorizado ou limite de sessões atingido.")
                return
            self._json(200, {"session_token": token, "expires_at": expiry})
            return
        if path == "/v1/session/renew":
            if not self.headers.get("X-Mark-Device"):
                self._error(401, "A renovação de sessão requer identidade do aparelho.")
                return
            if not self._authorized():
                return
            if getattr(self, "_verified_body", b""):
                self._error(400, "A renovação de sessão não aceita dados.")
                return
            session_token = self.headers.get("X-Mark-Session", "")
            try:
                token, expiry = self.server.sessions.renew(self._device_id, session_token)
            except ValueError:
                self._error(401, "Sessão inválida ou expirada para renovação.")
                return
            self._json(200, {"session_token": token, "expires_at": expiry})
            return
        if path == "/v1/device/rotate":
            device_id = self.headers.get("X-Mark-Device", "")
            if not device_id:
                self._error(401, "A rotação de chave requer identidade do aparelho.")
                return
            try:
                length = int(self.headers.get("Content-Length", ""))
                if not 2 <= length <= 2048:
                    raise ValueError("pedido grande demais")
                data = json.loads(self.rfile.read(length))
                new_device_id = self.server.devices.rotate_key(
                    device_id,
                    data["new_public_key"],
                    self.headers.get("X-Mark-Time", ""),
                    self.headers.get("X-Mark-Nonce", ""),
                    self.headers.get("X-Mark-Signature", ""),
                    data["proof"],
                    self.headers.get("X-Mark-Request-Id", ""),
                    self.headers.get("X-Mark-Session", ""),
                )
            except (ValueError, KeyError, TypeError, RecursionError):
                self._error(401, "Rotação de chave ECDSA inválida ou não autorizada.")
                return
            self._json(200, {"device_id": new_device_id, "status": "authorized"})
            return
        if path not in {"/v1/chat/completions", "/v1/voice/turn", "/v1/wear/telemetry"}:
            log.info("API móvel: app consultou uma rota POST não suportada.")
            self._error(404, "Rota não encontrada.")
            return
        if not self._authorized():
            return
        if path == "/v1/wear/telemetry":
            wear_telemetry.post_events(self)
            return
        if path == "/v1/voice/turn":
            self._voice_turn()
            return
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self._error(411, "Envie Content-Length válido.")
            return
        if length < 2 or length > MAX_BODY:
            self._error(413, "Requisição longa demais.")
            return
        try:
            payload = json.loads(getattr(self, "_verified_body", None) or self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("Requisição JSON inválida.")
            self._wear_origin = payload.get("mark_origin") == "wear"
            command, client_memory, images, attachment_context = _conversation(payload, self.server.cfg)
            core_context = _core_context(payload, self._origin())
            reasoning_level = payload.get("mark_reasoning_level", "medium")
            if not isinstance(reasoning_level, str) or reasoning_level not in REASONING_PROFILES:
                raise ValueError("Nível de resposta inválido.")
            action = control_command(command)
            if action in {"clear_memory", "shutdown"}:
                assert_not_llm_approved(self._origin(), action)
                if getattr(self, "_device_id", None):
                    owner_sig = self.headers.get("X-Mark-Owner-Signature", "")
                    owner_time = self.headers.get("X-Mark-Owner-Time", "")
                    owner_nonce = self.headers.get("X-Mark-Owner-Nonce", "")
                    if not (owner_sig and owner_time and owner_nonce and verify_owner_confirmation(
                        self.server.devices, self._device_id, action, owner_time, owner_nonce, owner_sig
                    )):
                        self._error(403, "Esta ação exige confirmação do dono no computador.")
                        return
            profile = "call" if payload.get("model") == "mark-call" else "chat"


            if self._origin() == "pc":
                memory = _DesktopConversationMemory(client_memory, self.server.memory)
            else:
                memory = self.server.memory if self.server.memory.render() != "Nenhuma." else client_memory
        except (ValueError, UnicodeDecodeError, RecursionError):
            self._error(400, "Mensagem inválida. Use texto e selecione mark ou mark-call.")
            return
        if not self.server.busy.acquire(blocking=False):
            self._error(429, "MARK está respondendo outro pedido; tente novamente.")
            return
        try:
            if not self._active_device():
                self._error(401, "Aparelho revogado ou bloqueado.")
                return
            request_id = "chatcmpl-" + uuid.uuid4().hex
            created = int(time.time())
            if payload.get("stream") is True:
                self._stream_answer(command, memory, request_id, created, profile, images,
                                    attachment_context, core_context, reasoning_level)
                return
            try:
                reply = self._answer(command, memory, profile, images, attachment_context,
                                     core_context, reasoning_level)
            except Exception as exc:
                log.error("Falha ao responder pelo celular (%s).", type(exc).__name__)
                self._error(502, "MARK não conseguiu responder agora.")
                return
            log.info("API móvel: resposta textual com %d caracteres; somente OK: %s.",
                     len(reply), reply.casefold().strip(" .!") == "ok")
            if not self._active_device():
                self._error(401, "Aparelho revogado ou bloqueado.")
                return
            self._remember(command, reply, self._source("chat"))
            self._json(200, _completion(reply, request_id, created))
        finally:
            self.server.busy.release()

    def _answer(self, command: str, memory: ShortMemory, profile: str = "chat",
                images: list[str] | None = None, attachment_context: str = "",
                context: CoreContext | None = None, reasoning_level: str = "medium") -> str:
        action = control_command(command)
        if action in {"clear_memory", "shutdown"}:
            assert_not_llm_approved(self._origin(), action)
            if getattr(self, "_device_id", None):
                owner_sig = getattr(self, "headers", {}).get("X-Mark-Owner-Signature", "")
                owner_time = getattr(self, "headers", {}).get("X-Mark-Owner-Time", "")
                owner_nonce = getattr(self, "headers", {}).get("X-Mark-Owner-Nonce", "")
                if not (owner_sig and owner_time and owner_nonce and verify_owner_confirmation(
                    self.server.devices, self._device_id, action, owner_time, owner_nonce, owner_sig
                )):
                    raise PermissionError("Ação crítica sem prova de presença do dono")


        use_engineer = (profile == "chat" and not images and context is not None
                        and context.specialization == "engineer")
        selected_profile = "engineer" if use_engineer else profile
        selected_cfg = copy.deepcopy(self.server.model_cfgs.get(selected_profile, self.server.cfg))
        if profile == "chat":
            level = reasoning_level if reasoning_level in REASONING_PROFILES else "medium"
            selected_cfg["reasoning_level"] = level
            selected_cfg["ollama"].update(REASONING_PROFILES[level])
        selected_session = self.server.model_sessions.get(selected_profile, self.server.model_session)
        is_awake = any(session.awake for session in self.server.model_sessions.values()) or self.server.model_session.awake
        agenda_reply = handle_agenda_command(command, self.server.agenda)
        if agenda_reply is not None:
            return agenda_reply
        from .mission_intent import handle_general_mission_request, is_engineer_delegation, is_mission_status_question
        if is_mission_status_question(command) or is_engineer_delegation(command):
            if self.server.mission_store is None:
                from .missions import MissionStore
                self.server.mission_store = MissionStore()
            mission_reply = handle_general_mission_request(command, self.server.mission_store, self._origin())
            if mission_reply is not None:
                return mission_reply
        from .gods_eye_intent import handle_godseye_intent, parse_godseye_intent
        if parse_godseye_intent(command) is not None:
            from .plugins import enabled as plugin_enabled
            if not plugin_enabled("gods_eye", self.server.cfg):
                return "O plugin God’s Eye View está desativado. Você pode ativá-lo em Ferramentas → Plugins no MARK para PC."
            manager = self.server._godseye()
            return handle_godseye_intent(command, manager)
        action = control_command(command)
        if action == "clear_memory":
            self.server.memory.clear()
            return "Memória recente limpa em todos os dispositivos."
        if action == "shutdown":
            for session in set(self.server.model_sessions.values()):
                session.sleep()
            return "Até mais."
        if sleep_command(command, selected_cfg if is_awake else None):
            for session in set(self.server.model_sessions.values()):
                session.sleep()
            return "Vou descansar. É só me chamar quando precisar."
        direct = direct_response(command, selected_cfg, memory=memory)
        if direct is not None:
            return direct

        free_reply = try_public_answer(command, selected_cfg, origin=self._origin(),
                                       images=images, attachment_context=attachment_context)
        if free_reply is not None:
            return free_reply
        fallback_notice = ""
        if not selected_session.awake:
            try:



                selected_key = getattr(selected_session, "model_key", None)
                for other_name, other_session in self.server.model_sessions.items():
                    other_key = getattr(other_session, "model_key", None)
                    if (selected_key is not None and other_name != selected_profile
                            and other_key is not None and other_key != selected_key):
                        other_session.sleep()
                selected_session.wake()
            except Exception as exc:
                if not use_engineer:
                    raise


                log.warning("Modelo Engineer indisponível; usando modelo Geral (%s).", type(exc).__name__)
                selected_profile = "chat"
                selected_cfg = copy.deepcopy(self.server.model_cfgs.get("chat", self.server.cfg))
                selected_session = self.server.model_sessions.get("chat", self.server.model_session)
                if not selected_session.awake:
                    selected_session.wake()
                fallback_notice = "O modelo especializado do Engineer não está disponível; respondi usando o modelo Geral.\n\n"
        if command.strip().lower() == "mark":
            return "Estou ouvindo."
        try:
            reply = answer(command, selected_cfg, memory, images=images,
                           attachment_context=attachment_context, origin=self._origin(), context=context)
        except Exception as exc:
            from .brain_model import EngineerModelError
            if not use_engineer or not isinstance(exc, EngineerModelError):
                raise


            log.warning("Inferência Engineer falhou; fallback único para Geral.")
            selected_session.sleep()
            fallback_notice = "O modelo especializado do Engineer falhou; respondi usando o modelo Geral.\n\n"
            selected_profile = "chat"
            selected_cfg = copy.deepcopy(self.server.model_cfgs.get("chat", self.server.cfg))
            selected_cfg["reasoning_level"] = reasoning_level if reasoning_level in REASONING_PROFILES else "medium"
            selected_cfg["ollama"].update(REASONING_PROFILES[selected_cfg["reasoning_level"]])
            selected_session = self.server.model_sessions.get("chat", self.server.model_session)
            if not selected_session.awake:
                selected_session.wake()
            reply = answer(command, selected_cfg, memory, images=images,
                           attachment_context=attachment_context, origin=self._origin(), context=context)
        return fallback_notice + reply

    def _origin(self) -> str:
        if getattr(self, "_device_id", None):
            return "relógio" if getattr(self, "_wear_origin", False) else "celular"
        return "pc" if getattr(self, "headers", {}).get("X-Mark-Client") == "desktop" else "cliente"

    def _source(self, kind: str) -> str:
        return {"pc": "desktop", "celular": "mobile", "relógio": "wear", "cliente": "legacy"}[self._origin()] + "-" + kind

    def _remember(self, command: str, reply: str, source: str) -> None:
        if control_command(command) != "clear_memory":
            self.server.memory.add(command, reply, source=source)

    def _stream_answer(self, command: str, memory: ShortMemory, request_id: str, created: int,
                       profile: str = "chat", images: list[str] | None = None,
                       attachment_context: str = "", context: CoreContext | None = None,
                       reasoning_level: str = "medium") -> None:
        results: queue.Queue[tuple[bool, str]] = queue.Queue(maxsize=1)

        def compute() -> None:
            try:
                results.put((True, self._answer(command, memory, profile, images,
                                                attachment_context, context, reasoning_level)))
            except Exception as exc:
                log.error("Falha ao responder pelo celular (%s).", type(exc).__name__)
                results.put((False, "MARK não conseguiu responder agora."))

        worker = threading.Thread(target=compute, daemon=True)
        worker.start()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            self._stream_event({"role": "assistant"}, None, request_id, created)



            self._stream_event({"content": " "}, None, request_id, created)
            while True:
                try:
                    succeeded, reply = results.get(timeout=HEARTBEAT_SECONDS)
                    break
                except queue.Empty:
                    self.wfile.write(b": MARK processando\n\n")
                    self.wfile.flush()
            if succeeded:
                if not self._active_device():
                    self.wfile.write(b"data: {\"error\":{\"message\":\"Aparelho revogado.\"}}\n\n")
                    return
                log.info("API móvel: resposta de voz/chat com %d caracteres; somente OK: %s.",
                         len(reply), reply.casefold().strip(" .!") == "ok")
                self._remember(command, reply, self._source("chat"))
                self._stream_event({"content": reply}, None, request_id, created)
                self._stream_event({}, "stop", request_id, created)
            else:
                self.wfile.write(("data: " + json.dumps({"error": {"message": reply}}) + "\n\n").encode())
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            worker.join()
            self.close_connection = True

    def _stream_event(self, delta: dict, finish: str | None, request_id: str, created: int) -> None:
        event = {"id": request_id, "object": "chat.completion.chunk", "created": created,
                 "model": "mark", "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
        self.wfile.write(("data: " + json.dumps(event, ensure_ascii=False) + "\n\n").encode("utf-8"))
        self.wfile.flush()

    def _voice_turn(self) -> None:

        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self._error(411, "Envie o tamanho do áudio.")
            return
        if not 5760 <= length <= MAX_VOICE_BODY or length % 2:
            self._error(413, "Áudio vazio ou longo demais (limite: cerca de 30 segundos).")
            return
        if not self.server.busy.acquire(blocking=False):
            self._error(429, "MARK está respondendo outro pedido; tente novamente.")
            return
        try:
            if not self._active_device():
                self._error(401, "Aparelho revogado ou bloqueado.")
                return
            pcm = getattr(self, "_verified_body", None) or self.rfile.read(length)
            if len(pcm) != length:
                self._error(400, "Áudio incompleto.")
                return
            tmp = Path(self.server.cfg["paths"]["tmp"])
            tmp.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="mark-call-", dir=tmp) as directory:
                text = transcribe_temporary(pcm, Path(directory) / "utterance.wav",
                                            self.server.cfg["whisper"]["command_model"],
                                            self.server.cfg, language="pt")
            text = text.strip()
            if not text:
                self._error(422, "Não consegui entender sua fala. Tente novamente.")
                return
            action = control_command(text)
            if action in {"clear_memory", "shutdown"}:
                assert_not_llm_approved(self._origin(), action)
                if getattr(self, "_device_id", None):
                    owner_sig = self.headers.get("X-Mark-Owner-Signature", "")
                    owner_time = self.headers.get("X-Mark-Owner-Time", "")
                    owner_nonce = self.headers.get("X-Mark-Owner-Nonce", "")
                    if not (owner_sig and owner_time and owner_nonce and verify_owner_confirmation(
                        self.server.devices, self._device_id, action, owner_time, owner_nonce, owner_sig
                    )):
                        self._error(403, "Esta ação exige confirmação do dono no computador.")
                        return
            dismissed = control_command(text) == "shutdown" or sleep_command(
                text, self.server.cfg if any(session.awake for session in self.server.model_sessions.values()) else None)
            reply = self._answer(text, self.server.memory, "call")
            data = speech_wav(reply, "pt", self.server.cfg)
            if not self._active_device():
                self._error(401, "Aparelho revogado ou bloqueado.")
                return
            self._remember(text, reply, self._source("call"))
            log.info("API móvel: turno de chamada concluído; entrada=%d caracteres, resposta=%d caracteres.",
                     len(text), len(reply))
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Mark-Sleep", "1" if dismissed else "0")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(data)
            self.close_connection = True
        except Exception as exc:
            log.error("Falha no turno de chamada (%s).", type(exc).__name__)
            self._error(502, "MARK não conseguiu concluir esta fala. Tente novamente.")
        finally:
            self.server.busy.release()


def serve_mobile(cfg: dict, port: int = 8765) -> None:
    if not 1 <= port <= 65535 or port == 80:
        raise ValueError("A porta da API deve estar entre 1 e 65535 (porta 80 HTTP legada proibida).")
    token = mobile_token()
    with MobileServer(cfg, token, port) as server:
        log.info("API móvel do MARK ativa somente em 127.0.0.1:%d (TLS 1.3 via Tailscale Serve :443).", port)
        previous = signal.getsignal(signal.SIGTERM)

        def stop(_signum, _frame):
            threading.Thread(target=server.shutdown, daemon=True).start()

        signal.signal(signal.SIGTERM, stop)
        try:
            server.serve_forever(poll_interval=0.2)
        finally:
            signal.signal(signal.SIGTERM, previous)

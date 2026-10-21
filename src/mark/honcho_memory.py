
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import queue
from pathlib import Path
from urllib.parse import urlsplit

from .config import CONFIG_FILE, DEFAULTS, load_config

_lock = threading.Lock()
_last_error = ""
MAX_CONTEXT_CHARS = 1800
MAX_MESSAGE_CHARS = 6000
_pending_turns: queue.Queue[tuple[str, str]] = queue.Queue(maxsize=32)
_worker_started = False


def _safe_url(value: str) -> str:
    if not isinstance(value, str) or len(value) > 500:
        raise ValueError("Endereço do Honcho inválido.")
    parsed = urlsplit(value.strip())
    if (parsed.scheme not in {"https", "http"} or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment):
        raise ValueError("Use um endereço HTTPS válido para o Honcho.")
    if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("HTTP só é permitido para Honcho local; use HTTPS para servidores remotos.")
    return value.strip().rstrip("/")


def _validate_id(value: str, label: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,96}", value):
        raise ValueError(f"Identificador de {label} inválido.")
    return value


def settings() -> dict:
    return dict(load_config().get("honcho", DEFAULTS["honcho"]))


def save_settings(*, enabled: bool, base_url: str, workspace_id: str,
                  owner_peer_id: str = "owner", assistant_peer_id: str = "mark",
                  session_id: str = "shared", timeout_seconds: int = 4) -> None:

    if type(enabled) is not bool:
        raise ValueError("Opção de sincronização inválida.")
    try:
        timeout = int(timeout_seconds)
    except (TypeError, ValueError):
        raise ValueError("O tempo limite precisa ficar entre 1 e 10 segundos.") from None
    if not 1 <= timeout <= 10:
        raise ValueError("O tempo limite precisa ficar entre 1 e 10 segundos.")
    values = {
        "enabled": enabled,
        "share_conversations": enabled,
        "base_url": _safe_url(base_url),
        "workspace_id": _validate_id(workspace_id, "workspace"),
        "owner_peer_id": _validate_id(owner_peer_id, "peer"),
        "assistant_peer_id": _validate_id(assistant_peer_id, "peer"),
        "session_id": _validate_id(session_id, "sessão"),
        "timeout_seconds": timeout,
    }
    CONFIG_FILE.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if CONFIG_FILE.parent.is_symlink() or CONFIG_FILE.parent.stat().st_uid != os.getuid():
        raise PermissionError("A pasta de configuração do MARK não é privada deste usuário.")
    CONFIG_FILE.parent.chmod(0o700)
    if CONFIG_FILE.exists():
        info = CONFIG_FILE.lstat()
        if CONFIG_FILE.is_symlink() or not CONFIG_FILE.is_file() or info.st_uid != os.getuid():
            raise PermissionError("O arquivo de configuração do MARK não é seguro.")
        if info.st_mode & 0o077:
            raise PermissionError("O arquivo de configuração do MARK deve ter permissão privada (0600).")
        try:
            document = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ValueError("O arquivo local de configuração do MARK não contém JSON válido.") from None
    else:
        document = {}
    if not isinstance(document, dict):
        raise ValueError("O arquivo local de configuração do MARK precisa ser um objeto JSON.")
    document["honcho"] = values
    temporary = CONFIG_FILE.with_name(f".{CONFIG_FILE.name}.{os.getpid()}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(document, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, CONFIG_FILE)
        CONFIG_FILE.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)


def _keyring(args: list[str], secret: str | None = None) -> str:
    binary = shutil.which("secret-tool")
    if not binary:
        raise RuntimeError("O serviço de senhas do sistema (secret-tool) não está disponível.")
    result = subprocess.run([binary, *args], input=secret, text=True, capture_output=True,
                            timeout=8, check=False)
    if result.returncode:
        raise RuntimeError("O chaveiro do sistema não concluiu a operação.")
    return result.stdout.strip()


def set_api_key(secret: str) -> None:
    if not isinstance(secret, str) or not 8 <= len(secret.strip()) <= 2048 or "\n" in secret:
        raise ValueError("A chave do Honcho parece inválida.")
    _keyring(["store", "--label=MARK Honcho API key", "service", "mark", "provider", "honcho"], secret.strip())


def clear_api_key() -> None:
    binary = shutil.which("secret-tool")
    if binary:
        subprocess.run([binary, "clear", "service", "mark", "provider", "honcho"],
                       input="", text=True, capture_output=True, timeout=8, check=False)


def _api_key() -> str:
    value = os.environ.get("HONCHO_API_KEY", "").strip()
    if value:
        return value
    try:
        return _keyring(["lookup", "service", "mark", "provider", "honcho"])
    except (OSError, RuntimeError, subprocess.TimeoutExpired):
        return ""


def status() -> dict:
    cfg = settings()
    has_key = bool(os.environ.get("HONCHO_API_KEY", "").strip())
    if not has_key:
        try:
            has_key = bool(_api_key())
        except Exception:
            has_key = False
    active = bool(cfg.get("enabled") and cfg.get("share_conversations"))
    return {"enabled": active, "key_configured": has_key,
            "ready": active and (has_key or _is_local(cfg.get("base_url", ""))),
            "last_error": _last_error}


def _is_local(base_url: str) -> bool:
    try:
        return urlsplit(base_url).hostname in {"localhost", "127.0.0.1", "::1"}
    except ValueError:
        return False


def _client(cfg):

    from honcho import Honcho
    key = _api_key()
    if not key and not _is_local(cfg["base_url"]):
        raise RuntimeError("Configure a chave do Honcho na tela de Memória do app MARK.")
    return Honcho(api_key=key or None, workspace_id=cfg["workspace_id"],
                  base_url=cfg["base_url"], timeout=min(10, max(1, int(cfg["timeout_seconds"]))),
                  max_retries=0)


def _enabled_cfg(config=None):
    cfg = (config or load_config()).get("honcho", DEFAULTS["honcho"])
    if not (cfg.get("enabled") is True and cfg.get("share_conversations") is True):
        return None
    return cfg


def record_turn(user_text: str, assistant_text: str) -> bool:

    global _last_error
    cfg = _enabled_cfg()
    if cfg is None:
        return False
    if not isinstance(user_text, str) or not isinstance(assistant_text, str):
        return False
    user_text, assistant_text = user_text.strip()[:MAX_MESSAGE_CHARS], assistant_text.strip()[:MAX_MESSAGE_CHARS]
    if not user_text or not assistant_text:
        return False
    with _lock:
        client = None
        try:
            client = _client(cfg)
            owner = client.peer(cfg["owner_peer_id"])
            assistant = client.peer(cfg["assistant_peer_id"])
            session = client.session(cfg["session_id"])
            session.add_peers([owner, assistant])
            session.add_messages([owner.message(user_text), assistant.message(assistant_text)])
            _last_error = ""
            return True
        except Exception as exc:
            _last_error = type(exc).__name__
            return False
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass


def _sync_worker() -> None:
    while True:
        user_text, assistant_text = _pending_turns.get()
        try:
            record_turn(user_text, assistant_text)
        finally:
            _pending_turns.task_done()


def enqueue_turn(user_text: str, assistant_text: str) -> bool:

    global _worker_started, _last_error
    if _enabled_cfg() is None:
        return False
    if not isinstance(user_text, str) or not isinstance(assistant_text, str):
        return False
    with _lock:
        if not _worker_started:
            worker = threading.Thread(target=_sync_worker, name="mark-honcho-sync", daemon=True)
            worker.start()
            _worker_started = True
    try:
        _pending_turns.put_nowait((user_text[:MAX_MESSAGE_CHARS], assistant_text[:MAX_MESSAGE_CHARS]))
        return True
    except queue.Full:
        _last_error = "sync_queue_full"
        return False


def recall_context(query: str, config=None) -> str:

    global _last_error
    cfg = _enabled_cfg(config)
    if cfg is None or not isinstance(query, str) or not query.strip():
        return ""
    client = None
    try:
        client = _client(cfg)
        owner = client.peer(cfg["owner_peer_id"])
        result = owner.representation(search_query=query[:500])
        if isinstance(result, str):
            text = result
        elif isinstance(result, dict):
            text = result.get("content") or result.get("representation") or result.get("text") or ""
        else:
            text = getattr(result, "content", None) or getattr(result, "representation", None) or str(result)
        _last_error = ""
        return str(text).strip()[:MAX_CONTEXT_CHARS]
    except Exception as exc:
        _last_error = type(exc).__name__
        return ""
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass

from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import os
import ssl
import stat
import subprocess
import urllib.request
import zipfile
import defusedxml.ElementTree as ET
from http.client import HTTPSConnection
from pathlib import Path

from .url_security import validate_url_scheme

from PyQt6.QtGui import QIcon

from .attachment_limits import MAX_ATTACHMENTS, MAX_ATTACHMENT_BYTES, MAX_ATTACHMENTS_TOTAL, MAX_ATTACHMENT_TEXT


ROOT = Path(__file__).resolve().parents[2]
API = "http://127.0.0.1:8765"
TOKEN_FILE = Path.home() / ".config/mark/mobile.token"
TEXT_ATTACHMENT_EXTENSIONS = {".txt", ".md", ".csv", ".json", ".yaml", ".yml", ".xml", ".html", ".py", ".log", ".toml", ".ini", ".cfg"}
IMAGE_MIME_TYPES = {"image/jpeg", "image/png", "image/webp"}
ICON_DIR = ROOT / "assets/icons/desktop"


def _mark_icon(name: str) -> QIcon:

    return QIcon(str(ICON_DIR / f"{name}.svg"))


def _prepare_attachments(paths: list[Path]) -> list[dict]:

    if len(paths) > MAX_ATTACHMENTS:
        raise ValueError(f"Anexe no máximo {MAX_ATTACHMENTS} arquivos por mensagem.")
    total = 0
    text_total = 0
    prepared = []
    for path in paths:
        try:
            info = path.stat()
        except PermissionError:
            prepared.append({"name": path.name, "text": f"[Não foi possível ler o anexo {path.name}: permissão de acesso negada]", "error": True})
            continue
        except OSError as exc:
            prepared.append({"name": path.name, "text": f"[Não foi possível ler o anexo {path.name}: erro de acesso ao arquivo ({exc})]", "error": True})
            continue

        if not path.is_file():
            prepared.append({"name": path.name, "text": f"[Não foi possível ler o anexo {path.name}: não é um arquivo regular]", "error": True})
            continue

        if info.st_size > MAX_ATTACHMENT_BYTES:
            prepared.append({"name": path.name, "text": f"[Não foi possível ler o anexo {path.name}: excede o limite de 6 MB]", "error": True})
            continue

        total += info.st_size
        if total > MAX_ATTACHMENTS_TOTAL:
            prepared.append({"name": path.name, "text": f"[Não foi possível ler o anexo {path.name}: volume total de anexos excede 8 MB]", "error": True})
            continue

        suffix = path.suffix.lower()
        if suffix in {".jpg", ".jpeg", ".png", ".webp"}:
            mime = mimetypes.guess_type(path.name)[0]
            if mime not in IMAGE_MIME_TYPES:
                prepared.append({"name": path.name, "text": f"[Não foi possível ler o anexo {path.name}: formato de imagem não suportado ({mime})]", "error": True})
                continue
            if suffix == ".webp":
                try:
                    result = subprocess.run(
                        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path),
                         "-frames:v", "1", "-f", "image2pipe", "-vcodec", "mjpeg", "-q:v", "3", "-"],
                        capture_output=True, timeout=12, check=False,
                    )
                    if result.returncode or not result.stdout or len(result.stdout) > MAX_ATTACHMENT_BYTES:
                        prepared.append({"name": path.name, "text": f"[Não foi possível ler o anexo {path.name}: falha na conversão de imagem WebP (arquivo corrompido)]", "error": True})
                        continue
                    raw_image, mime = result.stdout, "image/jpeg"
                except (OSError, subprocess.TimeoutExpired):
                    prepared.append({"name": path.name, "text": f"[Não foi possível ler o anexo {path.name}: tempo limite ou erro ao converter imagem]", "error": True})
                    continue
            else:
                try:
                    raw_image = path.read_bytes()
                except Exception as exc:
                    prepared.append({"name": path.name, "text": f"[Não foi possível ler o anexo {path.name}: falha na leitura da imagem ({exc})]", "error": True})
                    continue

            encoded = base64.b64encode(raw_image).decode("ascii")
            prepared.append({"name": path.name, "mime": mime, "data": encoded})

        elif suffix in TEXT_ATTACHMENT_EXTENSIONS:
            text = None
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                try:
                    raw = path.read_bytes()
                    for enc in ("latin-1", "cp1252", "utf-8-sig"):
                        try:
                            text = raw.decode(enc)
                            break
                        except UnicodeDecodeError:
                            continue
                    if text is None:
                        text = raw.decode("utf-8", errors="replace")
                except Exception as exc:
                    prepared.append({"name": path.name, "text": f"[Não foi possível ler o anexo {path.name}: falha de decodificação de texto ({exc})]", "error": True})
                    continue
            except PermissionError:
                prepared.append({"name": path.name, "text": f"[Não foi possível ler o anexo {path.name}: permissão negada]", "error": True})
                continue
            except Exception as exc:
                prepared.append({"name": path.name, "text": f"[Não foi possível ler o anexo {path.name}: erro na leitura do arquivo ({exc})]", "error": True})
                continue

            text_total += len(text)
            if text_total > MAX_ATTACHMENT_TEXT:
                text = text[:MAX_ATTACHMENT_TEXT] + "\n[Conteúdo truncado por exceder o limite de caracteres]"
            prepared.append({"name": path.name, "text": text})

        elif suffix == ".pdf":
            try:
                result = subprocess.run(
                    ["pdftotext", "-enc", "UTF-8", str(path), "-"],
                    capture_output=True, text=True, timeout=15, check=False,
                )
                if result.returncode:
                    prepared.append({"name": path.name, "text": f"[Não foi possível ler o anexo {path.name}: PDF corrompido ou protegido]", "error": True})
                    continue
                text = result.stdout[:MAX_ATTACHMENT_TEXT + 1]
            except (OSError, subprocess.TimeoutExpired):
                prepared.append({"name": path.name, "text": f"[Não foi possível ler o anexo {path.name}: tempo limite ou erro no extrator de PDF]", "error": True})
                continue

            text_total += len(text)
            if text_total > MAX_ATTACHMENT_TEXT:
                text = text[:MAX_ATTACHMENT_TEXT] + "\n[Conteúdo truncado por exceder o limite de caracteres]"
            prepared.append({"name": path.name, "text": text})

        elif suffix == ".docx":
            try:
                with zipfile.ZipFile(path) as archive:
                    member = archive.getinfo("word/document.xml")
                    if member.file_size > 4 * 1024 * 1024:
                        prepared.append({"name": path.name, "text": f"[Não foi possível ler o anexo {path.name}: documento DOCX excede limite interno]", "error": True})
                        continue
                    document = archive.read("word/document.xml")
                root = ET.fromstring(document)
                paragraphs = []
                for paragraph in root.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p"):
                    line = "".join(node.text or "" for node in paragraph.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t"))
                    if line:
                        paragraphs.append(line)
                text = "\n".join(paragraphs)
            except (OSError, KeyError, zipfile.BadZipFile, ET.ParseError, ValueError):
                prepared.append({"name": path.name, "text": f"[Não foi possível ler o anexo {path.name}: documento DOCX corrompido ou inválido]", "error": True})
                continue

            text_total += len(text)
            if text_total > MAX_ATTACHMENT_TEXT:
                text = text[:MAX_ATTACHMENT_TEXT] + "\n[Conteúdo truncado por exceder o limite de caracteres]"
            prepared.append({"name": path.name, "text": text})

        else:
            prepared.append({"name": path.name, "text": f"[Não foi possível ler o anexo {path.name}: formato não suportado]", "error": True})
    return prepared


def ensure_config_dir_exists(config_dir: Path | str | None = None) -> Path:
    target_dir = Path(config_dir) if config_dir is not None else Path.home() / ".config/mark"
    os.makedirs(target_dir, exist_ok=True)
    try:
        os.chmod(target_dir, 0o700)
    except OSError:
        pass
    token_file = target_dir / "mobile.token"
    if not token_file.exists():
        try:
            create_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            create_flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
            fd = os.open(token_file, create_flags, 0o600)
            os.close(fd)
            try:
                os.chmod(token_file, 0o600)
            except OSError:
                pass
        except (FileExistsError, OSError):
            pass
    return target_dir


def _token() -> str:
    try:
        ensure_config_dir_exists(TOKEN_FILE.parent)
    except (OSError, PermissionError):
        pass

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(TOKEN_FILE, flags)
    except FileNotFoundError:
        return ""

    with os.fdopen(fd, "r", encoding="utf-8") as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise PermissionError("A chave local do MARK não está com permissões privadas.")
        value = source.read(256).strip()
    return value


def _request(path: str, payload: dict | None = None, timeout: int = 240):
    try:
        token = _token()
    except (FileNotFoundError, PermissionError):
        token = ""
    headers = {"X-Mark-Client": "desktop"}
    if token:
        headers["Authorization"] = "Bearer " + token
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(API + path, data=data, headers=headers)
    validate_url_scheme(request)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec: B310
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            raise RuntimeError("Limite de taxa excedido (HTTP 429). Aguarde alguns instantes antes de enviar nova mensagem.") from exc
        raise
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
        raise RuntimeError(f"Falha de conexão com a API local do MARK: {exc}") from exc


def _voice_request(pcm: bytes) -> tuple[bytes, bool]:
    if not 5760 <= len(pcm) <= 960_000 or len(pcm) % 2:
        raise ValueError("A fala precisa ter entre 0,18 e 30 segundos.")
    try:
        token = _token()
    except (FileNotFoundError, PermissionError):
        token = ""
    headers = {"X-Mark-Client": "desktop", "Content-Type": "application/octet-stream"}
    if token:
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request(
        API + "/v1/voice/turn", data=pcm, method="POST",
        headers=headers,
    )
    validate_url_scheme(request)
    with urllib.request.urlopen(request, timeout=240) as response:  # nosec: B310
        if response.status != 200 or response.headers.get_content_type() != "audio/wav":
            raise RuntimeError("O MARK não devolveu uma resposta de áudio válida.")
        data = response.read(8_000_001)
        if not 44 <= len(data) <= 8_000_000:
            raise RuntimeError("A resposta de áudio do MARK está vazia ou excede o limite.")
        return data, response.headers.get("X-Mark-Sleep") == "1"


def _service(action: str, unit: str) -> str:
    from . import desktop as desktop_module
    result = desktop_module.subprocess.run(
        ["systemctl", "--user", action, unit], capture_output=True, text=True,
        timeout=30, check=False,
    )
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(detail or f"Não foi possível {action} {unit}.")
    return result.stdout.strip()


def _mobile_pairing() -> dict:

    from . import desktop as desktop_module
    from .device_auth import DeviceRegistry
    try:
        token = _token()
    except (FileNotFoundError, PermissionError, OSError):
        token = ""
    if not token:
        return {
            "status": "inactive",
            "active": False,
            "token": None,
            "url": None,
            "code": None,
            "pin": None,
        }
    result = desktop_module.subprocess.run(["tailscale", "status", "--json"], capture_output=True, text=True, timeout=8, check=True)
    hostname = json.loads(result.stdout)["Self"]["DNSName"].rstrip(".")
    if not hostname.endswith(".ts.net"):
        raise RuntimeError("O nome Tailscale do computador não está disponível.")
    tls_context = ssl.create_default_context()
    tls_context.minimum_version = ssl.TLSVersion.TLSv1_3
    connection = HTTPSConnection(hostname, 443, timeout=8, context=tls_context)
    try:
        connection.connect()
        if connection.sock is None or connection.sock.version() != "TLSv1.3":
            raise RuntimeError("A conexão com o celular exige TLS 1.3.")
        pin = hashlib.sha256(connection.sock.getpeercert(binary_form=True)).hexdigest()
        connection.request("GET", "/v1/pair/status", headers={"X-Mark-Device": "probe"})
        response = connection.getresponse()
        if response.status != 200 or json.loads(response.read(1024)).get("status") != "unknown":
            raise RuntimeError("A API móvel não respondeu como esperado no HTTPS do Tailscale.")
    finally:
        connection.close()
    registry = DeviceRegistry()
    try:
        code = registry.open_enrollment()
    finally:
        registry.close()
    return {"url": "https://" + hostname, "code": code, "pin": pin}

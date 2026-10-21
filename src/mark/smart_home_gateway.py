"""Gateway Multi-Plataforma Criptografado de Automação Residencial (Fase 8).

Unifica a conectividade de casa inteligente do MARK suportando:
1. Home Assistant (via API Token local cifrado no cofre de credenciais 0600)
2. MQTT over TLS/SSL (v1.2 / v1.3 verificado) para dispositivos IoT locais (leitura/subscribe)
3. Webhooks Criptografados e Assinados com HMAC-SHA256 (com proteção anti-replay por timestamp/nonce)
4. Gateways Locais (Zigbee2MQTT, Matter/Thread Bridge, Tasmota/ESPHome local HTTP/HTTPS)

Opera estritamente em modo SOMENTE LEITURA por padrão para segurança operacional.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
import base64
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
import re
import secrets
import socket
import ssl
import sqlite3
import stat
import struct
import threading
import time
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

from .cognition.domains import normalize
from .cognition.storage import private_file
from .cognition.tools import BridgeCapabilityGuard, CapabilityToken, ToolContract, ToolRegistry
from .gods_eye_view import _schema_check
from .home_assistant import (
    BLOCKED_HA_DOMAINS,
    HomeAssistantClient,
    save_ha_token,
    validate_ha_base_url,
)
from .url_security import validate_url_scheme

log = logging.getLogger("mark.smart_home_gateway")

DEFAULT_VAULT_FILE = Path.home() / ".local/state/mark/smart_home_vault.enc"
DEFAULT_VAULT_KEY_FILE = Path.home() / ".local/state/mark/smart_home_vault.key"
DEFAULT_WEBHOOK_NONCES_DB = Path.home() / ".local/state/mark/webhook_nonces.sqlite3"
WEBHOOK_MAX_SKEW_SECONDS = 60
MAX_GATEWAY_PAYLOAD_BYTES = 256 * 1024

SUPPORTED_PLATFORMS = frozenset({
    "home_assistant",
    "mqtt_tls",
    "webhook_hmac",
    "local_gateway",
})

_SECRET_SCRUB_RE = re.compile(
    r"(?i)(bearer\s+[A-Za-z0-9._\-]+|eyJ[A-Za-z0-9._\-]{10,}|secret\s*[:=]\s*\S+|password\s*[:=]\s*\S+)"
)


class SmartHomeGatewayError(RuntimeError):
    """Erro operacional seguro do Gateway Multi-Plataforma."""


class SmartHomeSecurityError(PermissionError):
    """Violação de segurança: mutação bloqueada, TLS ausente, assinatura HMAC inválida ou replay."""


class SmartHomeAuthError(SmartHomeGatewayError):
    """Falha de autenticação ou cofre de credenciais com permissões inseguras."""


def scrub_gateway_secrets(text: str) -> str:
    if not isinstance(text, str) or not text:
        return ""
    return _SECRET_SCRUB_RE.sub("[REDACTED_CREDENTIAL]", text)


def _ensure_private_0600(path: Path) -> Path:
    if path.is_symlink():
        raise SmartHomeAuthError(f"Symlink não permitido para arquivo sensível: {path}")
    target = private_file(path)
    target.chmod(0o600)
    return target


def _verify_private_0600(path: Path) -> None:
    if path.is_symlink():
        raise SmartHomeAuthError("Symlink detectado em arquivo de credencial do Smart Home Gateway.")
    if not path.exists():
        raise SmartHomeAuthError("Arquivo de credencial do Smart Home Gateway não encontrado.")
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or (info.st_mode & 0o077):
        raise SmartHomeAuthError("Credenciais do Smart Home Gateway devem pertencer ao usuário com modo 0600.")


from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM, ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

DEFAULT_PBKDF2_ITERATIONS = 310_000
MIN_PBKDF2_ITERATIONS = 100_000
MAX_PBKDF2_ITERATIONS = 2_000_000
SUPPORTED_VAULT_CIPHERS = frozenset({"AES-256-GCM", "ChaCha20-Poly1305"})


def _atomic_write_private_0600(path: Path, text: str) -> Path:
    """Escreve de forma atômica um arquivo sensível garantindo modo 0600 estrito desde o descritor de arquivo."""
    if path.is_symlink():
        raise SmartHomeAuthError(f"Symlink não permitido para arquivo sensível: {path}")
    parent = path.parent
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if parent.is_symlink():
        raise SmartHomeAuthError(f"Diretório pai sensível não pode ser symlink: {parent}")
    tmp_path = parent / f".vault_tmp_{os.getpid()}_{secrets.token_hex(8)}"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(tmp_path, flags, 0o600)
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
        path.chmod(0o600)
    except Exception:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass
        raise
    _verify_private_0600(path)
    return path


def _derive_aead_and_mac_keys(
    master_secret: bytes,
    salt: bytes,
    iterations: int = DEFAULT_PBKDF2_ITERATIONS,
) -> tuple[bytes, bytes]:
    """Deriva 64 bytes (32B AEAD key + 32B HMAC key) via PBKDF2-HMAC-SHA256 padrão de mercado."""
    if len(salt) < 16:
        raise SmartHomeAuthError("Salt criptográfico inválido para derivação PBKDF2.")
    if not MIN_PBKDF2_ITERATIONS <= int(iterations) <= MAX_PBKDF2_ITERATIONS:
        raise SmartHomeAuthError("Número de iterações PBKDF2 fora dos limites de segurança permitidos.")
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=64,
        salt=salt,
        iterations=int(iterations),
    )
    derived = kdf.derive(master_secret)
    return derived[:32], derived[32:]


class EncryptedCredentialVault:
    """Cofre local autenticado usando primitivas criptográficas consolidadas (`cryptography` AES-256-GCM / ChaCha20Poly1305)
    com derivação de chaves PBKDF2-HMAC-SHA256, comparação em tempo constante (`hmac.compare_digest`),
    rotação segura de chaves e permissão estrita `0600`.
    """

    MAGIC = "MARK_VAULT_V2"

    def __init__(
        self,
        vault_path: str | Path = DEFAULT_VAULT_FILE,
        key_path: str | Path = DEFAULT_VAULT_KEY_FILE,
        *,
        cipher_algorithm: str | None = None,
        cipher_suite: str | None = None,
        kdf_iterations: int = DEFAULT_PBKDF2_ITERATIONS,
    ):
        algo = cipher_suite or cipher_algorithm or "AES-256-GCM"
        if algo not in SUPPORTED_VAULT_CIPHERS:
            raise ValueError(f"Algoritmo AEAD não suportado: {algo}")
        if not MIN_PBKDF2_ITERATIONS <= int(kdf_iterations) <= MAX_PBKDF2_ITERATIONS:
            raise ValueError("kdf_iterations deve estar entre 100.000 e 2.000.000.")
        self.vault_path = Path(vault_path)
        self.key_path = Path(key_path)
        self.cipher_algorithm = algo
        self.kdf_iterations = int(kdf_iterations)
        self._lock = threading.RLock()

    def _read_key_record(self) -> tuple[bytes, str]:
        _verify_private_0600(self.key_path)
        raw = self.key_path.read_text(encoding="utf-8").strip()
        if raw.startswith("{") and raw.endswith("}"):
            try:
                parsed = json.loads(raw)
                key_b64 = str(parsed["master_key"])
                key_id = str(parsed.get("key_id") or "")
                key = base64.urlsafe_b64decode(key_b64.encode("ascii"))
            except (KeyError, ValueError, json.JSONDecodeError) as exc:
                raise SmartHomeAuthError("Arquivo de chave mestra do cofre corrompido.") from exc
        else:
            try:
                key = base64.urlsafe_b64decode(raw.encode("ascii"))
            except ValueError as exc:
                raise SmartHomeAuthError("Codificação da chave mestra do cofre inválida.") from exc
            key_id = hashlib.sha256(key).hexdigest()[:16]
        if len(key) < 32:
            raise SmartHomeAuthError("Chave mestra do cofre inválida (mínimo 256 bits).")
        expected_kid = hashlib.sha256(key).hexdigest()[:16]
        if key_id and not hmac.compare_digest(key_id.encode("ascii"), expected_kid.encode("ascii")):
            raise SmartHomeAuthError("Fingerprint da chave mestra do cofre inconsistente.")
        return key, expected_kid

    def _write_key_record(self, key: bytes) -> str:
        if len(key) < 32:
            raise ValueError("A chave mestra deve ter no mínimo 32 bytes (256 bits).")
        key_id = hashlib.sha256(key).hexdigest()[:16]
        payload = {
            "version": 2,
            "key_id": key_id,
            "master_key": base64.urlsafe_b64encode(key).decode("ascii"),
            "created_at": int(time.time()),
        }
        _atomic_write_private_0600(self.key_path, json.dumps(payload, sort_keys=True) + "\n")
        return key_id

    def _load_or_create_key(self) -> tuple[bytes, str]:
        with self._lock:
            if self.key_path.exists() or self.key_path.is_symlink():
                return self._read_key_record()
            key = secrets.token_bytes(32)
            key_id = self._write_key_record(key)
            return key, key_id

    def _build_aead(self, aead_key: bytes, algorithm: str) -> AESGCM | ChaCha20Poly1305:
        if algorithm == "AES-256-GCM":
            return AESGCM(aead_key)
        if algorithm == "ChaCha20-Poly1305":
            return ChaCha20Poly1305(aead_key)
        raise SmartHomeAuthError(f"Primitiva AEAD desconhecida: {algorithm}")

    def _encrypt_payload(self, data: dict[str, Any], *, master_override: tuple[bytes, str] | None = None) -> dict[str, Any]:
        master_key, key_id = master_override if master_override is not None else self._load_or_create_key()
        salt = secrets.token_bytes(16)
        nonce = secrets.token_bytes(12)  # 96-bit NIST SP 800-38D nonce para AES-GCM / ChaCha20Poly1305
        aead_key, mac_key = _derive_aead_and_mac_keys(master_key, salt, self.kdf_iterations)
        aad = f"{self.MAGIC}|{self.cipher_algorithm}|PBKDF2-HMAC-SHA256|{self.kdf_iterations}|{key_id}".encode("ascii")
        plaintext = json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")

        cipher = self._build_aead(aead_key, self.cipher_algorithm)
        ciphertext = cipher.encrypt(nonce, plaintext, aad)
        mac_input = aad + b"|" + salt + nonce + ciphertext
        tag = hmac.new(mac_key, mac_input, hashlib.sha256).hexdigest()

        return {
            "magic": self.MAGIC,
            "cipher": self.cipher_algorithm,
            "kdf": "PBKDF2-HMAC-SHA256",
            "kdf_iterations": self.kdf_iterations,
            "iterations": self.kdf_iterations,
            "key_id": key_id,
            "salt": base64.urlsafe_b64encode(salt).decode("ascii"),
            "nonce": base64.urlsafe_b64encode(nonce).decode("ascii"),
            "ciphertext": base64.urlsafe_b64encode(ciphertext).decode("ascii"),
            "hmac_sha256": tag,
        }

    def _decrypt_payload(self, envelope: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(envelope, dict) or envelope.get("magic") != self.MAGIC:
            raise SmartHomeAuthError("Formato de cofre de credenciais inválido, legado não suportado ou corrompido.")
        algorithm = str(envelope.get("cipher") or "")
        kdf_name = str(envelope.get("kdf") or "")
        if algorithm not in SUPPORTED_VAULT_CIPHERS or kdf_name != "PBKDF2-HMAC-SHA256":
            raise SmartHomeAuthError("Parâmetros criptográficos do cofre inválidos ou não suportados.")

        master_key, expected_key_id = self._load_or_create_key()
        try:
            if "iterations" in envelope and "kdf_iterations" in envelope:
                if int(envelope["iterations"]) != int(envelope["kdf_iterations"]):
                    raise SmartHomeAuthError("Metadados de iterações PBKDF2 inconsistentes ou adulterados.")
            iterations = int(envelope.get("iterations") or envelope["kdf_iterations"])
            env_key_id = str(envelope.get("key_id") or "")
            salt = base64.urlsafe_b64decode(str(envelope["salt"]).encode("ascii"))
            nonce = base64.urlsafe_b64decode(str(envelope["nonce"]).encode("ascii"))
            ciphertext = base64.urlsafe_b64decode(str(envelope["ciphertext"]).encode("ascii"))
            provided_tag = str(envelope["hmac_sha256"])
        except SmartHomeAuthError:
            raise
        except (KeyError, ValueError, TypeError) as exc:
            raise SmartHomeAuthError("Envelope criptográfico do cofre corrompido.") from exc

        if len(nonce) != 12 or len(ciphertext) < 16:
            raise SmartHomeAuthError("Nonce ou ciphertext AEAD do cofre inválido.")
        if not hmac.compare_digest(env_key_id.encode("ascii"), expected_key_id.encode("ascii")):
            raise SmartHomeAuthError("Chave mestra não corresponde ao key_id do cofre.")

        aead_key, mac_key = _derive_aead_and_mac_keys(master_key, salt, iterations)
        aad = f"{self.MAGIC}|{algorithm}|PBKDF2-HMAC-SHA256|{iterations}|{env_key_id}".encode("ascii")
        mac_input = aad + b"|" + salt + nonce + ciphertext
        expected_tag = hmac.new(mac_key, mac_input, hashlib.sha256).hexdigest()

        if not hmac.compare_digest(provided_tag.encode("ascii"), expected_tag.encode("ascii")):
            raise SmartHomeAuthError("Assinatura HMAC-SHA256 do cofre de credenciais inválida (adulteração detectada).")

        cipher = self._build_aead(aead_key, algorithm)
        try:
            plaintext = cipher.decrypt(nonce, ciphertext, aad)
        except InvalidTag as exc:
            raise SmartHomeAuthError("Tag de autenticação AEAD (AES-GCM/ChaCha20Poly1305) inválida no cofre.") from exc

        try:
            decoded = json.loads(plaintext.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SmartHomeAuthError("Conteúdo decifrado do cofre não é JSON válido.") from exc
        if not isinstance(decoded, dict):
            raise SmartHomeAuthError("Estrutura interna do cofre inválida.")
        return decoded

    def read_all(self) -> dict[str, Any]:
        with self._lock:
            if not self.vault_path.exists() and not self.vault_path.is_symlink():
                return {}
            _verify_private_0600(self.vault_path)
            try:
                envelope = json.loads(self.vault_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise SmartHomeAuthError("Não foi possível ler o cofre de credenciais.") from exc
            return self._decrypt_payload(envelope)

    def store_credential(self, profile_id: str, credential_data: dict[str, Any]) -> Path:
        if not isinstance(profile_id, str) or not re.fullmatch(r"[a-zA-Z0-9_.-]{2,64}", profile_id):
            raise ValueError("Identificador de perfil de credencial inválido.")
        if not isinstance(credential_data, dict) or not credential_data:
            raise ValueError("Dados de credencial não podem ser vazios.")
        with self._lock:
            current = self.read_all() if (self.vault_path.exists() or self.vault_path.is_symlink()) else {}
            current[profile_id] = credential_data
            envelope = self._encrypt_payload(current)
            return _atomic_write_private_0600(
                self.vault_path,
                json.dumps(envelope, indent=2, sort_keys=True) + "\n",
            )

    def get_credential(self, profile_id: str) -> dict[str, Any]:
        with self._lock:
            records = self.read_all()
            if profile_id not in records:
                raise SmartHomeAuthError(f"Credencial '{profile_id}' não encontrada no cofre local.")
            return records[profile_id]

    def rotate_key(
        self,
        new_master_key: bytes | str | None = None,
        *,
        new_cipher_suite: str | None = None,
        new_iterations: int | None = None,
    ) -> dict[str, Any]:
        """Rotaciona de forma atômica a chave mestra do cofre, re-cifrando todas as credenciais com novo salt/nonce AEAD."""
        with self._lock:
            records = self.read_all() if (self.vault_path.exists() or self.vault_path.is_symlink()) else {}
            old_key_id = ""
            if self.key_path.exists():
                _, old_key_id = self._read_key_record()

            if new_cipher_suite is not None:
                if new_cipher_suite not in SUPPORTED_VAULT_CIPHERS:
                    raise ValueError(f"Algoritmo AEAD não suportado na rotação: {new_cipher_suite}")
                self.cipher_algorithm = new_cipher_suite
            if new_iterations is not None:
                if not MIN_PBKDF2_ITERATIONS <= int(new_iterations) <= MAX_PBKDF2_ITERATIONS:
                    raise ValueError("new_iterations fora dos limites seguros.")
                self.kdf_iterations = int(new_iterations)

            if new_master_key is None:
                candidate_key = secrets.token_bytes(32)
            elif isinstance(new_master_key, str):
                raw_pass = new_master_key.strip().encode("utf-8")
                if len(raw_pass) < 16:
                    raise ValueError("Passphrase para rotação deve possuir pelo menos 16 bytes.")
                candidate_key = hashlib.sha256(raw_pass).digest()
            elif isinstance(new_master_key, bytes):
                if len(new_master_key) < 32:
                    raise ValueError("Nova chave mestra deve possuir pelo menos 32 bytes (256 bits).")
                candidate_key = bytes(new_master_key)
            else:
                raise ValueError("Formato de chave mestra inválido para rotação.")

            new_key_id = self._write_key_record(candidate_key)
            envelope = self._encrypt_payload(records, master_override=(candidate_key, new_key_id))
            _atomic_write_private_0600(
                self.vault_path,
                json.dumps(envelope, indent=2, sort_keys=True) + "\n",
            )
            return {
                "rotated": True,
                "previous_key_id": old_key_id,
                "new_key_id": new_key_id,
                "cipher": self.cipher_algorithm,
                "kdf": "PBKDF2-HMAC-SHA256",
                "credentials_reencrypted": len(records),
            }


class SignedWebhookVerifier:
    """Autenticador e verificador de Webhooks assinados com HMAC-SHA256, checagem atômica de nonces via SQLite,
    tolerância temporal máxima (clock skew <= 60s), rate limiting e limite máximo de payload."""

    def __init__(
        self,
        secret: str | bytes,
        *,
        max_skew_seconds: int = WEBHOOK_MAX_SKEW_SECONDS,
        db_path: Path | str | None = None,
        rate_limit_rpm: int = 60,
        max_payload_bytes: int = MAX_GATEWAY_PAYLOAD_BYTES,
    ):
        if isinstance(secret, str):
            clean = secret.strip().encode("utf-8")
        elif isinstance(secret, bytes):
            clean = secret
        else:
            raise ValueError("Segredo HMAC inválido.")
        if len(clean) < 16:
            raise ValueError("Segredo HMAC-SHA256 deve possuir no mínimo 16 bytes.")
        self._secret = clean
        self.max_skew_seconds = max(5, int(max_skew_seconds))
        self.rate_limit_rpm = max(1, int(rate_limit_rpm))
        self.max_payload_bytes = max(1024, int(max_payload_bytes))
        self._lock = threading.RLock()
        self._request_timestamps: list[float] = []

        if db_path is not None:
            self._db_path = Path(db_path) if str(db_path) != ":memory:" else None
            db_conn_str = str(db_path)
        else:
            self._db_path = None
            db_conn_str = ":memory:"

        if self._db_path is not None:
            self._db_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            self._db = sqlite3.connect(db_conn_str, check_same_thread=False, timeout=5)
            self._db_path.chmod(0o600)
        else:
            self._db = sqlite3.connect(":memory:", check_same_thread=False, timeout=5)

        with self._lock, self._db:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("""
                CREATE TABLE IF NOT EXISTS webhook_nonces (
                    nonce TEXT PRIMARY KEY,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL
                )
            """)
            self._db.execute("CREATE INDEX IF NOT EXISTS idx_webhook_nonces_exp ON webhook_nonces(expires_at)")

    def sign_payload(
        self,
        payload: dict[str, Any] | str | bytes,
        *,
        timestamp: int | None = None,
        nonce: str | None = None,
    ) -> dict[str, str]:
        ts = int(timestamp if timestamp is not None else time.time())
        nce = nonce or secrets.token_hex(12)
        if isinstance(payload, dict):
            body_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        elif isinstance(payload, str):
            body_bytes = payload.encode("utf-8")
        else:
            body_bytes = bytes(payload)
        signing_input = f"{ts}.{nce}.".encode("ascii") + body_bytes
        digest = hmac.new(self._secret, signing_input, hashlib.sha256).hexdigest()
        return {
            "X-MARK-Timestamp": str(ts),
            "X-MARK-Nonce": nce,
            "X-MARK-Signature-256": f"sha256={digest}",
        }

    def verify_and_parse(
        self,
        payload: str | bytes,
        headers: dict[str, str],
        *,
        now: float | None = None,
    ) -> dict[str, Any]:
        norm_headers = {k.lower(): v for k, v in (headers or {}).items()}
        ts_str = norm_headers.get("x-mark-timestamp", "")
        nonce = norm_headers.get("x-mark-nonce", "")
        sig_header = norm_headers.get("x-mark-signature-256", "")

        if not ts_str or not nonce or not sig_header.startswith("sha256="):
            raise SmartHomeSecurityError("Cabeçalhos HMAC-SHA256 ausentes ou malformados no Webhook.")
        try:
            ts = int(ts_str)
        except ValueError as exc:
            raise SmartHomeSecurityError("Timestamp inválido no cabeçalho do Webhook.") from exc

        ref_now = float(now if now is not None else time.time())
        if abs(ref_now - ts) > self.max_skew_seconds:
            raise SmartHomeSecurityError(
                f"Webhook rejeitado: timestamp fora da janela anti-replay permitida (clock skew > {self.max_skew_seconds}s)."
            )

        body_bytes = payload.encode("utf-8") if isinstance(payload, str) else bytes(payload)
        if len(body_bytes) > self.max_payload_bytes:
            raise SmartHomeSecurityError(
                f"Payload do Webhook excede o limite de tamanho permitido ({len(body_bytes)} > {self.max_payload_bytes} bytes)."
            )

        signing_input = f"{ts}.{nonce}.".encode("ascii") + body_bytes
        expected_sig = "sha256=" + hmac.new(self._secret, signing_input, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig_header, expected_sig):
            raise SmartHomeSecurityError("Assinatura HMAC-SHA256 inválida no Webhook.")

        with self._lock, self._db:
            # 1. Rate limiting check (requests per minute)
            window_start = ref_now - 60.0
            self._request_timestamps = [t for t in self._request_timestamps if t >= window_start]
            if len(self._request_timestamps) >= self.rate_limit_rpm:
                raise SmartHomeSecurityError(
                    f"Webhook rejeitado: limite de frequência excedido ({self.rate_limit_rpm} req/min)."
                )
            self._request_timestamps.append(ref_now)

            # 2. Atômico: purgar nonces expirados e tentar inserir novo nonce
            self._db.execute("DELETE FROM webhook_nonces WHERE expires_at < ?", (ref_now,))
            try:
                self._db.execute(
                    "INSERT INTO webhook_nonces (nonce, created_at, expires_at) VALUES (?, ?, ?)",
                    (nonce, ref_now, ref_now + self.max_skew_seconds),
                )
            except sqlite3.IntegrityError:
                raise SmartHomeSecurityError(
                    "Webhook rejeitado: nonce duplicado detectado (ataque de replay bloqueado)."
                )

        try:
            parsed = json.loads(body_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SmartHomeGatewayError("Corpo do Webhook autenticado não é JSON válido.") from exc
        if not isinstance(parsed, dict):
            raise SmartHomeGatewayError("Payload do Webhook deve ser um objeto JSON.")
        return parsed


def _encode_mqtt_utf8(value: str) -> bytes:
    raw = value.encode("utf-8")
    return struct.pack("!H", len(raw)) + raw


def _encode_mqtt_remaining_length(length: int) -> bytes:
    out = bytearray()
    while True:
        encoded = length % 128
        length //= 128
        if length > 0:
            encoded |= 0x80
        out.append(encoded)
        if length == 0:
            break
    return bytes(out)


class MQTTSecureClient:
    """Cliente MQTT v3.1.1 estritamente sobre TLS/SSL (somente leitura / subscribe)."""

    READ_ONLY = True

    def __init__(
        self,
        host: str,
        port: int = 8883,
        *,
        use_tls: bool = True,
        username: str = "",
        password: str = "",
        ca_file: str | Path | None = None,
        timeout: float = 5.0,
    ):
        if not use_tls:
            raise SmartHomeSecurityError("Conexões MQTT sem TLS/SSL são estritamente proibidas no MARK.")
        if not isinstance(host, str) or not host.strip():
            raise ValueError("Host do broker MQTT inválido.")
        if not 1 <= int(port) <= 65535:
            raise ValueError("Porta MQTT inválida.")
        self.host = host.strip()
        self.port = int(port)
        self.username = username
        self.password = password
        self.ca_file = Path(ca_file) if ca_file else None
        self.timeout = max(1.0, min(float(timeout), 15.0))

    def create_tls_context(self) -> ssl.SSLContext:
        ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.check_hostname = True
        ctx.verify_mode = ssl.CERT_REQUIRED
        if self.ca_file is not None:
            ctx.load_verify_locations(cafile=str(self.ca_file))
        return ctx

    def publish(self, *args, **kwargs) -> None:
        """Bloqueio estrito contra publicação MQTT no modo somente leitura."""
        raise SmartHomeSecurityError("Publicação MQTT (publish) bloqueada: cliente opera em modo SOMENTE LEITURA.")

    @staticmethod
    def validate_topic_filter(topic: str) -> str:
        if not isinstance(topic, str) or not topic.strip():
            raise ValueError("Tópico MQTT vazio.")
        clean = topic.strip()
        if len(clean) > 256 or "\x00" in clean:
            raise ValueError("Tópico MQTT inválido.")
        lowered = clean.lower()
        if any(bad in lowered for bad in ("/set", "/cmd", "/command", "/execute", "/write", "cmnd/")):
            raise SmartHomeSecurityError(f"Tópico de comando/mutação MQTT bloqueado: {clean}")
        return clean

    def build_connect_packet(self, client_id: str = "mark-readonly") -> bytes:
        var_header = _encode_mqtt_utf8("MQTT") + bytes([4])  # Protocol level 4 (MQTT 3.1.1)
        flags = 0x02  # Clean session
        payload = _encode_mqtt_utf8(client_id)
        if self.username:
            flags |= 0x80
            payload += _encode_mqtt_utf8(self.username)
            if self.password:
                flags |= 0x40
                payload += _encode_mqtt_utf8(self.password)
        var_header += bytes([flags]) + struct.pack("!H", 30)
        remaining = len(var_header) + len(payload)
        return bytes([0x10]) + _encode_mqtt_remaining_length(remaining) + var_header + payload

    def build_subscribe_packet(self, topic: str, packet_id: int = 1) -> bytes:
        clean_topic = self.validate_topic_filter(topic)
        var_header = struct.pack("!H", packet_id)
        payload = _encode_mqtt_utf8(clean_topic) + bytes([0x00])  # QoS 0
        remaining = len(var_header) + len(payload)
        return bytes([0x82]) + _encode_mqtt_remaining_length(remaining) + var_header + payload

    def read_retained_topic(self, topic: str) -> dict[str, Any]:
        """Conecta ao broker via TLS, assina o tópico de telemetria e lê o estado retido."""
        clean_topic = self.validate_topic_filter(topic)
        ctx = self.create_tls_context()
        try:
            with socket.create_connection((self.host, self.port), timeout=self.timeout) as raw_sock:
                with ctx.wrap_socket(raw_sock, server_hostname=self.host) as tls_sock:
                    tls_sock.sendall(self.build_connect_packet())
                    connack = tls_sock.recv(4)
                    if len(connack) < 4 or connack[0] != 0x20 or connack[3] != 0x00:
                        raise SmartHomeAuthError("Broker MQTT recusou a conexão ou credenciais (CONNACK inválido).")
                    tls_sock.sendall(self.build_subscribe_packet(clean_topic, packet_id=1))
                    packet = tls_sock.recv(4096)
                    # Procura pacote PUBLISH (0x30..0x3F) na resposta ou leitura seguinte
                    if packet and (packet[0] & 0xF0) == 0x90 and len(packet) <= 5:
                        packet = tls_sock.recv(4096)
                    try:
                        tls_sock.sendall(bytes([0xE0, 0x00]))  # DISCONNECT
                    except OSError:
                        pass
        except SmartHomeAuthError:
            raise
        except (ssl.SSLCertVerificationError, ssl.SSLError) as exc:
            raise SmartHomeSecurityError(
                f"Certificado SSL/TLS do broker MQTT rejeitado (autoridade não confiável ou hostname divergente): {scrub_gateway_secrets(str(exc))}"
            ) from exc
        except (TimeoutError, socket.timeout) as exc:
            raise TimeoutError(f"Timeout ao ler tópico MQTT TLS '{clean_topic}'.") from exc
        except OSError as exc:
            raise SmartHomeGatewayError(
                f"Erro de conexão TLS com broker MQTT ({self.host}:{self.port}): {scrub_gateway_secrets(str(exc))}"
            ) from exc

        payload_text = ""
        if packet and (packet[0] & 0xF0) == 0x30 and len(packet) >= 4:
            idx = 1
            multiplier = 1
            rem_len = 0
            while idx < len(packet):
                encoded_byte = packet[idx]
                rem_len += (encoded_byte & 127) * multiplier
                idx += 1
                if (encoded_byte & 128) == 0:
                    break
                multiplier *= 128
            if idx + 2 <= len(packet):
                topic_len = struct.unpack("!H", packet[idx : idx + 2])[0]
                body_start = idx + 2 + topic_len
                payload_bytes = packet[body_start : idx + rem_len]
                payload_text = payload_bytes.decode("utf-8", errors="replace").strip()

        parsed_value: Any = payload_text
        if payload_text.startswith("{") and payload_text.endswith("}"):
            try:
                parsed_value = json.loads(payload_text)
            except json.JSONDecodeError:
                parsed_value = payload_text

        return {
            "platform": "mqtt_tls",
            "topic": clean_topic,
            "tls_verified": True,
            "read_only": True,
            "payload": parsed_value,
        }


@dataclass(frozen=True)
class UnifiedDeviceState:
    platform: str
    device_id: str
    name: str
    domain: str
    state: str
    unit: str = ""
    room: str = ""
    available: bool = True
    stale: bool = False
    encrypted_transport: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def compact_line(self) -> str:
        flags = []
        if not self.available:
            flags.append("INDISPONÍVEL")
        elif self.stale:
            flags.append("DESATUALIZADO")
        room_prefix = f"[{self.room}] " if self.room else ""
        flag_suffix = f" ({', '.join(flags)})" if flags else ""
        val = f"{self.state} {self.unit}".strip() if self.available else self.state
        return f"- ({self.platform}) {room_prefix}{self.name} [`{self.device_id}`]: {val}{flag_suffix}"


class SmartHomeGateway:
    """Gateway Unificado Multi-Plataforma (Home Assistant + MQTT TLS + Webhooks HMAC + Gateways Locais)."""

    READ_ONLY_DEFAULT = True

    def __init__(
        self,
        *,
        vault: EncryptedCredentialVault | None = None,
        ha_client: HomeAssistantClient | None = None,
        mqtt_client: MQTTSecureClient | None = None,
        webhook_verifier: SignedWebhookVerifier | None = None,
        read_only: bool = True,
    ):
        if not read_only:
            raise SmartHomeSecurityError(
                "O SmartHomeGateway exige modo somente leitura (read_only=True) por política de segurança."
            )
        self.read_only = True
        self.vault = vault or EncryptedCredentialVault()
        self.ha_client = ha_client
        self.mqtt_client = mqtt_client
        self.webhook_verifier = webhook_verifier
        self._webhook_telemetry: dict[str, UnifiedDeviceState] = {}
        self._local_gateways: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()

    def execute_command(self, *args, **kwargs) -> None:
        """Bloqueia categoricamente qualquer comando de escrita/atuação sobre dispositivos."""
        raise SmartHomeSecurityError(
            "Ação negada: o SmartHomeGateway do MARK opera estritamente em modo SOMENTE LEITURA."
        )

    def configure_home_assistant(
        self,
        base_url: str,
        token: str,
        *,
        token_file: str | Path | None = None,
        timeout: float = 5.0,
    ) -> HomeAssistantClient:
        clean_url = validate_ha_base_url(base_url)
        self.vault.store_credential("home_assistant", {"base_url": clean_url, "token": token.strip()})
        target_token_file = Path(token_file) if token_file else (self.vault.vault_path.parent / "home_assistant.token")
        save_ha_token(token, target_token_file)
        with self._lock:
            self.ha_client = HomeAssistantClient(clean_url, token_path=target_token_file, timeout=timeout)
            return self.ha_client

    def configure_mqtt_tls(
        self,
        host: str,
        port: int = 8883,
        *,
        username: str = "",
        password: str = "",
        ca_file: str | Path | None = None,
        timeout: float = 5.0,
    ) -> MQTTSecureClient:
        self.vault.store_credential(
            "mqtt_tls",
            {"host": host, "port": int(port), "username": username, "password": password},
        )
        with self._lock:
            self.mqtt_client = MQTTSecureClient(
                host,
                port=port,
                use_tls=True,
                username=username,
                password=password,
                ca_file=ca_file,
                timeout=timeout,
            )
            return self.mqtt_client

    def configure_webhook_secret(self, secret: str, *, max_skew_seconds: int = WEBHOOK_MAX_SKEW_SECONDS) -> SignedWebhookVerifier:
        self.vault.store_credential("webhook_hmac", {"secret": secret})
        with self._lock:
            self.webhook_verifier = SignedWebhookVerifier(secret, max_skew_seconds=max_skew_seconds)
            return self.webhook_verifier

    def register_local_gateway(
        self,
        gateway_id: str,
        base_url: str,
        *,
        protocol: str = "zigbee2mqtt",
        token: str = "",
    ) -> None:
        if not re.fullmatch(r"[a-zA-Z0-9_.-]{2,48}", gateway_id):
            raise ValueError("Identificador de gateway local inválido.")
        clean_url = validate_ha_base_url(base_url)
        if token:
            self.vault.store_credential(f"gateway_{gateway_id}", {"base_url": clean_url, "token": token, "protocol": protocol})
        with self._lock:
            self._local_gateways[gateway_id] = {
                "gateway_id": gateway_id,
                "base_url": clean_url,
                "protocol": protocol,
                "has_token": bool(token),
            }

    def ingest_signed_webhook_event(
        self,
        payload: str | bytes,
        headers: dict[str, str],
        *,
        now: float | None = None,
    ) -> UnifiedDeviceState:
        """Valida um evento de telemetria via Webhook HMAC-SHA256 e armazena o estado em cache de leitura."""
        with self._lock:
            verifier = self.webhook_verifier
        if verifier is None:
            raise SmartHomeAuthError("Verificador de Webhook HMAC-SHA256 não está configurado.")
        data = verifier.verify_and_parse(payload, headers, now=now)
        action = str(data.get("action") or "state_report").lower()
        if action not in {"state_report", "telemetry", "sensor_update"}:
            raise SmartHomeSecurityError(f"Ação de webhook não permitida no modo somente leitura: {action}")

        device_id = str(data.get("device_id") or data.get("entity_id") or "").strip().lower()
        if not device_id or not re.fullmatch(r"[a-z0-9_.-]{2,128}", device_id):
            raise SmartHomeGatewayError("device_id ausente ou inválido no payload do Webhook.")
        domain = str(data.get("domain") or (device_id.split(".", 1)[0] if "." in device_id else "sensor")).lower()
        if domain in BLOCKED_HA_DOMAINS:
            raise SmartHomeSecurityError(f"Domínio bloqueado em evento de Webhook: {domain}")

        raw_state = str(data.get("state") if data.get("state") is not None else "unknown").strip()
        available = raw_state.lower() not in {"unavailable", "unknown", "offline", ""}
        entry = UnifiedDeviceState(
            platform="webhook_hmac",
            device_id=device_id,
            name=str(data.get("name") or device_id).strip()[:120],
            domain=domain,
            state=raw_state[:120],
            unit=str(data.get("unit") or "").strip()[:24],
            room=normalize(str(data.get("room") or "")).strip()[:48],
            available=available,
            stale=bool(data.get("stale", False)),
            encrypted_transport=True,
        )
        with self._lock:
            self._webhook_telemetry[device_id] = entry
        return entry

    def query_local_gateway(self, gateway_id: str, *, timeout: float = 5.0) -> list[UnifiedDeviceState]:
        """Consulta um gateway local registrado (somente GET em /api/devices ou /api/states)."""
        with self._lock:
            if gateway_id not in self._local_gateways:
                raise SmartHomeGatewayError(f"Gateway local '{gateway_id}' não registrado.")
            info = dict(self._local_gateways[gateway_id])
        headers = {"Accept": "application/json", "User-Agent": "MARK-SmartHomeGateway-ReadOnly/1.0"}
        if info["has_token"]:
            cred = self.vault.get_credential(f"gateway_{gateway_id}")
            headers["Authorization"] = f"Bearer {cred['token']}"
        req = urllib.request.Request(info["base_url"] + "/api/devices", method="GET", headers=headers)
        validate_url_scheme(req)
        try:
            with urllib.request.urlopen(req, timeout=min(float(timeout), 10.0)) as resp:  # nosec: B310
                raw = resp.read(MAX_GATEWAY_PAYLOAD_BYTES + 1)
        except Exception as exc:
            raise SmartHomeGatewayError(
                f"Falha ao consultar gateway local '{gateway_id}': {scrub_gateway_secrets(str(exc))}"
            ) from None
        if len(raw) > MAX_GATEWAY_PAYLOAD_BYTES:
            raise SmartHomeGatewayError("Resposta do gateway local excedeu 256 KB.")
        items = json.loads(raw.decode("utf-8"))
        if not isinstance(items, list):
            raise SmartHomeGatewayError("Resposta inválida do gateway local.")
        results: list[UnifiedDeviceState] = []
        for item in items[:50]:
            if not isinstance(item, dict):
                continue
            dev_id = str(item.get("device_id") or item.get("ieee_address") or item.get("id") or "").strip()
            if not dev_id:
                continue
            st = str(item.get("state") or "online").strip()
            results.append(
                UnifiedDeviceState(
                    platform=f"local_gateway:{info['protocol']}",
                    device_id=dev_id,
                    name=str(item.get("friendly_name") or item.get("name") or dev_id)[:120],
                    domain=str(item.get("domain") or "sensor"),
                    state=st,
                    unit=str(item.get("unit") or ""),
                    room=normalize(str(item.get("room") or "")).strip(),
                    available=st.lower() not in {"unavailable", "offline", "unknown"},
                    stale=False,
                    encrypted_transport=info["base_url"].startswith("https://") or info["has_token"],
                )
            )
        return results

    def query_unified_status(
        self,
        *,
        room: str | None = None,
        domain: str | None = None,
        platform: str | None = None,
        include_unavailable: bool = True,
        limit: int = 30,
    ) -> dict[str, Any]:
        """Agrega estados de todas as plataformas configuradas com filtragem sob demanda."""
        if platform and platform not in SUPPORTED_PLATFORMS:
            raise ValueError(f"Plataforma desconhecida: {platform}")
        norm_room = normalize(room).strip() if room else ""
        norm_domain = normalize(domain).strip() if domain else ""

        collected: list[UnifiedDeviceState] = []
        errors: list[str] = []

        with self._lock:
            ha_client = self.ha_client
            webhook_items = list(self._webhook_telemetry.values())
            gateway_ids = list(self._local_gateways)

        if ha_client is not None and (not platform or platform == "home_assistant"):
            try:
                ha_res = ha_client.query_entities(
                    room=room,
                    domain=domain,
                    include_unavailable=include_unavailable,
                    limit=limit,
                )
                for ent in ha_res.get("entities", []):
                    collected.append(
                        UnifiedDeviceState(
                            platform="home_assistant",
                            device_id=ent["entity_id"],
                            name=ent["friendly_name"],
                            domain=ent["domain"],
                            state=ent["state"],
                            unit=ent.get("unit", ""),
                            room=ent.get("room", ""),
                            available=ent.get("available", True),
                            stale=ent.get("stale", False),
                            encrypted_transport=True,
                        )
                    )
            except Exception as exc:
                errors.append(f"home_assistant: {scrub_gateway_secrets(str(exc))}")

        if not platform or platform == "webhook_hmac":
            for item in webhook_items:
                if norm_room and item.room != norm_room:
                    continue
                if norm_domain and item.domain != norm_domain:
                    continue
                if not include_unavailable and not item.available:
                    continue
                collected.append(item)

        if not platform or platform == "local_gateway":
            for gw_id in gateway_ids:
                try:
                    for item in self.query_local_gateway(gw_id):
                        if norm_room and item.room != norm_room:
                            continue
                        if norm_domain and item.domain != norm_domain:
                            continue
                        if not include_unavailable and not item.available:
                            continue
                        collected.append(item)
                except Exception as exc:
                    errors.append(f"local_gateway({gw_id}): {scrub_gateway_secrets(str(exc))}")

        selected = collected[: max(1, min(int(limit), 100))]
        lines = [d.compact_line() for d in selected]
        summary = (
            f"Smart Home Gateway (somente leitura · {len(selected)} dispositivo(s)):\n" + "\n".join(lines)
            if lines
            else "Nenhum dispositivo encontrado nas plataformas conectadas para os filtros informados."
        )
        return {
            "count": len(selected),
            "read_only": True,
            "devices": [d.to_dict() for d in selected],
            "errors": errors,
            "summary": summary,
        }


SMART_HOME_MCP_SCHEMAS: dict[str, dict[str, Any]] = {
    "smart_home_query_status": {
        "description": "Consulta de forma unificada e somente leitura os dispositivos de casa inteligente (Home Assistant, Webhooks HMAC e Gateways Locais).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "room": {"type": "string", "minLength": 1, "maxLength": 64},
                "domain": {"type": "string", "minLength": 1, "maxLength": 64},
                "platform": {
                    "type": "string",
                    "enum": ["home_assistant", "mqtt_tls", "webhook_hmac", "local_gateway"],
                },
                "include_unavailable": {"type": "boolean"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
            "additionalProperties": False,
        },
    },
    "smart_home_read_mqtt_tls": {
        "description": "Lê o estado retido de um tópico de telemetria IoT via MQTT sobre TLS/SSL (somente leitura).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "topic": {"type": "string", "minLength": 1, "maxLength": 256},
            },
            "required": ["topic"],
            "additionalProperties": False,
        },
    },
}


SMART_HOME_TOOL_PERMISSIONS: dict[str, tuple[str, ...]] = {
    "smart_home_query_status": ("home_assistant.read",),
    "smart_home_read_mqtt_tls": ("home_assistant.read", "mqtt.read"),
}


class SmartHomeMCPBridge:
    """Ponte MCP para o SmartHomeGateway."""

    def __init__(
        self,
        gateway: SmartHomeGateway | None = None,
        *,
        require_explicit_capabilities: bool = False,
    ):
        self.gateway = gateway or SmartHomeGateway()
        self.capability_guard = BridgeCapabilityGuard(
            "smart_home",
            SMART_HOME_TOOL_PERMISSIONS,
            strict_mode=require_explicit_capabilities,
        )

    def issue_mission_capability(
        self,
        mission_id: str,
        *,
        allowed_tools: list[str] | set[str] | tuple[str, ...],
        permissions: list[str] | set[str] | tuple[str, ...] | None = None,
        ttl_seconds: float = 900.0,
        allow_privileged: bool = False,
    ) -> CapabilityToken:
        return self.capability_guard.issue_mission_capability(
            mission_id,
            allowed_tools=allowed_tools,
            permissions=permissions,
            ttl_seconds=ttl_seconds,
            allow_privileged=allow_privileged,
        )

    def get_mcp_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": name,
                "description": spec["description"],
                "inputSchema": spec["inputSchema"],
            }
            for name, spec in sorted(SMART_HOME_MCP_SCHEMAS.items())
        ]

    def validate_call(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        grants: frozenset[str] | set[str] | CapabilityToken | None = None,
        mission_id: str | None = None,
    ) -> dict[str, Any]:
        if name not in SMART_HOME_MCP_SCHEMAS:
            raise SmartHomeSecurityError(f"Ferramenta MCP Smart Home não autorizada: {name}")
        if not isinstance(arguments, dict):
            raise ValueError("Argumentos devem ser um objeto JSON.")
        self.capability_guard.check(name, arguments, grants=grants, mission_id=mission_id)
        _schema_check(SMART_HOME_MCP_SCHEMAS[name]["inputSchema"], arguments)
        return arguments

    def execute_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        grants: frozenset[str] | set[str] | CapabilityToken | None = None,
        mission_id: str | None = None,
    ) -> dict[str, Any]:
        args = self.validate_call(name, arguments, grants=grants, mission_id=mission_id)
        if name == "smart_home_query_status":
            res = self.gateway.query_unified_status(
                room=args.get("room"),
                domain=args.get("domain"),
                platform=args.get("platform"),
                include_unavailable=bool(args.get("include_unavailable", True)),
                limit=int(args.get("limit", 30)),
            )
            return {"tool": name, "status": "ok", "result": res}
        if name == "smart_home_read_mqtt_tls":
            if self.gateway.mqtt_client is None:
                raise SmartHomeAuthError("Cliente MQTT TLS não está configurado neste gateway.")
            res = self.gateway.mqtt_client.read_retained_topic(args["topic"])
            return {"tool": name, "status": "ok", "result": res}
        raise SmartHomeSecurityError(f"Ferramenta não suportada: {name}")

    def register_in_cognition_registry(self, registry: ToolRegistry | None = None) -> ToolRegistry:
        target = registry if registry is not None else ToolRegistry()
        output_schema = {
            "type": "object",
            "properties": {
                "tool": {"type": "string", "maxLength": 128},
                "status": {"type": "string", "maxLength": 64},
                "result": {"type": "object"},
            },
            "required": ["tool", "status", "result"],
            "additionalProperties": False,
        }
        for tool in self.get_mcp_tools():
            t_name = tool["name"]

            def _make_runner(tool_name: str):
                def _runner(**kwargs):
                    return self.execute_tool(tool_name, kwargs)
                return _runner

            contract = ToolContract(
                name=f"smart_home.{t_name}",
                description=tool["description"],
                input_schema=tool["inputSchema"],
                output_schema=output_schema,
                risk_level="READ_ONLY",
                timeout=5.0,
                permissions=("home_assistant.read",),
            )
            target.register(contract, _make_runner(t_name))
        return target

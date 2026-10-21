




from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from typing import Callable

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

DEFAULT_DB = Path.home() / ".config/mark/mobile-devices.db"
DEFAULT_SESSION_LIFETIME = 120
CLOCK_SKEW = 60
_SAFE_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_B64_DATA = re.compile(r"[A-Za-z0-9_-]+\Z")

CRITICAL_ACTIONS = frozenset({
    "clear_memory",
    "shutdown",
    "lockdown_enable",
    "lockdown_disable",
    "device_revoke",
    "device_approve",
    "system_config",
    "backup_restore",
    "mcp_privileged",
})

LLM_BLOCKED_SOURCES = frozenset({
    "llm",
    "assistant",
    "model",
    "synthetic",
    "ai_agent",
    "autonomous_loop",
})


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def unb64(value: str) -> bytes:
    if not isinstance(value, str) or not value or len(value) > 4096 or not _B64_DATA.fullmatch(value):
        raise ValueError("codificação inválida")
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def signed_message(method: str, path: str, body: bytes, timestamp: str, nonce: str) -> bytes:
    return "\n".join(("MARK-MOBILE-V1", method, path, hashlib.sha256(body).hexdigest(), timestamp, nonce)).encode("ascii")


def session_message(method: str, path: str, body: bytes, timestamp: str, nonce: str,
                    request_id: str, token: str) -> bytes:

    return "\n".join(("MARK-MOBILE-V2", method, path, hashlib.sha256(body).hexdigest(),
                      timestamp, nonce, request_id, hashlib.sha256(token.encode("ascii")).hexdigest())).encode("ascii")


class SessionRegistry:

    def __init__(self, devices: "DeviceRegistry", lifetime: int = DEFAULT_SESSION_LIFETIME):
        self.devices = devices
        self.lifetime = lifetime
        self.lock = threading.RLock()
        self.sessions: dict[str, tuple[str, float, int]] = {}
        if hasattr(self.devices, "register_revocation_listener"):
            self.devices.register_revocation_listener(self.revoke_device_sessions)

    def issue(self, device_id: str) -> tuple[str, int]:
        token = secrets.token_urlsafe(32)
        now = time.time()

        with self.devices.lock, self.lock:
            active, generation = self.devices.session_state(device_id)
            if not active:
                raise ValueError("aparelho não autorizado")
            self.sessions = {digest: item for digest, item in self.sessions.items()
                             if item[1] > now and item[2] == generation}

            owned = [digest for digest, item in self.sessions.items() if item[0] == device_id]
            for digest in owned[:-7]:
                del self.sessions[digest]
            if len(self.sessions) >= 256:
                raise ValueError("limite de sessões atingido")
            self.sessions[hashlib.sha256(token.encode("ascii")).hexdigest()] = (
                device_id, now + self.lifetime, generation)
        return token, int(now + self.lifetime)

    def valid(self, device_id: str, token: str) -> bool:
        if not isinstance(token, str) or not token or len(token) > 128:
            return False
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with self.devices.lock, self.lock:
            active, generation = self.devices.session_state(device_id)
            if not active:
                return False
            row = self.sessions.get(digest)
            return bool(row and row[0] == device_id and row[1] > time.time()
                        and row[2] == generation)

    def renew(self, device_id: str, current_token: str) -> tuple[str, int]:
        """Renova uma sessão ativa com emissão de novo token e invalidação imediata do anterior."""
        with self.lock:
            if not self.valid(device_id, current_token):
                raise ValueError("sessão inválida ou expirada para renovação")
            old_digest = hashlib.sha256(current_token.encode("utf-8")).hexdigest()
            self.sessions.pop(old_digest, None)
            return self.issue(device_id)

    def revoke_device_sessions(self, device_id: str) -> int:
        """Expurga imediatamente em memória todas as sessões ativas do dispositivo revogado."""
        with self.lock:
            to_delete = [digest for digest, item in self.sessions.items() if item[0] == device_id]
            for digest in to_delete:
                del self.sessions[digest]
            return len(to_delete)

    def clear(self) -> None:
        with self.lock:
            self.sessions.clear()


class DeviceRegistry:
    def __init__(self, path: Path = DEFAULT_DB):
        self.path = Path(path)
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path.parent.chmod(0o700)
        self.lock = threading.RLock()
        self._revocation_listeners: list[Callable[[str], None]] = []
        self.db = sqlite3.connect(self.path, check_same_thread=False, timeout=5)
        self.path.chmod(0o600)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS enrollment (
              code_hash TEXT PRIMARY KEY, expires INTEGER NOT NULL, used INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS devices (
              id TEXT PRIMARY KEY, name TEXT NOT NULL, public_key BLOB NOT NULL,
              status TEXT NOT NULL, added INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS nonces (
              device_id TEXT NOT NULL, nonce TEXT NOT NULL, expires INTEGER NOT NULL,
              PRIMARY KEY(device_id, nonce));
            CREATE TABLE IF NOT EXISTS permanently_revoked_keys (
              device_id TEXT PRIMARY KEY, revoked_at INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """)
        self.db.execute("INSERT OR IGNORE INTO settings VALUES('security_generation', '0')")
        self.db.commit()

    def register_revocation_listener(self, callback: Callable[[str], None]) -> None:
        """Registra um callback executado imediatamente quando um dispositivo for revogado."""
        with self.lock:
            if callback not in self._revocation_listeners:
                self._revocation_listeners.append(callback)

    def unregister_revocation_listener(self, callback: Callable[[str], None]) -> None:
        """Desregistra um callback de revogação."""
        with self.lock:
            if callback in self._revocation_listeners:
                self._revocation_listeners.remove(callback)

    def _notify_revocation(self, device_id: str) -> None:
        listeners = list(self._revocation_listeners)
        for listener in listeners:
            try:
                listener(device_id)
            except Exception:
                pass

    def close(self):
        self.db.close()

    def lockdown(self) -> bool:
        with self.lock:
            row = self.db.execute("SELECT value FROM settings WHERE key='lockdown'").fetchone()
            return bool(row and row[0] == "1")

    def set_lockdown(self, enabled: bool):
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO settings VALUES('lockdown', ?)", ("1" if enabled else "0",))
            self._advance_generation()
            if enabled:
                self.db.execute("DELETE FROM enrollment")
                self.db.execute("UPDATE devices SET status='revoked' WHERE status='pending'")

    def _advance_generation(self) -> None:
        self.db.execute("UPDATE settings SET value=CAST(value AS INTEGER)+1 WHERE key='security_generation'")

    def session_state(self, device_id: str) -> tuple[bool, int]:

        with self.lock:
            row = self.db.execute("""
                SELECT d.status, (SELECT value FROM settings WHERE key='lockdown'),
                       (SELECT value FROM settings WHERE key='security_generation')
                FROM devices AS d WHERE d.id=?
            """, (device_id,)).fetchone()
            return (bool(row and row[0] == "authorized" and row[1] != "1"),
                    int(row[2]) if row else -1)

    def open_enrollment(self, validity_seconds: int = 180) -> str:
        """Abre uma janela estrita de uso único para pareamento de novo dispositivo via QR Code."""
        if not 10 <= validity_seconds <= 600:
            raise ValueError("validity_seconds deve estar entre 10 e 600 segundos")
        with self.lock, self.db:
            if self.lockdown():
                raise ValueError("MARK está em lockdown")
            code = secrets.token_urlsafe(24)
            self.db.execute("DELETE FROM enrollment")
            self.db.execute("INSERT INTO enrollment VALUES(?, ?, 0)",
                            (hashlib.sha256(code.encode()).hexdigest(), int(time.time()) + validity_seconds))
            return code

    def generate_pairing_qr_payload(self, server_url: str, cert_fingerprint: str, validity_seconds: int = 180) -> dict:
        """Gera payload canônico para QR Code de uso único com janela estrita de expiração e fingerprint PKI."""
        if not isinstance(server_url, str) or not server_url.startswith("https://"):
            raise ValueError("URL do servidor deve usar HTTPS.")
        clean_pin = cert_fingerprint.strip().lower()
        if not isinstance(cert_fingerprint, str) or not re.fullmatch(r"[a-f0-9]{64}", clean_pin):
            raise ValueError("Fingerprint do certificado deve ser um hash SHA-256 de 64 caracteres hexadecimais.")
        code = self.open_enrollment(validity_seconds=validity_seconds)
        now = int(time.time())
        return {
            "version": "MARK-PAIR-V1",
            "url": server_url.rstrip("/"),
            "code": code,
            "pin": clean_pin,
            "expires_at": now + validity_seconds,
        }

    def enroll(self, code: str, name: str, public_der_b64: str, proof_b64: str) -> str:
        if not all(isinstance(value, str) for value in (code, name, public_der_b64, proof_b64)) or not 1 <= len(name) <= 60 or not 1 <= len(code) <= 100:
            raise ValueError("dados inválidos")
        public_der = unb64(public_der_b64)
        if len(public_der) > 512:
            raise ValueError("chave grande demais")
        key = serialization.load_der_public_key(public_der)
        if not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(key.curve, ec.SECP256R1):
            raise ValueError("use chave EC P-256")
        try:
            key.verify(unb64(proof_b64), b"MARK-PAIR-V1\n" + code.encode(), ec.ECDSA(hashes.SHA256()))
        except InvalidSignature as exc:
            raise ValueError("prova de posse inválida") from exc
        digest = hashlib.sha256(code.encode()).hexdigest()
        with self.lock, self.db:
            if self.lockdown():
                raise ValueError("MARK está em lockdown")
            row = self.db.execute("SELECT expires, used FROM enrollment WHERE code_hash=?", (digest,)).fetchone()
            if not row or row[1] or row[0] < time.time():
                raise ValueError("pareamento expirado ou já usado")
            device_id = b64(hashlib.sha256(public_der).digest())
            perm = self.db.execute("SELECT 1 FROM permanently_revoked_keys WHERE device_id=?", (device_id,)).fetchone()
            if perm:
                raise ValueError("chave e dispositivo revogados permanentemente; reativação proibida")
            self.db.execute("UPDATE enrollment SET used=1 WHERE code_hash=?", (digest,))
            self.db.execute("INSERT OR REPLACE INTO devices VALUES(?, ?, ?, 'pending', ?)",
                            (device_id, name, public_der, int(time.time())))
            self._advance_generation()
            return device_id

    def status(self, device_id: str) -> str:
        with self.lock:
            row = self.db.execute("SELECT status FROM devices WHERE id=?", (device_id,)).fetchone()
            return row[0] if row else "unknown"

    def list_devices(self) -> list[tuple[str, str, str]]:
        with self.lock:
            return self.db.execute("SELECT id, name, status FROM devices ORDER BY added DESC").fetchall()

    def update_device(self, device_id: str, status: str) -> bool:
        if status not in {"authorized", "revoked"}:
            raise ValueError("estado inválido")
        with self.lock, self.db:
            if status == "authorized" and self.lockdown():
                raise ValueError("MARK está em lockdown")
            if status == "authorized":
                changed = self.db.execute("UPDATE devices SET status='authorized' WHERE id=? AND status='pending'",
                                          (device_id,)).rowcount
            else:
                changed = self.db.execute("UPDATE devices SET status='revoked' WHERE id=? AND status IN ('pending','authorized')",
                                          (device_id,)).rowcount
                if changed:
                    self.db.execute("DELETE FROM nonces WHERE device_id=?", (device_id,))
            if changed:
                self._advance_generation()
        if changed and status == "revoked":
            self._notify_revocation(device_id)
        return bool(changed)

    def revoke_device(self, device_id: str, permanent: bool = False) -> bool:
        """Revoga imediatamente um dispositivo autorizado ou pendente em tempo real."""
        changed = self.update_device(device_id, "revoked")
        if permanent:
            with self.lock, self.db:
                self.db.execute("INSERT OR REPLACE INTO permanently_revoked_keys VALUES(?, ?)",
                                (device_id, int(time.time())))
        return changed

    def verify(self, device_id: str, method: str, path: str, body: bytes,
               timestamp: str, nonce: str, signature: str,
               request_id: str = "", session_token: str = "") -> bool:
        if (not isinstance(device_id, str) or len(device_id) != 43 or not _SAFE_ID.fullmatch(device_id)
                or not isinstance(nonce, str) or not _SAFE_ID.fullmatch(nonce)):
            return False
        try:
            if not isinstance(timestamp, str) or not re.fullmatch(r"[0-9]{1,12}", timestamp):
                return False
            seconds = int(timestamp)
            if abs(time.time() - seconds) > CLOCK_SKEW:
                return False
            if bool(request_id) != bool(session_token):
                return False
            if request_id and (not _SAFE_ID.fullmatch(request_id) or len(session_token) > 128
                                or not _SAFE_ID.fullmatch(session_token)):
                return False
            data = (session_message(method, path, body, timestamp, nonce, request_id, session_token)
                    if session_token else signed_message(method, path, body, timestamp, nonce))
            decoded = unb64(signature)
        except (ValueError, UnicodeError):
            return False
        with self.lock, self.db:
            if self.lockdown():
                return False
            row = self.db.execute("SELECT public_key FROM devices WHERE id=? AND status='authorized'",
                                  (device_id,)).fetchone()
            if not row:
                return False
            try:
                key = serialization.load_der_public_key(row[0])
                key.verify(decoded, data, ec.ECDSA(hashes.SHA256()))
            except (InvalidSignature, ValueError):
                return False
            self.db.execute("DELETE FROM nonces WHERE expires < ?", (int(time.time()),))
            try:
                self.db.execute("INSERT INTO nonces VALUES(?, ?, ?)",
                                (device_id, nonce, seconds + CLOCK_SKEW))
            except sqlite3.IntegrityError:
                return False
            return True

    def rotate_key(self, device_id: str, new_public_der_b64: str,
                   timestamp: str, nonce: str, signature: str, new_proof_b64: str,
                   request_id: str = "", session_token: str = "") -> str:
        """Rotaciona a chave ECDSA P-256 de um aparelho autorizado com prova de posse da chave anterior e da nova."""
        new_public_der = unb64(new_public_der_b64)
        if len(new_public_der) > 512:
            raise ValueError("chave grande demais")
        new_key = serialization.load_der_public_key(new_public_der)
        if not isinstance(new_key, ec.EllipticCurvePublicKey) or not isinstance(new_key.curve, ec.SECP256R1):
            raise ValueError("use chave EC P-256")
        body = json.dumps({"new_public_key": new_public_der_b64, "proof": new_proof_b64},
                          separators=(",", ":"), sort_keys=True).encode("ascii")
        if not self.verify(device_id, "POST", "/v1/device/rotate", body,
                           timestamp, nonce, signature, request_id, session_token):
            raise ValueError("assinatura atual inválida")
        try:
            new_key.verify(unb64(new_proof_b64),
                           f"MARK-ROTATE-V1\n{device_id}\n{timestamp}\n{nonce}".encode("ascii"),
                           ec.ECDSA(hashes.SHA256()))
        except InvalidSignature as exc:
            raise ValueError("prova de posse da nova chave inválida") from exc
        new_device_id = b64(hashlib.sha256(new_public_der).digest())
        with self.lock, self.db:
            if self.lockdown():
                raise ValueError("MARK está em lockdown")
            new_perm = self.db.execute("SELECT 1 FROM permanently_revoked_keys WHERE device_id=?", (new_device_id,)).fetchone()
            if new_perm:
                raise ValueError("nova chave corresponde a um dispositivo revogado; rotação proibida")
            row = self.db.execute("SELECT name FROM devices WHERE id=? AND status='authorized'", (device_id,)).fetchone()
            if not row:
                raise ValueError("aparelho não autorizado")
            self.db.execute("DELETE FROM devices WHERE id=?", (device_id,))
            self.db.execute("INSERT OR REPLACE INTO devices VALUES(?, ?, ?, 'authorized', ?)",
                            (new_device_id, row[0], new_public_der, int(time.time())))
            self._advance_generation()
            return new_device_id

    def recover_after_device_theft(
        self,
        lost_device_id: str,
        new_name: str,
        new_public_der_b64: str,
        new_proof_b64: str,
        enrollment_code: str,
        auto_authorize: bool = False,
    ) -> str:
        """Procedimento seguro pós-roubo/perda:
        1. Revoga permanentemente o dispositivo furtado/comprometido.
        2. Impede reativação da chave antiga.
        3. Registra o dispositivo substituto com o novo par de chaves EC P-256.
        4. Opcionalmente autoriza o novo dispositivo se auto_authorize=True.
        """
        self.revoke_device(lost_device_id, permanent=True)
        new_device_id = self.enroll(
            code=enrollment_code,
            name=new_name,
            public_der_b64=new_public_der_b64,
            proof_b64=new_proof_b64,
        )
        if auto_authorize:
            self.update_device(new_device_id, "authorized")
        return new_device_id


def create_owner_challenge(action: str, device_id: str, timestamp: int | None = None, nonce: str | None = None) -> tuple[str, str, str]:
    """Cria o desafio canônico para confirmação do proprietário (biometria ou PIN)."""
    ts = str(int(time.time() if timestamp is None else timestamp))
    n = secrets.token_hex(16) if nonce is None else nonce
    challenge = f"MARK-OWNER-CONFIRM-V1\n{action}\n{device_id}\n{ts}\n{n}"
    return challenge, ts, n


def assert_not_llm_approved(source: str, action: str) -> None:
    """Bloqueia categoricamente a tentativa do modelo de linguagem de aprovar suas próprias operações sensíveis."""
    clean_src = str(source).strip().lower()
    if clean_src in LLM_BLOCKED_SOURCES:
        raise PermissionError(
            f"Aprovação autônoma pelo modelo de linguagem proibida para a ação crítica '{action}'. "
            "Confirmação física ou biométrica do proprietário obrigatória."
        )


def verify_owner_confirmation(
    devices: DeviceRegistry,
    device_id: str,
    action: str,
    timestamp: str,
    nonce: str,
    signature_b64: str,
    max_age_seconds: int = 30,
) -> bool:
    """Valida a prova criptográfica de confirmação do proprietário (ECDSA P-256) com proteção anti-replay e janela estrita."""
    if action not in CRITICAL_ACTIONS:
        return False
    try:
        ts_int = int(timestamp)
        now = time.time()
        if abs(now - ts_int) > max_age_seconds:
            return False
        if not _SAFE_ID.fullmatch(nonce):
            return False
        challenge = f"MARK-OWNER-CONFIRM-V1\n{action}\n{device_id}\n{timestamp}\n{nonce}".encode("ascii")
        sig_bytes = unb64(signature_b64)
    except (ValueError, TypeError):
        return False

    with devices.lock, devices.db:
        if devices.lockdown():
            return False
        row = devices.db.execute(
            "SELECT public_key FROM devices WHERE id=? AND status='authorized'",
            (device_id,),
        ).fetchone()
        if not row:
            return False
        try:
            key = serialization.load_der_public_key(row[0])
            key.verify(sig_bytes, challenge, ec.ECDSA(hashes.SHA256()))
        except (InvalidSignature, ValueError):
            return False

        # Anti-replay estrito no nonce de confirmação
        devices.db.execute("DELETE FROM nonces WHERE expires < ?", (int(now),))
        try:
            devices.db.execute(
                "INSERT INTO nonces VALUES(?, ?, ?)",
                (f"owner-confirm:{device_id}", nonce, ts_int + max_age_seconds),
            )
        except sqlite3.IntegrityError:
            return False
        return True


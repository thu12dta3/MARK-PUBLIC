"""Identidade Criptográfica e Gestão de Dispositivos no MARK Link."""

from __future__ import annotations

import base64
import hashlib
import re
from typing import Final

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from ...device_auth import DeviceRegistry, b64, unb64

_SAFE_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")


class LinkDevice:
    """Representação da identidade criptográfica de um dispositivo."""

    def __init__(self, device_id: str, public_der: bytes, status: str = "pending") -> None:
        self.device_id = device_id
        self.public_der = public_der
        self.status = status

    @classmethod
    def from_public_der(cls, public_der: bytes, status: str = "pending") -> LinkDevice:
        digest = hashlib.sha256(public_der).digest()
        device_id = b64(digest)
        return cls(device_id=device_id, public_der=public_der, status=status)

    def is_valid_id(self) -> bool:
        return len(self.device_id) == 43 and bool(_SAFE_ID.fullmatch(self.device_id))

    def verify_signature(self, message: bytes, signature_b64: str) -> bool:
        try:
            signature = unb64(signature_b64)
            key = serialization.load_der_public_key(self.public_der)
            if not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(key.curve, ec.SECP256R1):
                return False
            key.verify(signature, message, ec.ECDSA(hashes.SHA256()))
            return True
        except (InvalidSignature, ValueError, TypeError):
            return False


class LinkDeviceManager:
    """Ponte entre o MARK Link e o registro canônico de dispositivos do MARK."""

    def __init__(self, registry: DeviceRegistry | None = None) -> None:
        self.registry = registry or DeviceRegistry()

    def get_status(self, device_id: str) -> str:
        return self.registry.status(device_id)

    def is_authorized(self, device_id: str) -> bool:
        if self.registry.lockdown():
            return False
        return self.registry.status(device_id) == "authorized"

    def get_public_der(self, device_id: str) -> bytes | None:
        """Obtém os bytes DER da chave pública EC P-256 de um aparelho autorizado."""
        with self.registry.lock:
            row = self.registry.db.execute(
                "SELECT public_key FROM devices WHERE id=? AND status='authorized'",
                (device_id,),
            ).fetchone()
            return row[0] if row else None

    def revoke_device(self, device_id: str, permanent: bool = True) -> bool:
        return self.registry.revoke_device(device_id, permanent=permanent)


def canonical_handshake_init(
    device_id: str,
    ephemeral_pk_der_b64: str,
    nonce_b64: str,
    timestamp: int,
) -> bytes:
    """Mensagem canônica para assinatura no HandshakeInit do MARK Link."""
    return f"MARK-LINK-AUTH-INIT-V1\n{device_id}\n{ephemeral_pk_der_b64}\n{nonce_b64}\n{timestamp}".encode("utf-8")


def canonical_handshake_response(
    server_id: str,
    ephemeral_pk_der_b64: str,
    client_nonce_b64: str,
    server_nonce_b64: str,
    timestamp: int,
    init_hash_b64: str = "",
) -> bytes:
    """Mensagem canônica para resposta no HandshakeResponse do MARK Link com vinculação de init_hash."""
    if not init_hash_b64:
        return f"MARK-LINK-AUTH-RESP-V1\n{server_id}\n{ephemeral_pk_der_b64}\n{client_nonce_b64}\n{server_nonce_b64}\n{timestamp}".encode("utf-8")
    return f"MARK-LINK-AUTH-RESP-V1\n{server_id}\n{ephemeral_pk_der_b64}\n{client_nonce_b64}\n{server_nonce_b64}\n{timestamp}\n{init_hash_b64}".encode("utf-8")


def compute_transcript_hash(canonical_init: bytes, canonical_resp: bytes) -> bytes:
    """Calcula o digest SHA-256 do transcript completo (Init + Response) para Session Binding."""
    return hashlib.sha256(canonical_init + b"\n" + canonical_resp).digest()

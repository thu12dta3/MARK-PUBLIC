"""Envelope Cifrado do Relay Cego do MARK Link."""

from __future__ import annotations

import re
import struct
from typing import Final

RELAY_MAGIC: Final[bytes] = b"MRLY"
MAX_ENVELOPE_PAYLOAD: Final[int] = 64 * 1024  # 64 KB

_SAFE_DEVICE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")


def is_valid_device_id(dev_id: str) -> bool:
    """Valida se o identificador de dispositivo é um DeviceId seguro do MARK.

    Bloqueia estritamente:
    - IPs arbitrários (IPv4/IPv6 com pontos ou dois-pontos).
    - URLs ou proxies com esquemas http/https.
    - Caminhos de sistema ou sockets (/dev/tcp/...).
    - Identificadores fora do comprimento permitido (16 a 64 chars).
    """
    if not isinstance(dev_id, str):
        return False
    if any(c in dev_id for c in (":", "/", ".", "\\", "@", " ", "?", "#")):
        return False
    return bool(_SAFE_DEVICE_ID_RE.match(dev_id))


class EncryptedEnvelope:
    """Envelope de roteamento cego: o relay só lê os IDs de origem e destino."""

    def __init__(self, target_id: str, source_id: str, opaque_payload: bytes) -> None:
        if not is_valid_device_id(target_id):
            raise ValueError(f"Identificador de dispositivo no envelope inválido (Destino inválido): {target_id}")
        if not is_valid_device_id(source_id):
            raise ValueError(f"Identificador de dispositivo no envelope inválido (Origem inválida): {source_id}")
        if len(opaque_payload) > MAX_ENVELOPE_PAYLOAD:
            raise ValueError(f"Payload do envelope ({len(opaque_payload)} B) excede o limite de {MAX_ENVELOPE_PAYLOAD} B")
        self.target_id = target_id
        self.source_id = source_id
        self.opaque_payload = opaque_payload

    def serialize(self) -> bytes:
        target_bytes = self.target_id.encode("utf-8")
        source_bytes = self.source_id.encode("utf-8")
        payload_len = len(self.opaque_payload)

        # Magic (4B) + TargetLen (1B) + Target + SourceLen (1B) + Source + PayloadLen (4B) + Payload
        header = (
            struct.pack("!4sB", RELAY_MAGIC, len(target_bytes))
            + target_bytes
            + struct.pack("!B", len(source_bytes))
            + source_bytes
            + struct.pack("!L", payload_len)
        )
        return header + self.opaque_payload

    @classmethod
    def deserialize(cls, data: bytes) -> EncryptedEnvelope:
        if len(data) < 4 + 1 + 1 + 4:
            raise ValueError("Buffer truncado para envelope de relay")
        if not data.startswith(RELAY_MAGIC):
            raise ValueError(f"Magic de relay inválido: {data[:4]!r}")

        offset = 4
        target_len = data[offset]
        offset += 1
        if offset + target_len > len(data):
            raise ValueError("Buffer truncado para target_id")
        target_id = data[offset : offset + target_len].decode("utf-8")
        offset += target_len

        if offset >= len(data):
            raise ValueError("Buffer truncado antes de source_id")
        source_len = data[offset]
        offset += 1
        if offset + source_len + 4 > len(data):
            raise ValueError("Buffer truncado para source_id")
        source_id = data[offset : offset + source_len].decode("utf-8")
        offset += source_len

        payload_len = struct.unpack("!L", data[offset : offset + 4])[0]
        offset += 4
        if offset + payload_len > len(data):
            raise ValueError("Buffer truncado para payload opaco")
        opaque_payload = data[offset : offset + payload_len]

        return cls(target_id=target_id, source_id=source_id, opaque_payload=opaque_payload)

"""Enquadramento Binário e Criptografia de Transporte do MARK Link (Protocolo MLNK v1)."""

from __future__ import annotations

import struct
from enum import IntEnum
from typing import Final

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

MAGIC: Final[bytes] = b"MLNK"
PROTOCOL_VERSION: Final[int] = 1
HEADER_SIZE: Final[int] = 20
TAG_SIZE: Final[int] = 16
MAX_PAYLOAD_SIZE: Final[int] = 4 * 1024 * 1024   # 4 MB rígido (mitigação de OOM)
MAX_FRAME_SIZE: Final[int] = 64 * 1024           # 64 KB


class PacketType(IntEnum):
    HANDSHAKE_INIT = 0x01
    HANDSHAKE_RESPONSE = 0x02
    DATA = 0x03
    PING = 0x04
    PONG = 0x05
    CLOSE = 0x06
    ERROR = 0x07
    RELAY_ENVELOPE = 0x08
    SESSION_RESUME = 0x09
    SESSION_RESUME_OK = 0x0A


class PacketHeader:
    """Cabeçalho binário canônico de 20 bytes do protocolo MLNK."""

    def __init__(self, packet_type: PacketType, flags: int, sequence: int, length: int) -> None:
        self.packet_type = packet_type
        self.flags = flags
        self.sequence = sequence
        self.length = length

    def encode(self) -> bytes:
        # Magic (4B) + Version (1B) + Type (1B) + Flags (2B) + Seq (8B) + Length (4B) = 20B
        return struct.pack("!4sBBHQL", MAGIC, PROTOCOL_VERSION, int(self.packet_type), self.flags, self.sequence, self.length)

    @classmethod
    def decode(cls, data: bytes) -> PacketHeader:
        if len(data) < HEADER_SIZE:
            raise ValueError(f"Cabeçalho incompleto: esperado {HEADER_SIZE} bytes, recebido {len(data)}")
        magic, version, pkt_type, flags, seq, length = struct.unpack("!4sBBHQL", data[:HEADER_SIZE])
        if magic != MAGIC:
            raise ValueError(f"Magic de protocolo inválido: {magic!r}")
        if version != PROTOCOL_VERSION:
            raise ValueError(f"Versão de protocolo incompatível: {version}")
        if length > MAX_PAYLOAD_SIZE:
            raise ValueError(f"Tamanho de carga excede o limite máximo ({length} > {MAX_PAYLOAD_SIZE})")
        try:
            packet_type = PacketType(pkt_type)
        except ValueError:
            raise ValueError(f"Tipo de pacote desconhecido: 0x{pkt_type:02X}")
        return cls(packet_type=packet_type, flags=flags, sequence=seq, length=length)


import threading
from ..identity.replay import ReplayFilter


class SecureChannel:
    """Canal seguro de transporte criptografado com AES-256-GCM e AAD."""

    def __init__(self, key: bytes, nonce_salt: bytes, replay_filter: ReplayFilter | None = None) -> None:
        if len(key) != 32:
            raise ValueError("A chave do canal deve ter exatamente 32 bytes (AES-256)")
        if len(nonce_salt) != 4:
            raise ValueError("O salt de nonce deve ter exatamente 4 bytes")
        self.aesgcm = AESGCM(key)
        self.nonce_salt = nonce_salt
        self.tx_seq = 1
        self.replay_filter = replay_filter
        self._lock = threading.Lock()

    @classmethod
    def from_shared_secret(cls, shared_secret: bytes, salt: bytes, info: bytes = b"mark-link-v1") -> SecureChannel:
        hkdf = HKDF(
            algorithm=hashes.SHA256(),
            length=36,
            salt=salt,
            info=info,
        )
        derived = hkdf.derive(shared_secret)
        key = derived[:32]
        nonce_salt = derived[32:36]
        return cls(key=key, nonce_salt=nonce_salt)

    def _make_nonce(self, seq: int) -> bytes:
        # Nonce determinístico de 12 bytes: 4 bytes salt + 8 bytes seq (big-endian)
        return self.nonce_salt + struct.pack("!Q", seq)

    def seal_packet(self, packet_type: PacketType, plaintext: bytes, flags: int = 0) -> bytes:
        if len(plaintext) + TAG_SIZE > MAX_PAYLOAD_SIZE:
            raise ValueError("Dados excedem tamanho máximo de payload")
        with self._lock:
            seq = self.tx_seq
            if seq >= (1 << 32) - 1:
                raise OverflowError("Contador de sequência de nonces esgotado (prevenção de colisão de nonces)")
            self.tx_seq += 1

        payload_len = len(plaintext) + TAG_SIZE
        header = PacketHeader(packet_type=packet_type, flags=flags, sequence=seq, length=payload_len)
        header_bytes = header.encode()
        nonce = self._make_nonce(seq)

        # Header passado como AAD (Additional Authenticated Data)
        ciphertext = self.aesgcm.encrypt(nonce, plaintext, header_bytes)
        return header_bytes + ciphertext

    def unseal_packet(self, frame: bytes) -> tuple[PacketHeader, bytes]:
        if len(frame) < HEADER_SIZE:
            raise ValueError(f"Cabeçalho incompleto: esperado pelo menos {HEADER_SIZE} bytes, recebido {len(frame)}")
        if len(frame) < HEADER_SIZE + TAG_SIZE:
            raise ValueError("Frame curto demais para decifragem (menor que o cabeçalho com tag MAC)")
        header = PacketHeader.decode(frame[:HEADER_SIZE])
        expected_len = HEADER_SIZE + header.length
        if len(frame) < expected_len:
            raise ValueError(f"Frame incompleto: recebido {len(frame)}, esperado {expected_len}")

        if self.replay_filter and not self.replay_filter.validate_sequence(header.sequence):
            raise PermissionError(f"Pacote duplicado ou fora da janela detectado (replay seq={header.sequence})")

        header_bytes = frame[:HEADER_SIZE]
        ciphertext = frame[HEADER_SIZE:expected_len]
        nonce = self._make_nonce(header.sequence)

        try:
            plaintext = self.aesgcm.decrypt(nonce, ciphertext, header_bytes)
            return header, plaintext
        except InvalidTag as exc:
            raise PermissionError("Falha de autenticação do frame: dados corrompidos ou chave inválida.") from exc


class DirectionalSecureChannel:
    """Canal seguro bidirecional com separação estrita de chaves e nonces (TX vs RX)."""

    def __init__(
        self,
        tx_key: bytes,
        tx_nonce_salt: bytes,
        rx_key: bytes,
        rx_nonce_salt: bytes,
        replay_filter: ReplayFilter | None = None,
    ) -> None:
        if len(tx_key) != 32 or len(rx_key) != 32:
            raise ValueError("As chaves de TX e RX devem ter exatamente 32 bytes (AES-256)")
        if len(tx_nonce_salt) != 4 or len(rx_nonce_salt) != 4:
            raise ValueError("Os salts de nonce devem ter exatamente 4 bytes")

        self.tx_key = tx_key
        self.rx_key = rx_key
        self.tx_aesgcm = AESGCM(tx_key)
        self.rx_aesgcm = AESGCM(rx_key)
        self.tx_nonce_salt = tx_nonce_salt
        self.rx_nonce_salt = rx_nonce_salt
        self.tx_seq = 1
        self.replay_filter = replay_filter or ReplayFilter(window_size=64)
        self._lock = threading.Lock()

    @classmethod
    def derive(
        cls,
        shared_secret: bytes,
        client_nonce: bytes,
        server_nonce: bytes,
        is_server: bool = False,
        transcript_hash: bytes | None = None,
    ) -> DirectionalSecureChannel:
        info = (
            b"mark-link-v1-session-bound\n" + transcript_hash
            if transcript_hash is not None
            else b"mark-link-v1-directional-session"
        )
        hkdf = HKDF(
            algorithm=hashes.SHA256(),
            length=72,
            salt=client_nonce + server_nonce,
            info=info,
        )
        okm = hkdf.derive(shared_secret)
        c2s_key = okm[0:32]
        c2s_salt = okm[32:36]
        s2c_key = okm[36:68]
        s2c_salt = okm[68:72]

        if is_server:
            return cls(
                tx_key=s2c_key,
                tx_nonce_salt=s2c_salt,
                rx_key=c2s_key,
                rx_nonce_salt=c2s_salt,
            )
        else:
            return cls(
                tx_key=c2s_key,
                tx_nonce_salt=c2s_salt,
                rx_key=s2c_key,
                rx_nonce_salt=s2c_salt,
            )

    def _make_tx_nonce(self, seq: int) -> bytes:
        return self.tx_nonce_salt + struct.pack("!Q", seq)

    def _make_rx_nonce(self, seq: int) -> bytes:
        return self.rx_nonce_salt + struct.pack("!Q", seq)

    def seal_packet(self, packet_type: PacketType, plaintext: bytes, flags: int = 0) -> bytes:
        if len(plaintext) + TAG_SIZE > MAX_PAYLOAD_SIZE:
            raise ValueError("Dados excedem tamanho máximo de payload")
        with self._lock:
            seq = self.tx_seq
            if seq >= (1 << 32) - 1:
                raise OverflowError("Exaustão de sequência de nonces (prevenção de colisão de nonces)")
            self.tx_seq += 1

        payload_len = len(plaintext) + TAG_SIZE
        header = PacketHeader(packet_type=packet_type, flags=flags, sequence=seq, length=payload_len)
        header_bytes = header.encode()
        nonce = self._make_tx_nonce(seq)

        ciphertext = self.tx_aesgcm.encrypt(nonce, plaintext, header_bytes)
        return header_bytes + ciphertext

    def unseal_packet(self, frame: bytes) -> tuple[PacketHeader, bytes]:
        if len(frame) < HEADER_SIZE:
            raise ValueError(f"Cabeçalho incompleto: esperado pelo menos {HEADER_SIZE} bytes, recebido {len(frame)}")
        if len(frame) < HEADER_SIZE + TAG_SIZE:
            raise ValueError("Frame curto demais para decifragem (menor que o cabeçalho com tag MAC)")
        header = PacketHeader.decode(frame[:HEADER_SIZE])
        expected_len = HEADER_SIZE + header.length
        if len(frame) < expected_len:
            raise ValueError(f"Frame incompleto: recebido {len(frame)}, esperado {expected_len}")

        # Validação anti-replay preliminar antes da decifragem
        if not self.replay_filter.check_sequence(header.sequence):
            raise PermissionError(f"Pacote duplicado ou fora da janela detectado (replay seq={header.sequence})")

        header_bytes = frame[:HEADER_SIZE]
        ciphertext = frame[HEADER_SIZE:expected_len]
        nonce = self._make_rx_nonce(header.sequence)

        try:
            plaintext = self.rx_aesgcm.decrypt(nonce, ciphertext, header_bytes)
            self.replay_filter.commit_sequence(header.sequence)
            return header, plaintext
        except InvalidTag as exc:
            raise PermissionError("Falha de autenticação do frame: dados corrompidos ou chave inválida.") from exc

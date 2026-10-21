"""Protocolo de sincronização resiliente e gerenciador de conexões multiplataforma do MARK.

Implementa:
- Protocolo padronizado para comunicação bidirecional de mensagens, tarefas e arquivos.
- Handshake criptográfico efêmero (ECDH P-256 + AES-256-GCM) para pareamento QR Code.
- Controle de idempotência para evitar duplicação de comandos após quedas de conexão.
- Retomada de sessão com controle de sequência e buffer de replay.
- Gerenciador de dispositivos com aprovação explícita e revogação granular.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import socket
import subprocess
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from ..device_auth import DEFAULT_DB, DeviceRegistry, SessionRegistry, b64, unb64

logger = logging.getLogger("mark.network.sync")

# Constantes do Protocolo
PROTOCOL_VERSION = "MARK-SYNC-V2"
DEFAULT_PAIRING_PORT = 8765
DEFAULT_PAIRING_LIFETIME = 180  # 3 minutos para QR Code de uso único
MAX_REPLAY_BUFFER = 64
CHUNK_SIZE_BYTES = 32768  # 32 KB para transferência em pedaços

class SyncMessageType(str, Enum):
    """Tipos de mensagens suportadas pelo protocolo de sincronização."""
    MESSAGE = "message"
    TASK = "task"
    TASK_RESULT = "task_result"
    FILE_START = "file_start"
    FILE_CHUNK = "file_chunk"
    FILE_ACK = "file_ack"
    HEARTBEAT = "heartbeat"
    ACK = "ack"
    RESUME = "resume"
    RESUME_ACK = "resume_ack"
    REVOKE = "revoke"
    ERROR = "error"


_b64e = b64
_b64d = unb64


def get_best_local_endpoint(default_port: int = DEFAULT_PAIRING_PORT) -> dict[str, str]:
    """Detecta a melhor URL ou IP para o dispositivo móvel se conectar ao PC.
    
    Prioriza Tailscale (se ativo e com HTTPS válido), caindo para IPv4 LAN local.
    """
    ts_url = ""
    ts_ip = ""
    try:
        res = subprocess.run(
            ["tailscale", "status", "--json"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        if res.returncode == 0 and res.stdout.strip():
            ts_data = json.loads(res.stdout)
            if ts_data.get("BackendState") == "Running":
                self_node = ts_data.get("Self", {})
                dns_name = self_node.get("DNSName", "").rstrip(".")
                if dns_name.endswith(".ts.net"):
                    ts_url = f"https://{dns_name}"
                ips = self_node.get("TailscaleIPs", [])
                if ips:
                    ts_ip = ips[0]
    except Exception as exc:
        logger.debug("Tailscale não detectado ou inativo: %s", exc)

    # Detecta IPv4 da rede local (LAN)
    local_ip = "127.0.0.1"
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            # Não conecta de verdade, apenas descobre a interface de saída
            s.connect(("10.255.255.255", 1))
            local_ip = s.getsockname()[0]
    except Exception:
        try:
            local_ip = socket.gethostbyname(socket.gethostname())
        except Exception:
            local_ip = "127.0.0.1"

    # MARK Link direto (transporte isolado próprio em Rust)
    link_port = 8766
    link_direct = f"mark-link://{local_ip}:{link_port}"

    primary_url = ts_url if ts_url else f"http://{local_ip}:{default_port}"
    return {
        "url": primary_url,
        "tailscale_url": ts_url,
        "tailscale_ip": ts_ip,
        "local_ip": local_ip,
        "port": str(default_port),
        "link_direct": link_direct,
        "link_port": str(link_port),
    }


@dataclass
class SyncPacket:
    """Pacote serializável do protocolo MARK Sync."""
    packet_id: str
    type: SyncMessageType
    seq: int
    ack_seq: int
    idempotency_key: str
    payload: dict[str, Any]
    timestamp: float = field(default_factory=time.time)
    session_token: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": PROTOCOL_VERSION,
            "packet_id": self.packet_id,
            "type": self.type.value,
            "seq": self.seq,
            "ack_seq": self.ack_seq,
            "idempotency_key": self.idempotency_key,
            "payload": self.payload,
            "timestamp": self.timestamp,
            "session_token": self.session_token,
        }

    def serialize(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"))

    @classmethod
    def deserialize(cls, data_str: str | bytes) -> SyncPacket:
        raw = json.loads(data_str)
        return cls(
            packet_id=raw["packet_id"],
            type=SyncMessageType(raw["type"]),
            seq=int(raw.get("seq", 0)),
            ack_seq=int(raw.get("ack_seq", 0)),
            idempotency_key=raw.get("idempotency_key", ""),
            payload=raw.get("payload", {}),
            timestamp=float(raw.get("timestamp", time.time())),
            session_token=raw.get("session_token", ""),
        )


class IdempotencyManager:
    """Gerenciador de idempotência com deduplicação de mensagens e tarefas.
    
    Evita a execução duplicada de comandos mesmo quando o dispositivo móvel
    retransmite requisições após quedas temporárias de rede.
    """

    def __init__(self, max_entries: int = 500, ttl_seconds: float = 86400.0):
        self._max_entries = max_entries
        self._ttl_seconds = ttl_seconds
        self._cache: dict[str, tuple[float, dict[str, Any]]] = {}

    def is_duplicate(self, idempotency_key: str) -> bool:
        if not idempotency_key:
            return False
        self._prune_expired()
        return idempotency_key in self._cache

    def get_cached_result(self, idempotency_key: str) -> dict[str, Any] | None:
        if not idempotency_key:
            return None
        self._prune_expired()
        entry = self._cache.get(idempotency_key)
        return entry[1] if entry else None

    def record_result(self, idempotency_key: str, result: dict[str, Any]) -> None:
        if not idempotency_key:
            return
        self._prune_expired()
        if len(self._cache) >= self._max_entries:
            # Remove o mais antigo
            oldest_key = min(self._cache.keys(), key=lambda k: self._cache[k][0])
            del self._cache[oldest_key]
        self._cache[idempotency_key] = (time.time(), result)

    def _prune_expired(self) -> None:
        now = time.time()
        expired = [k for k, (ts, _) in self._cache.items() if (now - ts) > self._ttl_seconds]
        for k in expired:
            del self._cache[k]


@dataclass
class PairingPayload:
    """Payload estruturado do QR Code efêmero de uso único."""
    version: str
    url: str
    ip: str
    port: int
    code: str
    pin: str
    challenge: str
    ephemeral_pk: str
    expires_at: int

    def to_json(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"))


class PairingSession:
    """Sessão efêmera de pareamento de novo dispositivo.
    
    Gera par de chaves efêmero ECDH P-256 e challenge token de uso único.
    Aguarda o handshake com AES-256-GCM iniciado pelo smartphone.
    """

    def __init__(
        self,
        device_registry: DeviceRegistry,
        validity_seconds: int = DEFAULT_PAIRING_LIFETIME,
        port: int = DEFAULT_PAIRING_PORT,
        cert_pin: str = "",
    ):
        self.device_registry = device_registry
        self.validity_seconds = validity_seconds
        self.port = port
        self.expires_at = int(time.time()) + validity_seconds
        self.challenge = secrets.token_urlsafe(32)

        # Gera chave ECDH efêmera do PC para o handshake de pareamento
        self._ecdh_private_key = ec.generate_private_key(ec.SECP256R1())
        pub_der = self._ecdh_private_key.public_key().public_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        self.ephemeral_pk_b64 = _b64e(pub_der)

        # Registra código de uso único no DeviceRegistry
        self.enrollment_code = self.device_registry.open_enrollment(validity_seconds=validity_seconds)

        # Detecta endpoint
        endpoints = get_best_local_endpoint(default_port=port)
        self.url = endpoints["url"]
        self.ip = endpoints["local_ip"]
        self.pin = cert_pin if cert_pin else hashlib.sha256(pub_der).hexdigest()

        self._pending_approval: dict[str, Any] | None = None
        self._used = False

    def is_expired(self) -> bool:
        return time.time() >= self.expires_at or self._used

    def get_qr_payload(self) -> PairingPayload:
        """Gera o payload JSON completo para exibição no QR Code."""
        return PairingPayload(
            version="MARK-PAIR-V2",
            url=self.url,
            ip=self.ip,
            port=self.port,
            code=self.enrollment_code,
            pin=self.pin,
            challenge=self.challenge,
            ephemeral_pk=self.ephemeral_pk_b64,
            expires_at=self.expires_at,
        )

    def process_handshake(
        self,
        client_ephemeral_pk_der_b64: str,
        encrypted_payload_b64: str,
        nonce_b64: str,
    ) -> dict[str, Any]:
        """Processa o handshake criptográfico recebido do dispositivo móvel.
        
        Deriva chave AES-256-GCM via HKDF sobre ECDH, decifra o pacote de pareamento,
        valida o token de desafio e enfileira para aprovação do proprietário.
        """
        if self.is_expired():
            raise ValueError("Sessão de pareamento expirada ou já utilizada.")

        # Carrega chave pública efêmera do cliente
        client_der = _b64d(client_ephemeral_pk_der_b64)
        client_pub = serialization.load_der_public_key(client_der)
        if not isinstance(client_pub, ec.EllipticCurvePublicKey) or not isinstance(client_pub.curve, ec.SECP256R1):
            raise ValueError("Chave efêmera inválida; deve ser EC P-256.")

        # Deriva segredo compartilhado ECDH
        shared_secret = self._ecdh_private_key.exchange(ec.ECDH(), client_pub)

        # KDF -> AES-256-GCM key (32 bytes)
        hkdf = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=self.challenge.encode("utf-8"),
            info=b"mark-pairing-handshake-v2",
        )
        aes_key = hkdf.derive(shared_secret)

        # Decifra payload com AES-256-GCM
        nonce = _b64d(nonce_b64)
        ciphertext = _b64d(encrypted_payload_b64)
        aesgcm = AESGCM(aes_key)
        try:
            plaintext = aesgcm.decrypt(nonce, ciphertext, None)
        except Exception as exc:
            raise ValueError("Falha na autenticação/decriptação AES-256-GCM do handshake.") from exc

        data = json.loads(plaintext.decode("utf-8"))

        # Valida challenge token
        received_challenge = data.get("challenge")
        if not received_challenge or not secrets.compare_digest(received_challenge, self.challenge):
            raise ValueError("Desafio criptográfico inválido.")

        device_name = data.get("name", "Dispositivo Móvel")
        device_public_key_b64 = data.get("public_key")
        device_proof_b64 = data.get("proof")

        if not device_public_key_b64 or not device_proof_b64:
            raise ValueError("Chave de identidade ou prova de posse ausentes no handshake.")

        # Registra o dispositivo no DeviceRegistry em estado 'pending'
        device_id = self.device_registry.enroll(
            code=self.enrollment_code,
            name=device_name,
            public_der_b64=device_public_key_b64,
            proof_b64=device_proof_b64,
        )

        self._pending_approval = {
            "device_id": device_id,
            "name": device_name,
            "received_at": time.time(),
            "aes_key": aes_key,
        }
        self._used = True

        return {
            "device_id": device_id,
            "name": device_name,
            "status": "pending",
        }


class ResilientSessionStream:
    """Gerencia o canal resiliente de mensagens com um dispositivo específico.
    
    Controla sequenciamento estrito, buffer de replay para reconexões e deduplicação.
    """

    def __init__(self, device_id: str, idempotency_manager: IdempotencyManager):
        self.device_id = device_id
        self.idempotency = idempotency_manager
        self.tx_seq = 0
        self.rx_seq = 0
        self.last_activity = time.time()
        self.replay_buffer: deque[SyncPacket] = deque(maxlen=MAX_REPLAY_BUFFER)
        self.active_file_transfers: dict[str, dict[str, Any]] = {}

    def create_packet(
        self,
        msg_type: SyncMessageType,
        payload: dict[str, Any],
        idempotency_key: str | None = None,
        session_token: str = "",
    ) -> SyncPacket:
        self.tx_seq += 1
        key = idempotency_key or secrets.token_hex(16)
        pkt = SyncPacket(
            packet_id=secrets.token_hex(12),
            type=msg_type,
            seq=self.tx_seq,
            ack_seq=self.rx_seq,
            idempotency_key=key,
            payload=payload,
            timestamp=time.time(),
            session_token=session_token,
        )
        self.replay_buffer.append(pkt)
        self.last_activity = time.time()
        return pkt

    def acknowledge_received(self, remote_seq: int) -> None:
        if remote_seq > self.rx_seq:
            self.rx_seq = remote_seq
        self.last_activity = time.time()

    def get_replay_packets(self, from_seq: int) -> list[SyncPacket]:
        """Retorna pacotes do buffer que o cliente perdeu durante a desconexão."""
        return [pkt for pkt in self.replay_buffer if pkt.seq > from_seq]


class ResilientSyncProtocol:
    """Serviço principal do protocolo resiliente MARK Sync no PC."""

    def __init__(self, db_path: Path = DEFAULT_DB):
        self.device_registry = DeviceRegistry(db_path)
        self.session_registry = SessionRegistry(self.device_registry)
        self.idempotency = IdempotencyManager()
        self.sessions: dict[str, ResilientSessionStream] = {}
        self.active_pairing: PairingSession | None = None
        self._approval_callback: Callable[[str, str], bool] | None = None

    def set_approval_callback(self, callback: Callable[[str, str], bool]) -> None:
        """Define o callback de aprovação na UI do PyQt6 ("Aprovar novo dispositivo?")."""
        self._approval_callback = callback

    def open_pairing(self, validity_seconds: int = DEFAULT_PAIRING_LIFETIME) -> PairingPayload:
        """Abre uma janela para conexão via QR Code de uso único."""
        self.active_pairing = PairingSession(
            device_registry=self.device_registry,
            validity_seconds=validity_seconds,
        )
        return self.active_pairing.get_qr_payload()

    def cancel_pairing(self) -> None:
        self.active_pairing = None

    def get_or_create_stream(self, device_id: str) -> ResilientSessionStream:
        if device_id not in self.sessions:
            self.sessions[device_id] = ResilientSessionStream(device_id, self.idempotency)
        return self.sessions[device_id]

    def process_incoming_packet(
        self,
        device_id: str,
        packet: SyncPacket,
        on_message: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        on_task: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ) -> SyncPacket:
        """Processa pacote de entrada garantindo idempotência e atualizando sequenciamento."""
        # Verifica se o dispositivo está autorizado
        status = self.device_registry.status(device_id)
        if status != "authorized":
            return SyncPacket(
                packet_id=secrets.token_hex(12),
                type=SyncMessageType.ERROR,
                seq=0,
                ack_seq=packet.seq,
                idempotency_key=packet.idempotency_key,
                payload={"error": f"Dispositivo não autorizado (status: {status})"},
            )

        stream = self.get_or_create_stream(device_id)
        stream.acknowledge_received(packet.seq)

        # Checagem de Idempotência
        if packet.idempotency_key and self.idempotency.is_duplicate(packet.idempotency_key):
            cached = self.idempotency.get_cached_result(packet.idempotency_key) or {}
            logger.info("Comando duplicado recebido (%s). Retornando resposta em cache.", packet.idempotency_key)
            return stream.create_packet(
                msg_type=SyncMessageType.ACK,
                payload={"idempotent": True, "result": cached},
                idempotency_key=f"ack-{packet.idempotency_key}",
            )

        # Processamento por tipo
        response_payload: dict[str, Any] = {"status": "ok"}
        if packet.type == SyncMessageType.MESSAGE:
            if on_message:
                response_payload = on_message(packet.payload)
            else:
                response_payload = {"status": "received", "echo": packet.payload.get("text", "")}

        elif packet.type == SyncMessageType.TASK:
            if on_task:
                response_payload = on_task(packet.payload)
            else:
                response_payload = {"status": "queued", "task_id": packet.payload.get("task_id", "")}

        elif packet.type == SyncMessageType.RESUME:
            # Retomada de sessão
            last_received = int(packet.payload.get("last_seq", 0))
            replays = stream.get_replay_packets(last_received)
            return stream.create_packet(
                msg_type=SyncMessageType.RESUME_ACK,
                payload={
                    "resumed": True,
                    "replayed_count": len(replays),
                    "replayed_packets": [p.to_dict() for p in replays],
                },
                idempotency_key=packet.idempotency_key,
            )

        elif packet.type == SyncMessageType.HEARTBEAT:
            response_payload = {"heartbeat": "pong", "time": time.time()}

        elif packet.type == SyncMessageType.FILE_START:
            # Início de recebimento de arquivo em pedaços
            transfer_id = packet.payload.get("transfer_id", secrets.token_hex(8))
            stream.active_file_transfers[transfer_id] = {
                "file_name": packet.payload.get("file_name", "upload.bin"),
                "file_size": packet.payload.get("file_size", 0),
                "expected_sha256": packet.payload.get("sha256", ""),
                "chunks": {},
            }
            response_payload = {"status": "transfer_started", "transfer_id": transfer_id}

        elif packet.type == SyncMessageType.FILE_CHUNK:
            transfer_id = packet.payload.get("transfer_id", "")
            chunk_idx = packet.payload.get("chunk_idx", 0)
            chunk_data_b64 = packet.payload.get("data", "")
            transfer = stream.active_file_transfers.get(transfer_id)
            if transfer:
                transfer["chunks"][chunk_idx] = _b64d(chunk_data_b64)
                response_payload = {"status": "chunk_received", "chunk_idx": chunk_idx}
            else:
                response_payload = {"status": "error", "message": "Transferência inexistente"}

        # Registra execução para garantir idempotência
        if packet.idempotency_key:
            self.idempotency.record_result(packet.idempotency_key, response_payload)

        return stream.create_packet(
            msg_type=SyncMessageType.ACK,
            payload=response_payload,
            idempotency_key=f"ack-{packet.idempotency_key}" if packet.idempotency_key else None,
        )

    def close(self) -> None:
        self.device_registry.close()


class NetworkDeviceManager:
    """Gerenciador de dispositivos emparelhados e controle granular de sessões."""

    def __init__(self, db_path: Path = DEFAULT_DB):
        self.registry = DeviceRegistry(db_path)
        self.sessions = SessionRegistry(self.registry)

    def list_devices_detailed(self) -> list[dict[str, Any]]:
        """Lista dispositivos registrados com detalhes de status e sessão."""
        rows = self.registry.list_devices()
        result = []
        for dev_id, name, status in rows:
            is_active, expiry = self.registry.session_state(dev_id)
            result.append({
                "device_id": dev_id,
                "name": name,
                "status": status,
                "session_active": is_active,
                "session_expiry": expiry,
            })
        return result

    def approve_device(self, device_id: str) -> bool:
        """Aprova dispositivo pendente."""
        return self.registry.update_device(device_id, "authorized")

    def revoke_device_access(self, device_id: str, permanent: bool = False) -> bool:
        """Revoga o dispositivo e encerra imediatamente todas as suas sessões."""
        self.sessions.revoke_device_sessions(device_id)
        return self.registry.revoke_device(device_id, permanent=permanent)

    def revoke_session_only(self, device_id: str) -> int:
        """Revoga as sessões ativas do dispositivo sem despareá-lo."""
        return self.sessions.revoke_device_sessions(device_id)

    def is_lockdown(self) -> bool:
        return self.registry.lockdown()

    def set_lockdown(self, enabled: bool) -> None:
        self.registry.set_lockdown(enabled)

    def close(self) -> None:
        self.registry.close()

"""Servidor e Roteador de Relay Cego para o MARK Link."""

from __future__ import annotations

import logging
import socket
import struct
import threading
from collections import defaultdict, deque
from typing import Deque

from .envelope import EncryptedEnvelope, RELAY_MAGIC

logger = logging.getLogger("mark.link.relay.server")


class BlindRelayRouter:
    """Roteia envelopes cifrados entre dispositivos conectados sem acesso ao texto claro."""

    def __init__(self, max_queue_per_peer: int = 64) -> None:
        self.max_queue = max_queue_per_peer
        self.queues: dict[str, Deque[EncryptedEnvelope]] = defaultdict(lambda: deque(maxlen=self.max_queue))
        self.lock = threading.Lock()

    def register_peer(self, peer_id: str) -> None:
        with self.lock:
            _ = self.queues[peer_id]

    def unregister_peer(self, peer_id: str) -> None:
        with self.lock:
            self.queues.pop(peer_id, None)

    def route_envelope(self, envelope: EncryptedEnvelope) -> bool:
        with self.lock:
            if envelope.target_id not in self.queues:
                logger.debug("Relay: destinatário '%s' não encontrado", envelope.target_id)
                return False
            queue = self.queues[envelope.target_id]
            if len(queue) >= self.max_queue:
                logger.warning("Relay: fila cheia para '%s' (backpressure: descartando envelope)", envelope.target_id)
                return False
            queue.append(envelope)
            return True

    def drain_for(self, peer_id: str) -> list[EncryptedEnvelope]:
        with self.lock:
            queue = self.queues.get(peer_id)
            if not queue:
                return []
            items = list(queue)
            queue.clear()
            return items


class RelayServer:
    """Servidor de Relay em rede TCP (Blind Relay Server) compatível com o protocolo Rust."""

    def __init__(self, host: str = "127.0.0.1", port: int = 8767) -> None:
        self.host = host
        self.port = port
        self.server_sock: socket.socket | None = None
        self.running = False
        self.router = BlindRelayRouter()
        self.active_sockets: dict[str, socket.socket] = {}
        self.lock = threading.Lock()
        self.accept_thread: threading.Thread | None = None

    def start(self) -> None:
        self.server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_sock.bind((self.host, self.port))
        self.server_sock.listen(16)
        self.running = True
        self.accept_thread = threading.Thread(target=self._accept_loop, daemon=True, name="RelayServerAccept")
        self.accept_thread.start()
        logger.info("MARK Link Relay Server ativo em %s:%d", self.host, self.port)

    @staticmethod
    def _read_exact(sock: socket.socket, n: int) -> bytes:
        buf = bytearray()
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionResetError("Conexão encerrada pelo cliente de relay")
            buf.extend(chunk)
        return bytes(buf)

    @classmethod
    def _read_envelope(cls, sock: socket.socket) -> EncryptedEnvelope:
        magic = cls._read_exact(sock, 4)
        if magic != RELAY_MAGIC:
            raise ValueError(f"Magic de relay inválido: {magic!r}")
        target_len = cls._read_exact(sock, 1)[0]
        target_id = cls._read_exact(sock, target_len).decode("utf-8")
        source_len = cls._read_exact(sock, 1)[0]
        source_id = cls._read_exact(sock, source_len).decode("utf-8")
        payload_len = struct.unpack("!L", cls._read_exact(sock, 4))[0]
        payload = cls._read_exact(sock, payload_len)
        return EncryptedEnvelope(target_id=target_id, source_id=source_id, opaque_payload=payload)

    def _accept_loop(self) -> None:
        while self.running and self.server_sock:
            try:
                client_sock, addr = self.server_sock.accept()
            except Exception:
                break
            t = threading.Thread(
                target=self._handle_peer,
                args=(client_sock, addr),
                daemon=True,
                name=f"RelayPeer-{addr[0]}:{addr[1]}",
            )
            t.start()

    def _handle_peer(self, sock: socket.socket, addr: tuple[str, int]) -> None:
        sock.settimeout(60.0)
        device_id: str | None = None
        try:
            # 1. Lê mensagem de registro: "REGISTER <device_id>\n"
            line = bytearray()
            while not line.endswith(b"\n") and len(line) < 256:
                b = sock.recv(1)
                if not b:
                    return
                line.extend(b)

            parts = line.decode("utf-8").strip().split()
            if len(parts) != 2 or parts[0] != "REGISTER":
                return
            device_id = parts[1]

            with self.lock:
                self.active_sockets[device_id] = sock
                self.router.register_peer(device_id)

            # Drena envelopes pendentes acumulados para este peer
            pending = self.router.drain_for(device_id)
            for env in pending:
                sock.sendall(env.serialize())

            # Loop de leitura e roteamento de envelopes
            while self.running:
                envelope = self._read_envelope(sock)
                with self.lock:
                    target_sock = self.active_sockets.get(envelope.target_id)
                    if target_sock:
                        try:
                            target_sock.sendall(envelope.serialize())
                        except Exception:
                            self.router.route_envelope(envelope)
                    else:
                        self.router.route_envelope(envelope)
        except Exception as exc:
            logger.debug("Relay peer %s finalizado: %s", device_id or addr, exc)
        finally:
            if device_id:
                with self.lock:
                    self.active_sockets.pop(device_id, None)
                    self.router.unregister_peer(device_id)
            try:
                sock.close()
            except Exception:
                pass

    def stop(self) -> None:
        self.running = False
        if self.server_sock:
            try:
                self.server_sock.close()
            except Exception:
                pass
        with self.lock:
            for s in list(self.active_sockets.values()):
                try:
                    s.close()
                except Exception:
                    pass
            self.active_sockets.clear()

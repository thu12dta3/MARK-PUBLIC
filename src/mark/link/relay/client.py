"""Cliente de Relay Cego do MARK Link."""

from __future__ import annotations

import logging
import socket
import struct
import threading
from typing import Callable

from .envelope import EncryptedEnvelope, RELAY_MAGIC
from .server import BlindRelayRouter

logger = logging.getLogger("mark.link.relay.client")


class RelayClient:
    """Cliente que encaminha e recebe frames através de um servidor ou roteador de relay."""

    def __init__(
        self,
        my_device_id: str,
        router: BlindRelayRouter | None = None,
        server_addr: tuple[str, int] | None = None,
    ) -> None:
        self.my_device_id = my_device_id
        self.router = router
        self.server_addr = server_addr
        self.sock: socket.socket | None = None
        self.incoming_queue: list[bytes] = []
        self.lock = threading.Lock()
        self.running = False
        self.reader_thread: threading.Thread | None = None

        if self.server_addr:
            self.connect(self.server_addr[0], self.server_addr[1])
        elif self.router:
            self.router.register_peer(my_device_id)

    def connect(self, host: str, port: int) -> None:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.settimeout(10.0)
        self.sock.connect((host, port))
        reg_msg = f"REGISTER {self.my_device_id}\n".encode("utf-8")
        self.sock.sendall(reg_msg)
        self.running = True
        self.reader_thread = threading.Thread(target=self._recv_loop, daemon=True, name="RelayClientRecv")
        self.reader_thread.start()

    def _read_exact(self, n: int) -> bytes:
        if not self.sock:
            raise ConnectionResetError("Socket fechado")
        buf = bytearray()
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionResetError("Conexão encerrada pelo servidor de relay")
            buf.extend(chunk)
        return bytes(buf)

    def _recv_loop(self) -> None:
        while self.running and self.sock:
            try:
                magic = self._read_exact(4)
                if magic != RELAY_MAGIC:
                    break
                target_len = self._read_exact(1)[0]
                target_id = self._read_exact(target_len).decode("utf-8")
                source_len = self._read_exact(1)[0]
                source_id = self._read_exact(source_len).decode("utf-8")
                payload_len = struct.unpack("!L", self._read_exact(4))[0]
                payload = self._read_exact(payload_len)
                with self.lock:
                    self.incoming_queue.append(payload)
            except Exception:
                break

    def send_envelope(self, target_id: str, opaque_frame: bytes) -> bool:
        env = EncryptedEnvelope(target_id=target_id, source_id=self.my_device_id, opaque_payload=opaque_frame)
        if self.sock and self.running:
            try:
                self.sock.sendall(env.serialize())
                return True
            except Exception as exc:
                logger.warning("Falha ao enviar envelope via socket relay: %s", exc)
                return False
        if self.router:
            return self.router.route_envelope(env)
        return False

    def poll_incoming(self) -> list[bytes]:
        if self.sock and self.running:
            with self.lock:
                items = list(self.incoming_queue)
                self.incoming_queue.clear()
                return items
        if self.router:
            envelopes = self.router.drain_for(self.my_device_id)
            return [env.opaque_payload for env in envelopes]
        return []

    def close(self) -> None:
        self.running = False
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None
        if self.router:
            self.router.unregister_peer(self.my_device_id)

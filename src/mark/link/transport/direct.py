"""Servidor e Cliente de Transporte Direto via Sockets para o MARK Link."""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import socket
import struct
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from ...device_auth import b64, unb64
from ..identity.device import LinkDeviceManager, canonical_handshake_init, canonical_handshake_response
from .adapter import LinkTransportAdapter
from .framing import (
    HEADER_SIZE,
    DirectionalSecureChannel,
    PacketHeader,
    PacketType,
    SecureChannel,
)

logger = logging.getLogger("mark.link.transport.direct")


class DirectConnectionHandler:
    """Gerencia a sessão criptografada bidirecional de um socket direto."""

    def __init__(
        self,
        sock: socket.socket,
        channel: DirectionalSecureChannel | SecureChannel,
        device_id: str,
        adapter: LinkTransportAdapter,
    ) -> None:
        self.sock = sock
        self.channel = channel
        self.device_id = device_id
        self.adapter = adapter
        self.running = True
        self.lock = threading.Lock()

    def send_packet(self, packet_type: PacketType, payload: bytes) -> None:
        with self.lock:
            frame = self.channel.seal_packet(packet_type, payload)
            self.sock.sendall(frame)

    def read_exact(self, n: int) -> bytes:
        data = bytearray()
        while len(data) < n:
            chunk = self.sock.recv(n - len(data))
            if not chunk:
                raise ConnectionResetError("Conexão encerrada pelo par remoto")
            data.extend(chunk)
        return bytes(data)

    def process_loop(self) -> None:
        self.sock.settimeout(60.0)
        try:
            while self.running:
                header_bytes = self.read_exact(HEADER_SIZE)
                header = PacketHeader.decode(header_bytes)
                payload_ciphertext = self.read_exact(header.length)

                full_frame = header_bytes + payload_ciphertext
                hdr, plaintext = self.channel.unseal_packet(full_frame)

                if hdr.packet_type == PacketType.PING:
                    self.send_packet(PacketType.PONG, b"pong")
                elif hdr.packet_type == PacketType.DATA:
                    try:
                        req_data = json.loads(plaintext.decode("utf-8"))
                        method = req_data.get("method", "GET")
                        path = req_data.get("path", "/health")
                        headers = req_data.get("headers", {})
                        body = req_data.get("body", "").encode("utf-8")
                        request_id = (
                            req_data.get("request_id")
                            or headers.get("X-Request-ID")
                            or headers.get("x-request-id")
                        )

                        status, resp_headers, resp_body = self.adapter.forward_request(
                            method, path, headers, body, self.device_id, request_id=request_id
                        )
                        is_replay = (
                            resp_headers.get("X-Mark-Idempotent-Replay") == "true"
                            or resp_headers.get("x-mark-idempotent-replay") == "true"
                        )
                        resp_payload = json.dumps({
                            "status": status,
                            "headers": resp_headers,
                            "body": resp_body.decode("utf-8", errors="replace"),
                            "idempotent_replay": is_replay,
                        }).encode("utf-8")
                        self.send_packet(PacketType.DATA, resp_payload)
                    except Exception as exc:
                        logger.warning("Erro ao processar pacote DATA: %s", exc)
                        status_code = 403 if isinstance(exc, PermissionError) else 500
                        err_bytes = json.dumps({
                            "status": status_code,
                            "error": str(exc),
                        }).encode("utf-8")
                        self.send_packet(PacketType.ERROR, err_bytes)
                elif hdr.packet_type == PacketType.CLOSE:
                    break
        except (ConnectionResetError, BrokenPipeError, socket.timeout) as exc:
            logger.debug("Conexão finalizada: %s", exc)
        finally:
            self.close()

    def close(self) -> None:
        self.running = False
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except Exception:
            pass
        try:
            self.sock.close()
        except Exception:
            pass


class DirectListener:
    """Escuta conexões TCP diretas locais do MARK Link com autenticação mútua obrigatória."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8766,
        adapter: LinkTransportAdapter | None = None,
        key_resolver: Callable[[str], bytes | None] | None = None,
        device_manager: LinkDeviceManager | None = None,
        server_private_key: ec.EllipticCurvePrivateKey | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.adapter = adapter or LinkTransportAdapter()
        self.key_resolver = key_resolver
        self.device_manager = device_manager or (LinkDeviceManager() if key_resolver is None else None)
        self.server_private_key = server_private_key or ec.generate_private_key(ec.SECP256R1())
        self.server_sock: socket.socket | None = None
        self.running = False
        self.thread: threading.Thread | None = None
        self.active_handlers: list[DirectConnectionHandler] = []
        self.lock = threading.Lock()

        # Vincula revogação em tempo real para derrubar sessões ativas imediatamente
        if self.device_manager and hasattr(self.device_manager, "registry"):
            self.device_manager.registry.register_revocation_listener(self.revoke_device_sessions)

    def revoke_device_sessions(self, device_id: str) -> int:
        """Derruba imediatamente os sockets e cancela sessões ativas do dispositivo revogado."""
        with self.lock:
            affected = [h for h in self.active_handlers if h.device_id == device_id]
            for h in affected:
                h.close()
            self.active_handlers = [h for h in self.active_handlers if h.device_id != device_id]
            return len(affected)

    @property
    def server_public_der(self) -> bytes:
        """Chave pública DER do hub/servidor local."""
        return self.server_private_key.public_key().public_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )

    def start(self) -> None:
        if not self.key_resolver and not self.device_manager:
            raise ValueError("DirectListener requer key_resolver ou LinkDeviceManager válido para autenticação criptográfica.")
        self.server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_sock.bind((self.host, self.port))
        self.server_sock.listen(16)
        self.running = True
        self.thread = threading.Thread(target=self._accept_loop, daemon=True, name="MarkLinkListener")
        self.thread.start()
        logger.info("MARK Link Listener ativo em %s:%d", self.host, self.port)

    @staticmethod
    def _read_exact_sock(sock: socket.socket, n: int) -> bytes:
        data = bytearray()
        while len(data) < n:
            chunk = sock.recv(n - len(data))
            if not chunk:
                raise ConnectionResetError("Conexão encerrada pelo par remoto")
            data.extend(chunk)
        return bytes(data)

    def _accept_loop(self) -> None:
        while self.running and self.server_sock:
            try:
                client_sock, addr = self.server_sock.accept()
            except Exception:
                break

            with self.lock:
                self.active_handlers = [h for h in self.active_handlers if h.running]
                if len(self.active_handlers) >= 16:
                    logger.warning("Limite de conexões simultâneas atingido (%d)", len(self.active_handlers))
                    try:
                        client_sock.close()
                    except Exception:
                        pass
                    continue

            t = threading.Thread(
                target=self._handle_new_connection,
                args=(client_sock, addr),
                daemon=True,
                name=f"MarkLinkConn-{addr[0]}:{addr[1]}",
            )
            t.start()

    def _handle_new_connection(self, client_sock: socket.socket, addr: tuple[str, int]) -> None:
        client_sock.settimeout(15.0)
        try:
            # 1. Lê o cabeçalho do frame inicial (HandshakeInit)
            header_bytes = self._read_exact_sock(client_sock, HEADER_SIZE)
            header = PacketHeader.decode(header_bytes)
            if header.packet_type != PacketType.HANDSHAKE_INIT:
                logger.warning("Conexão de %s rejeitada: primeiro pacote não é HandshakeInit (%s)", addr, header.packet_type)
                client_sock.close()
                return

            init_payload_bytes = self._read_exact_sock(client_sock, header.length)
            init_data = json.loads(init_payload_bytes.decode("utf-8"))

            device_id = init_data.get("device_id")
            ephemeral_pk_der_b64 = init_data.get("ephemeral_pk_der_b64")
            client_nonce_b64 = init_data.get("nonce_b64")
            timestamp = int(init_data.get("timestamp", 0))
            signature_b64 = init_data.get("signature_b64")

            if not all((device_id, ephemeral_pk_der_b64, client_nonce_b64, timestamp, signature_b64)):
                logger.warning("HandshakeInit incompleto de %s", addr)
                client_sock.close()
                return

            # Validação anti-replay do timestamp (|now - timestamp| <= 60s)
            if abs(time.time() - timestamp) > 60:
                logger.warning("HandshakeInit com timestamp expirado ou skew excessivo (%d)", timestamp)
                client_sock.close()
                return

            # Resolução da chave pública e autorização do dispositivo
            client_pub_der: bytes | None = None
            if self.key_resolver:
                client_pub_der = self.key_resolver(device_id)
            elif self.device_manager:
                if not self.device_manager.is_authorized(device_id):
                    logger.warning("Dispositivo não autorizado ou revogado: %s", device_id)
                    client_sock.close()
                    return
                client_pub_der = self.device_manager.get_public_der(device_id)

            if not client_pub_der:
                logger.warning("Chave pública não encontrada para dispositivo: %s", device_id)
                client_sock.close()
                return

            # Verificação da assinatura P-256 ECDSA
            canonical_msg = canonical_handshake_init(
                device_id=device_id,
                ephemeral_pk_der_b64=ephemeral_pk_der_b64,
                nonce_b64=client_nonce_b64,
                timestamp=timestamp,
            )
            client_pub_key = serialization.load_der_public_key(client_pub_der)
            if not isinstance(client_pub_key, ec.EllipticCurvePublicKey):
                client_sock.close()
                return
            client_pub_key.verify(unb64(signature_b64), canonical_msg, ec.ECDSA(hashes.SHA256()))

            # Servidor gera seu par efêmero ECDH P-256
            server_eph_priv = ec.generate_private_key(ec.SECP256R1())
            server_eph_pub_der = server_eph_priv.public_key().public_bytes(
                encoding=serialization.Encoding.DER,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            )
            server_eph_pub_b64 = b64(server_eph_pub_der)
            server_nonce = secrets.token_bytes(32)
            server_nonce_b64 = b64(server_nonce)

            # Deriva segredo compartilhado ECDH
            client_eph_pub = serialization.load_der_public_key(unb64(ephemeral_pk_der_b64))
            shared_secret = server_eph_priv.exchange(ec.ECDH(), client_eph_pub)

            now_sec = int(time.time())

            # Resposta canônica assinada vinculando mensagem Init e parâmetros do servidor
            init_hash = hashlib.sha256(canonical_msg).digest()
            init_hash_b64 = b64(init_hash)

            canonical_resp = canonical_handshake_response(
                server_id="mark-pc-core",
                ephemeral_pk_der_b64=server_eph_pub_b64,
                client_nonce_b64=client_nonce_b64,
                server_nonce_b64=server_nonce_b64,
                timestamp=now_sec,
                init_hash_b64=init_hash_b64,
            )
            server_sig = self.server_private_key.sign(canonical_resp, ec.ECDSA(hashes.SHA256()))
            server_sig_b64 = b64(server_sig)

            # Session Binding: injeta transcript completo no HKDF
            transcript_hash = hashlib.sha256(canonical_msg + b"\n" + canonical_resp).digest()

            # Deriva canal bidirecional (is_server = True) com Session Binding
            client_nonce = unb64(client_nonce_b64)
            channel = DirectionalSecureChannel.derive(
                shared_secret=shared_secret,
                client_nonce=client_nonce,
                server_nonce=server_nonce,
                is_server=True,
                transcript_hash=transcript_hash,
            )

            # Responde com HANDSHAKE_RESPONSE incluindo assinatura
            resp_payload = json.dumps({
                "server_id": "mark-pc-core",
                "ephemeral_pk_der_b64": server_eph_pub_b64,
                "nonce_b64": server_nonce_b64,
                "timestamp": now_sec,
                "status": "authorized",
                "signature_b64": server_sig_b64,
            }).encode("utf-8")

            resp_hdr = PacketHeader(
                packet_type=PacketType.HANDSHAKE_RESPONSE,
                flags=0,
                sequence=0,
                length=len(resp_payload),
            ).encode()
            client_sock.sendall(resp_hdr + resp_payload)

            handler = DirectConnectionHandler(client_sock, channel, device_id, self.adapter)
            with self.lock:
                old_handlers = [h for h in self.active_handlers if h.device_id == device_id and h.running]
                for old in old_handlers:
                    logger.info("Encerrando conexão zumbi anterior para %s para prevenir split-brain", device_id)
                    old.close()
                self.active_handlers = [h for h in self.active_handlers if h.running]
                self.active_handlers.append(handler)
            try:
                handler.process_loop()
            finally:
                with self.lock:
                    if handler in self.active_handlers:
                        self.active_handlers.remove(handler)
        except (ConnectionResetError, BrokenPipeError, socket.timeout) as exc:
            logger.debug("Conexão finalizada durante sessão com %s: %s", addr, exc)
            try:
                client_sock.close()
            except Exception:
                pass
        except Exception as exc:
            logger.warning("Falha durante handshake/sessão com %s: %s", addr, exc)
            try:
                client_sock.close()
            except Exception:
                pass

    def stop(self) -> None:
        self.running = False
        if self.device_manager and hasattr(self.device_manager, "registry"):
            self.device_manager.registry.unregister_revocation_listener(self.revoke_device_sessions)
        if self.server_sock:
            try:
                self.server_sock.close()
            except Exception:
                pass
        with self.lock:
            for h in list(self.active_handlers):
                h.close()
            self.active_handlers.clear()


class DirectClient:
    """Cliente direto do MARK Link que estabelece conexão autenticada via TCP com autenticação mútua."""

    def __init__(
        self,
        device_id: str,
        private_key: ec.EllipticCurvePrivateKey,
        host: str = "127.0.0.1",
        port: int = 8766,
        server_public_key: ec.EllipticCurvePublicKey | bytes | None = None,
    ) -> None:
        self.device_id = device_id
        self.private_key = private_key
        self.host = host
        self.port = port
        self.server_public_key = server_public_key
        self.sock: socket.socket | None = None
        self.channel: DirectionalSecureChannel | None = None
        self.lock = threading.Lock()
        self.last_activity: float = time.time()
        self.idle_threshold_secs: float = 30.0
        self.active_ping_secs: float = 5.0
        self.idle_ping_secs: float = 45.0

    def record_activity(self) -> None:
        """Registra atividade recente de dados para otimização de keepalive adaptativo."""
        self.last_activity = time.time()

    def current_ping_interval(self) -> float:
        """Calcula o intervalo de ping ideal: alta frequência (ativo) vs repouso (economia de bateria)."""
        elapsed = time.time() - self.last_activity
        if elapsed >= self.idle_threshold_secs:
            return self.idle_ping_secs
        return self.active_ping_secs

    def connect(self, timeout: float = 10.0) -> None:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.settimeout(timeout)
        self.sock.connect((self.host, self.port))

        eph_priv = ec.generate_private_key(ec.SECP256R1())
        eph_pub_der = eph_priv.public_key().public_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        eph_pub_b64 = b64(eph_pub_der)
        client_nonce = secrets.token_bytes(32)
        client_nonce_b64 = b64(client_nonce)
        now = int(time.time())

        canonical_msg = canonical_handshake_init(
            device_id=self.device_id,
            ephemeral_pk_der_b64=eph_pub_b64,
            nonce_b64=client_nonce_b64,
            timestamp=now,
        )
        sig = self.private_key.sign(canonical_msg, ec.ECDSA(hashes.SHA256()))
        sig_b64 = b64(sig)

        init_payload = json.dumps({
            "device_id": self.device_id,
            "ephemeral_pk_der_b64": eph_pub_b64,
            "nonce_b64": client_nonce_b64,
            "timestamp": now,
            "signature_b64": sig_b64,
        }).encode("utf-8")

        hdr = PacketHeader(
            packet_type=PacketType.HANDSHAKE_INIT,
            flags=0,
            sequence=0,
            length=len(init_payload),
        ).encode()
        self.sock.sendall(hdr + init_payload)

        # Lê HANDSHAKE_RESPONSE
        hdr_bytes = self._read_exact(HEADER_SIZE)
        resp_hdr = PacketHeader.decode(hdr_bytes)
        if resp_hdr.packet_type != PacketType.HANDSHAKE_RESPONSE:
            raise ConnectionError(f"Esperado HANDSHAKE_RESPONSE, recebido {resp_hdr.packet_type}")

        resp_data = self._read_exact(resp_hdr.length)
        resp_json = json.loads(resp_data.decode("utf-8"))
        if resp_json.get("status") != "authorized":
            raise PermissionError(f"Handshake rejeitado pelo servidor: {resp_json}")

        resp_timestamp = int(resp_json.get("timestamp", 0))
        if abs(time.time() - resp_timestamp) > 60:
            raise PermissionError("HandshakeResponse com timestamp expirado ou skew excessivo")

        server_id = resp_json.get("server_id", "mark-pc-core")
        server_eph_pub_b64 = resp_json.get("ephemeral_pk_der_b64", "")
        server_nonce_b64 = resp_json.get("nonce_b64", "")
        server_sig_b64 = resp_json.get("signature_b64", "")

        init_hash_b64 = b64(hashlib.sha256(canonical_msg).digest())
        canonical_resp = canonical_handshake_response(
            server_id=server_id,
            ephemeral_pk_der_b64=server_eph_pub_b64,
            client_nonce_b64=client_nonce_b64,
            server_nonce_b64=server_nonce_b64,
            timestamp=resp_timestamp,
            init_hash_b64=init_hash_b64,
        )

        # Autenticação mútua: validação da assinatura criptográfica do servidor
        if self.server_public_key is not None:
            if not server_sig_b64:
                raise PermissionError("Assinatura do servidor ausente no HandshakeResponse")
            srv_pub = (
                serialization.load_der_public_key(self.server_public_key)
                if isinstance(self.server_public_key, (bytes, bytearray))
                else self.server_public_key
            )
            try:
                srv_pub.verify(unb64(server_sig_b64), canonical_resp, ec.ECDSA(hashes.SHA256()))
            except Exception as exc:
                raise PermissionError("Assinatura do servidor inválida ou não autorizada") from exc

        server_eph_der = unb64(server_eph_pub_b64)
        server_eph_pub = serialization.load_der_public_key(server_eph_der)
        server_nonce = unb64(server_nonce_b64)

        shared_secret = eph_priv.exchange(ec.ECDH(), server_eph_pub)

        # Session Binding: vincula transcript completo ao cálculo da chave de sessão HKDF
        transcript_hash = hashlib.sha256(canonical_msg + b"\n" + canonical_resp).digest()
        self.channel = DirectionalSecureChannel.derive(
            shared_secret=shared_secret,
            client_nonce=client_nonce,
            server_nonce=server_nonce,
            is_server=False,
            transcript_hash=transcript_hash,
        )

    def _read_exact(self, n: int) -> bytes:
        data = bytearray()
        while len(data) < n:
            chunk = self.sock.recv(n - len(data))
            if not chunk:
                raise ConnectionResetError("Conexão encerrada pelo servidor")
            data.extend(chunk)
        return bytes(data)

    def send_request(
        self,
        method: str,
        path: str,
        headers: dict[str, str] | None = None,
        body: bytes = b"",
        request_id: str | None = None,
    ) -> dict[str, Any]:
        if not self.channel or not self.sock:
            raise ConnectionError("Cliente não conectado")
        self.record_activity()
        req_headers = dict(headers or {})
        if request_id:
            req_headers["X-Request-ID"] = request_id

        req_dict: dict[str, Any] = {
            "method": method,
            "path": path,
            "headers": req_headers,
            "body": body.decode("utf-8", errors="replace"),
        }
        if request_id:
            req_dict["request_id"] = request_id

        req_payload = json.dumps(req_dict).encode("utf-8")
        with self.lock:
            frame = self.channel.seal_packet(PacketType.DATA, req_payload)
            self.sock.sendall(frame)

            hdr_bytes = self._read_exact(HEADER_SIZE)
            hdr = PacketHeader.decode(hdr_bytes)
            cipher = self._read_exact(hdr.length)
            resp_hdr, plaintext = self.channel.unseal_packet(hdr_bytes + cipher)
            self.record_activity()
            return json.loads(plaintext.decode("utf-8"))

    def ping(self) -> bool:
        if not self.channel or not self.sock:
            raise ConnectionError("Cliente não conectado")
        self.record_activity()
        with self.lock:
            frame = self.channel.seal_packet(PacketType.PING, b"ping")
            self.sock.sendall(frame)

            hdr_bytes = self._read_exact(HEADER_SIZE)
            hdr = PacketHeader.decode(hdr_bytes)
            cipher = self._read_exact(hdr.length)
            resp_hdr, _ = self.channel.unseal_packet(hdr_bytes + cipher)
            self.record_activity()
            return resp_hdr.packet_type == PacketType.PONG

    def close(self) -> None:
        if self.sock:
            try:
                if self.channel:
                    frame = self.channel.seal_packet(PacketType.CLOSE, b"close")
                    self.sock.sendall(frame)
            except Exception:
                pass
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None
            self.channel = None

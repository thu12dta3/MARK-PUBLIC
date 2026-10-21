"""Testes de Segurança: Endurecimento do Handshake, Autenticação Mútua e Session Binding.

Fase RED do TDD:
Estes testes comprovam as vulnerabilidades do handshake anterior:
1. Servidor fraudulento (rogue server) sem a chave privada do hub conseguia completar o handshake.
2. Man-In-The-Middle (MITM) podia adulterar parâmetros em Init/Response sem invalidar a derivação de chaves.
3. Chaves de sessão (HKDF) não estavam amarradas ao transcript completo do aperto de mão (Session Binding).
"""

import hashlib
import json
import socket
import threading
import time
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from src.mark.device_auth import DeviceRegistry, b64, unb64
from src.mark.link.identity.device import LinkDeviceManager, canonical_handshake_init, canonical_handshake_response
from src.mark.link.transport.direct import DirectClient, DirectListener
from src.mark.link.transport.framing import (
    HEADER_SIZE,
    DirectionalSecureChannel,
    PacketHeader,
    PacketType,
)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_rogue_server_handshake_rejected_due_to_invalid_or_missing_signature(tmp_path):
    """Prova RED 1: Um servidor fraudulento que tenta responder sem a assinatura correta

    da chave privada do hub DEVE ser categoricamente rejeitado pelo cliente.
    """
    db_path = tmp_path / "rogue_server_test.db"
    registry = DeviceRegistry(db_path)
    dev_mgr = LinkDeviceManager(registry)

    # Chave legítima do Hub/Servidor autorizada
    hub_key = ec.generate_private_key(ec.SECP256R1())
    hub_pub_der = hub_key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    # Chave e registro do cliente
    client_key = ec.generate_private_key(ec.SECP256R1())
    client_pub_der = client_key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    code = registry.open_enrollment(validity_seconds=120)
    proof = client_key.sign(b"MARK-PAIR-V1\n" + code.encode("utf-8"), ec.ECDSA(hashes.SHA256()))
    device_id = registry.enroll(code, "Aparelho Cliente Teste", b64(client_pub_der), b64(proof))
    registry.update_device(device_id, "authorized")

    # Inicia um servidor rogue/fraudulento que tem uma chave diferente (não possui a chave privada do hub)
    rogue_port = _free_port()
    rogue_server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    rogue_server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    rogue_server_sock.bind(("127.0.0.1", rogue_port))
    rogue_server_sock.listen(1)

    def _rogue_worker():
        try:
            conn, _ = rogue_server_sock.accept()
            # Lê HandshakeInit
            hdr_bytes = conn.recv(HEADER_SIZE)
            hdr = PacketHeader.decode(hdr_bytes)
            init_bytes = conn.recv(hdr.length)
            init_json = json.loads(init_bytes.decode("utf-8"))

            # Servidor fraudulento gera sua própria chave efêmera
            rogue_eph = ec.generate_private_key(ec.SECP256R1())
            rogue_eph_der = rogue_eph.public_key().public_bytes(
                encoding=serialization.Encoding.DER,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            )

            # O rogue server tenta assinar com uma chave arbitrária de terceiro (falsificação)
            fake_key = ec.generate_private_key(ec.SECP256R1())
            canonical_resp = canonical_handshake_response(
                server_id="mark-pc-core",
                ephemeral_pk_der_b64=b64(rogue_eph_der),
                client_nonce_b64=init_json["nonce_b64"],
                server_nonce_b64=b64(b"\x99" * 32),
                timestamp=int(time.time()),
            )
            fake_sig = fake_key.sign(canonical_resp, ec.ECDSA(hashes.SHA256()))

            resp_payload = json.dumps({
                "server_id": "mark-pc-core",
                "ephemeral_pk_der_b64": b64(rogue_eph_der),
                "nonce_b64": b64(b"\x99" * 32),
                "timestamp": int(time.time()),
                "status": "authorized",
                "signature_b64": b64(fake_sig),
            }).encode("utf-8")

            resp_hdr = PacketHeader(
                packet_type=PacketType.HANDSHAKE_RESPONSE,
                flags=0,
                sequence=0,
                length=len(resp_payload),
            ).encode()
            conn.sendall(resp_hdr + resp_payload)
            conn.close()
        except Exception:
            pass
        finally:
            rogue_server_sock.close()

    t = threading.Thread(target=_rogue_worker, daemon=True)
    t.start()

    # O cliente configurado com a chave pública esperada do Hub DEVE abortar a conexão
    client = DirectClient(
        device_id=device_id,
        private_key=client_key,
        host="127.0.0.1",
        port=rogue_port,
        server_public_key=hub_pub_der,
    )

    with pytest.raises((PermissionError, ConnectionError), match="(?i)(assinatura|signature|não autorizado|rejeitado|inválid)"):
        client.connect(timeout=5.0)


def test_mitm_parameter_tampering_rejected_by_session_binding():
    """Prova RED 2: Se um Man-In-The-Middle alterar qualquer parâmetro do handshake

    (como server_id, nonces ou chave efêmera), o Session Binding deve impedir
    a descriptografia ou a validação de assinatura.
    """
    shared_secret = b"\x77" * 32
    client_nonce = b"\x11" * 32
    server_nonce = b"\x22" * 32

    transcript_legit = hashlib.sha256(b"INIT_MSG_LEGIT||RESP_MSG_LEGIT").digest()
    transcript_tampered = hashlib.sha256(b"INIT_MSG_LEGIT||RESP_MSG_TAMPERED_BY_MITM").digest()

    # Canal cliente deriva com o transcript legítimo
    client_chan = DirectionalSecureChannel.derive(
        shared_secret=shared_secret,
        client_nonce=client_nonce,
        server_nonce=server_nonce,
        is_server=False,
        transcript_hash=transcript_legit,
    )

    # Canal servidor deriva com o transcript adulterado pelo MITM
    server_chan = DirectionalSecureChannel.derive(
        shared_secret=shared_secret,
        client_nonce=client_nonce,
        server_nonce=server_nonce,
        is_server=True,
        transcript_hash=transcript_tampered,
    )

    # As chaves geradas sob transcripts diferentes DEVEM ser matematicamente distintas
    assert client_chan.tx_key != server_chan.rx_key, "Session Binding FALHOU: chaves idênticas apesar de adulteração do transcript!"

    # O servidor NÃO pode conseguir decifrar um frame enviado pelo cliente
    frame = client_chan.seal_packet(PacketType.DATA, b"MENSAGEM-CONFIDENCIAL")
    with pytest.raises(PermissionError, match="Falha de autenticação do frame"):
        server_chan.unseal_packet(frame)


def test_session_binding_hkdf_transcript_isolation():
    """Prova RED 3: HKDF deve injetar o transcript completo como material criptográfico

    garantindo que mesmo segredo compartilhado e nonces produzam chaves diferentes se o transcript mudar.
    """
    shared_secret = b"\x42" * 32
    c_nonce = b"\xaa" * 32
    s_nonce = b"\xbb" * 32

    chan1 = DirectionalSecureChannel.derive(
        shared_secret=shared_secret,
        client_nonce=c_nonce,
        server_nonce=s_nonce,
        is_server=False,
        transcript_hash=b"TRANSCRIPT-SESSION-1",
    )

    chan2 = DirectionalSecureChannel.derive(
        shared_secret=shared_secret,
        client_nonce=c_nonce,
        server_nonce=s_nonce,
        is_server=False,
        transcript_hash=b"TRANSCRIPT-SESSION-2",
    )

    assert chan1.tx_key != chan2.tx_key, "Falha de Session Binding: chan1.tx_key e chan2.tx_key são idênticos!"
    assert chan1.rx_key != chan2.rx_key, "Falha de Session Binding: chan1.rx_key e chan2.rx_key são idênticos!"
    assert chan1.tx_nonce_salt != chan2.tx_nonce_salt, "Falha de Session Binding: salts de nonce são idênticos!"


def test_hardened_mutual_handshake_e2e_and_mitm_tamper_rejection(tmp_path):
    """Valida ponta a ponta que:

    1. Handshake legítimo com autenticação mútua completa funciona perfeitamente (PING -> PONG).
    2. Adulteração por MITM na resposta é detectada e a conexão é abortada.
    """
    db_path = tmp_path / "devices_e2e_sec.db"
    registry = DeviceRegistry(db_path)
    dev_mgr = LinkDeviceManager(registry)

    # Chave do cliente
    client_key = ec.generate_private_key(ec.SECP256R1())
    client_pub_der = client_key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    code = registry.open_enrollment(validity_seconds=120)
    proof = client_key.sign(b"MARK-PAIR-V1\n" + code.encode("utf-8"), ec.ECDSA(hashes.SHA256()))
    device_id = registry.enroll(code, "Galaxy Seguro E2E", b64(client_pub_der), b64(proof))
    registry.update_device(device_id, "authorized")

    # Servidor com chave própria
    server_key = ec.generate_private_key(ec.SECP256R1())
    port = _free_port()
    listener = DirectListener(
        host="127.0.0.1",
        port=port,
        device_manager=dev_mgr,
        server_private_key=server_key,
    )
    listener.start()

    try:
        # 1. Cliente legítimo com chave pública do servidor autorizada conecta com sucesso
        client = DirectClient(
            device_id=device_id,
            private_key=client_key,
            host="127.0.0.1",
            port=port,
            server_public_key=listener.server_public_der,
        )
        client.connect()
        assert client.ping() is True
        client.close()

        # 2. Cliente com chave pública esperada divergente (detecta MITM / rogue server)
        rogue_expected_key = ec.generate_private_key(ec.SECP256R1())
        rogue_expected_der = rogue_expected_key.public_key().public_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        client_strict = DirectClient(
            device_id=device_id,
            private_key=client_key,
            host="127.0.0.1",
            port=port,
            server_public_key=rogue_expected_der,
        )
        with pytest.raises(PermissionError, match="Assinatura do servidor inválida ou não autorizada"):
            client_strict.connect()
        client_strict.close()
    finally:
        listener.stop()

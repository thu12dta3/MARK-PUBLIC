"""Testes de Segurança e Autenticação Mútua (Marco A - Fase RED / GREEN).

Esta suíte de testes valida os 5 pilares de segurança obrigatórios do Marco A:
a) Ataques MITM (substituição de chaves efêmeras num proxy controlado);
b) Replay de mensagens de autenticação;
c) Downgrade de algoritmos (rejeição estrita de algoritmos que não sejam EC P-256 + SHA-256);
d) Tentativa de substituição silenciosa de identidades já aprovadas;
e) Revogação de um dispositivo durante uma sessão ativa (comprovando que reconexões
   e operações em andamento falham instantaneamente).
"""

from __future__ import annotations

import hashlib
import json
import socket
import threading
import time
from pathlib import Path
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa, ed25519

from src.mark.device_auth import DeviceRegistry, b64, unb64
from src.mark.link.identity.device import (
    LinkDeviceManager,
    canonical_handshake_init,
    canonical_handshake_response,
    compute_transcript_hash,
)
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


# ==============================================================================
# a) Ataques MITM (Substituição de chaves efêmeras num proxy controlado)
# ==============================================================================
def test_mitm_ephemeral_key_substitution_in_proxy_fails_handshake(tmp_path: Path):
    """Vetor MITM: Um proxy intermediário intercepta o HandshakeInit e substitui

    a chave efêmera ECDH do cliente por uma chave própria de atacante.
    O servidor deve detectar violação de integridade da assinatura da mensagem
    canônica e recusar categoricamente a conexão.
    """
    db_path = tmp_path / "mitm_test.db"
    registry = DeviceRegistry(db_path)
    hub_key = ec.generate_private_key(ec.SECP256R1())
    hub_pub_der = hub_key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    client_key = ec.generate_private_key(ec.SECP256R1())
    client_pub_der = client_key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    code = registry.open_enrollment(validity_seconds=120)
    proof = client_key.sign(b"MARK-PAIR-V1\n" + code.encode("utf-8"), ec.ECDSA(hashes.SHA256()))
    device_id = registry.enroll(code, "Aparelho Cliente MITM", b64(client_pub_der), b64(proof))
    registry.update_device(device_id, "authorized")

    server_port = _free_port()
    listener = DirectListener(
        host="127.0.0.1",
        port=server_port,
        device_manager=LinkDeviceManager(registry),
        server_private_key=hub_key,
    )
    listener.start()
    time.sleep(0.05)

    # Cria proxy intermediário atacante na porta proxy_port
    proxy_port = _free_port()
    proxy_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    proxy_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    proxy_sock.bind(("127.0.0.1", proxy_port))
    proxy_sock.listen(1)

    attacker_eph = ec.generate_private_key(ec.SECP256R1())
    attacker_eph_der = attacker_eph.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    attacker_eph_b64 = b64(attacker_eph_der)

    def _proxy_worker():
        try:
            client_conn, _ = proxy_sock.accept()
            real_server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            real_server.connect(("127.0.0.1", server_port))

            # Lê frame HandshakeInit do cliente
            hdr_bytes = client_conn.recv(HEADER_SIZE)
            hdr = PacketHeader.decode(hdr_bytes)
            init_payload_bytes = client_conn.recv(hdr.length)
            init_json = json.loads(init_payload_bytes.decode("utf-8"))

            # ATACANTE substitui a chave efêmera pela dele
            init_json["ephemeral_pk_der_b64"] = attacker_eph_b64
            tampered_payload = json.dumps(init_json).encode("utf-8")
            tampered_hdr = PacketHeader(
                packet_type=PacketType.HANDSHAKE_INIT,
                flags=0,
                sequence=0,
                length=len(tampered_payload),
            ).encode()

            real_server.sendall(tampered_hdr + tampered_payload)

            # Tenta repassar resposta do servidor
            resp_hdr_bytes = real_server.recv(HEADER_SIZE)
            if resp_hdr_bytes:
                client_conn.sendall(resp_hdr_bytes)
                resp_hdr = PacketHeader.decode(resp_hdr_bytes)
                client_conn.sendall(real_server.recv(resp_hdr.length))
            real_server.close()
            client_conn.close()
        except Exception:
            pass
        finally:
            proxy_sock.close()

    t = threading.Thread(target=_proxy_worker, daemon=True)
    t.start()

    # O cliente conecta ao proxy
    client = DirectClient(
        device_id=device_id,
        private_key=client_key,
        host="127.0.0.1",
        port=proxy_port,
        server_public_key=hub_pub_der,
    )

    # A conexão DEVE falhar porque o servidor rejeitou a assinatura adulterada
    with pytest.raises((ConnectionError, ConnectionResetError, PermissionError)):
        client.connect(timeout=2.0)

    listener.stop()


# ==============================================================================
# b) Replay de Mensagens de Autenticação
# ==============================================================================
def test_replay_of_handshake_authentication_message_blocked(tmp_path: Path):
    """Vetor Replay: Um atacante grava um frame legítimo de HandshakeInit

    e tenta reenviá-lo mais tarde. O servidor deve bloquear o replay via
    detecção de colisão de nonce ou timestamp expirado.
    """
    db_path = tmp_path / "replay_test.db"
    registry = DeviceRegistry(db_path)
    hub_key = ec.generate_private_key(ec.SECP256R1())

    client_key = ec.generate_private_key(ec.SECP256R1())
    client_pub_der = client_key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    code = registry.open_enrollment(validity_seconds=120)
    proof = client_key.sign(b"MARK-PAIR-V1\n" + code.encode("utf-8"), ec.ECDSA(hashes.SHA256()))
    device_id = registry.enroll(code, "Cliente Replay", b64(client_pub_der), b64(proof))
    registry.update_device(device_id, "authorized")

    server_port = _free_port()
    listener = DirectListener(
        host="127.0.0.1",
        port=server_port,
        device_manager=LinkDeviceManager(registry),
        server_private_key=hub_key,
    )
    listener.start()
    time.sleep(0.05)

    # 1. Conecta legitimamente uma vez
    client1 = DirectClient(
        device_id=device_id,
        private_key=client_key,
        host="127.0.0.1",
        port=server_port,
        server_public_key=listener.server_public_der,
    )
    client1.connect(timeout=3.0)
    assert client1.ping() is True
    client1.close()

    # 2. Constrói payload de HandshakeInit com timestamp forjado no passado (> 60s)
    stale_timestamp = int(time.time()) - 180
    stale_eph = ec.generate_private_key(ec.SECP256R1())
    stale_eph_der = stale_eph.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    stale_nonce_b64 = b64(b"\x33" * 32)
    canonical = canonical_handshake_init(
        device_id=device_id,
        ephemeral_pk_der_b64=b64(stale_eph_der),
        nonce_b64=stale_nonce_b64,
        timestamp=stale_timestamp,
    )
    stale_sig = client_key.sign(canonical, ec.ECDSA(hashes.SHA256()))

    replayed_payload = json.dumps({
        "device_id": device_id,
        "ephemeral_pk_der_b64": b64(stale_eph_der),
        "nonce_b64": stale_nonce_b64,
        "timestamp": stale_timestamp,
        "signature_b64": b64(stale_sig),
    }).encode("utf-8")

    hdr = PacketHeader(
        packet_type=PacketType.HANDSHAKE_INIT,
        flags=0,
        sequence=0,
        length=len(replayed_payload),
    ).encode()

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect(("127.0.0.1", server_port))
    sock.sendall(hdr + replayed_payload)
    sock.settimeout(2.0)

    # O servidor deve fechar imediatamente a conexão sem responder HANDSHAKE_RESPONSE autorizada
    resp = sock.recv(1024)
    assert len(resp) == 0, "Servidor aceitou HandshakeInit com timestamp expirado!"
    sock.close()
    listener.stop()


# ==============================================================================
# c) Downgrade de Algoritmos (Rejeição de Criptografia Fraca / Não-P256)
# ==============================================================================
def test_algorithm_downgrade_rejected(tmp_path: Path):
    """Vetor Downgrade: O atacante tenta registrar um dispositivo ou fazer handshake

    utilizando algoritmos proibidos/rebaixados (RSA ou Ed25519 em vez de EC P-256).
    O sistema deve rejeitar terminantemente qualquer chave que não seja EC P-256.
    """
    db_path = tmp_path / "downgrade_test.db"
    registry = DeviceRegistry(db_path)

    # 1. Tentativa de cadastro com chave RSA (Downgrade de curva elíptica para RSA)
    rsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    rsa_pub_der = rsa_key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    code = registry.open_enrollment(validity_seconds=120)
    proof_rsa = b64(b"fake_rsa_signature")

    with pytest.raises(ValueError, match="(?i)(EC P-256|curva|inválid)"):
        registry.enroll(code, "RSA Client Attack", b64(rsa_pub_der), proof_rsa)

    # 2. Tentativa de cadastro com chave Ed25519 (curva não homologada no MARK Link)
    ed_key = ed25519.Ed25519PrivateKey.generate()
    ed_pub_der = ed_key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    code2 = registry.open_enrollment(validity_seconds=120)
    proof_ed = b64(b"fake_ed_signature")

    with pytest.raises(ValueError, match="(?i)(EC P-256|curva|inválid)"):
        registry.enroll(code2, "Ed25519 Client Attack", b64(ed_pub_der), proof_ed)


# ==============================================================================
# d) Tentativa de Substituição Silenciosa de Identidades Já Aprovadas
# ==============================================================================
def test_silent_identity_replacement_blocked(tmp_path: Path):
    """Vetor Substituição Silenciosa: Um atacante tenta substituir a chave pública

    de um dispositivo já aprovado sem a realização do procedimento formal de rotação
    (que exige assinatura simultânea da chave antiga e da nova).
    """
    db_path = tmp_path / "silent_replace_test.db"
    registry = DeviceRegistry(db_path)

    # 1. Registra e autoriza o dispositivo legítimo
    client_key_original = ec.generate_private_key(ec.SECP256R1())
    pub_der_original = client_key_original.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    code = registry.open_enrollment(validity_seconds=120)
    proof = client_key_original.sign(b"MARK-PAIR-V1\n" + code.encode("utf-8"), ec.ECDSA(hashes.SHA256()))
    device_id = registry.enroll(code, "Aparelho Legítimo", b64(pub_der_original), b64(proof))
    registry.update_device(device_id, "authorized")

    # 2. Um atacante gera uma nova chave
    attacker_key = ec.generate_private_key(ec.SECP256R1())
    attacker_pub_der = attacker_key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    # 3. Tentativa de re-enroll ou injeção com novo código forçando mesmo device_id
    # No protocolo MARK Link, device_id é estritamente b64(sha256(pub_der)).
    # Se o atacante tentar forçar inserção no banco ou rotação sem a chave antiga, DEVE falhar.
    code2 = registry.open_enrollment(validity_seconds=120)
    proof2 = attacker_key.sign(b"MARK-PAIR-V1\n" + code2.encode("utf-8"), ec.ECDSA(hashes.SHA256()))
    new_device_id = registry.enroll(code2, "Aparelho Falso", b64(attacker_pub_der), b64(proof2))

    assert new_device_id != device_id, "Device ID não pode colidir com chave pública diferente"

    # Tentativa de atualizar/sobrescrever dispositivo original sem permissão
    with pytest.raises(Exception):
        # Tenta rotacionar sem a chave original
        fake_nonce = "nonce-fake"
        fake_ts = str(int(time.time()))
        registry.rotate_key(
            device_id=device_id,
            new_public_der_b64=b64(attacker_pub_der),
            timestamp=fake_ts,
            nonce=fake_nonce,
            signature=b64(b"fake_sig_from_attacker"),
            new_proof_b64=b64(b"fake_proof"),
        )


# ==============================================================================
# e) Revogação de um Dispositivo Durante uma Sessão Ativa
# ==============================================================================
def test_revocation_during_active_session_instantly_drops_socket_and_blocks_requests(tmp_path: Path):
    """Vetor Revogação Imediata: Um dispositivo autorizado está conectado e executando operações.

    O administrador revoga o dispositivo no SQLite / DeviceRegistry.
    Instantaneamente:
    1. A sessão ativa em andamento deve ser derrubada e o socket fechado.
    2. Operações subsequentes no canal devem falhar imediatamente.
    3. Novas tentativas de conexão pelo dispositivo revogado devem ser recusadas.
    """
    db_path = tmp_path / "revocation_active_test.db"
    registry = DeviceRegistry(db_path)
    hub_key = ec.generate_private_key(ec.SECP256R1())

    client_key = ec.generate_private_key(ec.SECP256R1())
    client_pub_der = client_key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    code = registry.open_enrollment(validity_seconds=120)
    proof = client_key.sign(b"MARK-PAIR-V1\n" + code.encode("utf-8"), ec.ECDSA(hashes.SHA256()))
    device_id = registry.enroll(code, "Aparelho a Revogar", b64(client_pub_der), b64(proof))
    registry.update_device(device_id, "authorized")

    server_port = _free_port()
    listener = DirectListener(
        host="127.0.0.1",
        port=server_port,
        device_manager=LinkDeviceManager(registry),
        server_private_key=hub_key,
    )
    listener.start()
    time.sleep(0.05)

    client = DirectClient(
        device_id=device_id,
        private_key=client_key,
        host="127.0.0.1",
        port=server_port,
        server_public_key=listener.server_public_der,
    )
    client.connect(timeout=3.0)

    # Verifica que a sessão ativa está plenamente funcional antes da revogação
    assert client.ping() is True

    # REVOGAÇÃO IMEDIATA DO DISPOSITIVO
    revoked = registry.revoke_device(device_id, permanent=True)
    assert revoked is True
    assert registry.status(device_id) == "revoked"

    # Pequena pausa para propagação do encerramento
    time.sleep(0.1)

    # 1. Operações em andamento ou subsequentes no socket DEVEM falhar imediatamente (socket fechado)
    with pytest.raises((ConnectionError, ConnectionResetError, BrokenPipeError, PermissionError)):
        client.ping()

    # 2. Nova conexão pelo mesmo dispositivo revogado DEVE ser recusada categoricamente
    client2 = DirectClient(
        device_id=device_id,
        private_key=client_key,
        host="127.0.0.1",
        port=server_port,
        server_public_key=listener.server_public_der,
    )
    with pytest.raises((ConnectionError, PermissionError)):
        client2.connect(timeout=2.0)

    listener.stop()


# ==============================================================================
# Vetores Criptográficos Cruzados (Conformidade Rigorosa Python <-> Rust)
# ==============================================================================
def test_cross_language_cryptographic_golden_vectors():
    """Valida que o Python e o Rust concordam bit a bit sobre os hashes de canonicalização,

    Session Binding e rejeição de mensagens malformadas.
    """
    dev_id = "test-device-id-golden-vector-0123456789abc"
    eph = "eph-pk-golden-123"
    nonce = "client-nonce-golden-456"
    ts = 1700000000

    init_bytes = canonical_handshake_init(dev_id, eph, nonce, ts)
    init_hash = hashlib.sha256(init_bytes).digest()
    init_hash_b64 = b64(init_hash)

    assert init_hash.hex() == "3711efb419ffb936e32c92113659cc9b5648a7240539f673c532878bd5bfaa1d"
    assert init_hash_b64 == "NxHvtBn_uTbjLJIRNlnMm1ZIpyQFOfZzxTKHi9W_qh0"

    srv_eph = "server-eph-golden-789"
    srv_nonce = "server-nonce-golden-999"

    resp_bytes = canonical_handshake_response(
        server_id="mark-pc-core",
        ephemeral_pk_der_b64=srv_eph,
        client_nonce_b64=nonce,
        server_nonce_b64=srv_nonce,
        timestamp=ts,
        init_hash_b64=init_hash_b64,
    )

    transcript_hash = compute_transcript_hash(init_bytes, resp_bytes)
    assert transcript_hash.hex() == "638839677435d60cd988e28d5776a81ff38cf68edbfc30c1a2774a56c53bf47b"
    assert b64(transcript_hash) == "Y4g5Z3Q11gzZiOKNV3aoH_OM9o7b_DDBondKVsU79Hs"


def test_malformed_handshake_packet_rejection():
    """Valida que pacotes com cabeçalho ou JSON corrompido são imediatamente rejeitados."""
    # 1. Cabeçalho com magic number inválido ou tamanho truncado
    corrupted_hdr = b"\x00" * HEADER_SIZE
    with pytest.raises(Exception):
        PacketHeader.decode(corrupted_hdr)

    # 2. Frame com length excessivo (> 12 MB) deve ser categoricamente rejeitado pelo parser
    excessive_hdr = PacketHeader(
        packet_type=PacketType.DATA,
        flags=0,
        sequence=1,
        length=20 * 1024 * 1024,  # > 12 MB
    ).encode()
    with pytest.raises(ValueError, match="(?i)excede o limite máximo"):
        PacketHeader.decode(excessive_hdr)


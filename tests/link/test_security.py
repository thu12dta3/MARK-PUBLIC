"""Testes de Segurança, Isolamento Criptográfico e Proteção Anti-Replay do MARK Link."""

import time
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from src.mark.device_auth import DeviceRegistry, b64
from src.mark.link import (
    AccessPolicy,
    DirectionalSecureChannel,
    EncryptedEnvelope,
    LinkDeviceManager,
    MarkLinkService,
    PacketType,
    ReplayFilter,
    SecureChannel,
    LinkTelemetryCollector,
)


def test_access_policy_strict_allowlist():
    policy = AccessPolicy()

    # Rotas autorizadas do MARK
    assert policy.is_route_allowed("/v1/chat/completions")
    assert policy.is_route_allowed("/v1/voice/turn")
    assert policy.is_route_allowed("/health")
    assert policy.is_route_allowed("/v1/session")
    assert policy.is_route_allowed("/v1/session/renew")
    assert policy.is_route_allowed("/v1/pair/enroll")
    assert policy.is_route_allowed("/v1/agenda")
    assert policy.is_route_allowed("/v1/history")

    # Rotas invasivas ou não relacionadas terminantemente bloqueadas
    assert not policy.is_route_allowed("/admin")
    assert not policy.is_route_allowed("/etc/shadow")
    assert not policy.is_route_allowed("/api/v1/docker")
    assert not policy.is_route_allowed("/system/exec")
    assert not policy.is_route_allowed("/local/files")

    # Métodos autorizados
    assert policy.is_method_allowed("GET")
    assert policy.is_method_allowed("POST")
    assert not policy.is_method_allowed("DELETE")
    assert not policy.is_method_allowed("PUT")
    assert not policy.is_method_allowed("PATCH")

    # Disparo de exceções de permissão
    with pytest.raises(PermissionError, match="Rota '/arbitrary' proibida"):
        policy.validate_request("GET", "/arbitrary")

    with pytest.raises(PermissionError, match="Método 'DELETE' não autorizado"):
        policy.validate_request("DELETE", "/v1/chat/completions")


def test_replay_filter_nonce_and_sequence():
    replay = ReplayFilter(window_size=64)

    # 1. Nonce de uso único
    assert replay.register_nonce("nonce-abc-1")
    assert not replay.register_nonce("nonce-abc-1")  # Replay rejeitado
    assert replay.register_nonce("nonce-abc-2")

    # 2. Timestamp e deriva de relógio (clock skew)
    now = int(time.time())
    assert replay.check_timestamp(now)
    assert replay.check_timestamp(now + 30)
    assert not replay.check_timestamp(now + 120)  # Muito no futuro
    assert not replay.check_timestamp(now - 120)  # Muito no passado

    # 3. Sequências e janela deslizante
    assert replay.validate_sequence(1)
    assert replay.validate_sequence(2)
    assert not replay.validate_sequence(2)  # Replay de sequência
    assert replay.validate_sequence(10)     # Avanço legítimo
    assert replay.validate_sequence(8)      # Fora de ordem aceito na janela
    assert not replay.validate_sequence(8)  # Duplicado rejeitado
    assert not replay.validate_sequence(0)  # Inválido


def test_cryptographic_seal_unseal_and_tamper_detection():
    key = b"\x42" * 32
    salt = b"\x01\x02\x03\x04"
    channel_tx = SecureChannel(key, salt)
    channel_rx = SecureChannel(key, salt)

    plaintext = b"MARK-LINK-PAYLOAD-CONFIDENTIAL"
    frame = channel_tx.seal_packet(PacketType.DATA, plaintext)
    assert len(frame) > len(plaintext)

    # Decifragem legítima
    header, decrypted = channel_rx.unseal_packet(frame)
    assert header.packet_type == PacketType.DATA
    assert header.sequence == 1
    assert decrypted == plaintext

    # Adulteração no texto cifrado
    tampered_cipher = bytearray(frame)
    tampered_cipher[-1] ^= 0x01
    with pytest.raises(PermissionError, match="Falha de autenticação do frame"):
        channel_rx.unseal_packet(bytes(tampered_cipher))

    # Adulteração no cabeçalho (AAD)
    tampered_header = bytearray(frame)
    tampered_header[5] = PacketType.PING.value  # Modifica packet_type no AAD
    with pytest.raises(PermissionError, match="Falha de autenticação do frame"):
        channel_rx.unseal_packet(bytes(tampered_header))


def test_directional_secure_channel_tx_rx_separation():
    shared_secret = b"\x99" * 32
    c_nonce = b"\x11" * 32
    s_nonce = b"\x22" * 32

    client_chan = DirectionalSecureChannel.derive(shared_secret, c_nonce, s_nonce, is_server=False)
    server_chan = DirectionalSecureChannel.derive(shared_secret, c_nonce, s_nonce, is_server=True)

    # As chaves de transmissão e recepção DEVEM ser distintas
    assert client_chan.tx_key != client_chan.rx_key
    assert server_chan.tx_key != server_chan.rx_key
    assert client_chan.tx_key == server_chan.rx_key
    assert client_chan.rx_key == server_chan.tx_key

    # Cliente envia pacote para o servidor
    c2s_frame = client_chan.seal_packet(PacketType.DATA, b"CLIENT_TO_SERVER")
    hdr, payload = server_chan.unseal_packet(c2s_frame)
    assert hdr.packet_type == PacketType.DATA
    assert payload == b"CLIENT_TO_SERVER"

    # Cliente NÃO pode decifrar o próprio pacote transmitido (separação direcional estrita)
    with pytest.raises(PermissionError):
        client_chan.unseal_packet(c2s_frame)

    # Servidor envia resposta para o cliente
    s2c_frame = server_chan.seal_packet(PacketType.DATA, b"SERVER_TO_CLIENT")
    hdr_resp, payload_resp = client_chan.unseal_packet(s2c_frame)
    assert payload_resp == b"SERVER_TO_CLIENT"

    # Servidor NÃO pode decifrar o pacote que ele mesmo transmitiu
    with pytest.raises(PermissionError):
        server_chan.unseal_packet(s2c_frame)


def test_directional_channel_sequence_overflow_failsafe():
    shared_secret = b"\x55" * 32
    c_nonce = b"\x01" * 32
    s_nonce = b"\x02" * 32

    chan = DirectionalSecureChannel.derive(shared_secret, c_nonce, s_nonce, is_server=False)
    chan.tx_seq = 0xFFFFFFFF  # Força exaustão de contador

    with pytest.raises(OverflowError, match="Exaustão de sequência de nonces"):
        chan.seal_packet(PacketType.DATA, b"OVERFLOW_ATTEMPT")


def test_directional_channel_replay_rejection():
    shared_secret = b"\x77" * 32
    c_nonce = b"\x03" * 32
    s_nonce = b"\x04" * 32

    client_chan = DirectionalSecureChannel.derive(shared_secret, c_nonce, s_nonce, is_server=False)
    server_chan = DirectionalSecureChannel.derive(shared_secret, c_nonce, s_nonce, is_server=True)

    frame = client_chan.seal_packet(PacketType.DATA, b"SINGLE_USE_COMMAND")
    # Primeira recepção é legítima
    _, p = server_chan.unseal_packet(frame)
    assert p == b"SINGLE_USE_COMMAND"

    # Tentativa de replay do mesmo frame deve ser rejeitada
    with pytest.raises(PermissionError, match="(replay seq=|Pacote duplicado)"):
        server_chan.unseal_packet(frame)


def test_service_session_derivation_freshness(tmp_path):
    registry = DeviceRegistry(tmp_path / "devices_freshness.db")
    dev_mgr = LinkDeviceManager(registry)
    service = MarkLinkService(device_manager=dev_mgr)

    # Registra e aprova dispositivo
    key = ec.generate_private_key(ec.SECP256R1())
    pub_der = key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    code = registry.open_enrollment(validity_seconds=120)
    proof = key.sign(b"MARK-PAIR-V1\n" + code.encode("utf-8"), ec.ECDSA(hashes.SHA256()))
    device_id = registry.enroll(code, "Aparelho Freshness", b64(pub_der), b64(proof))
    registry.update_device(device_id, "authorized")

    # Deriva duas sessões consecutivas
    session1 = service.establish_peer_session(device_id)
    session2 = service.establish_peer_session(device_id)

    # Cada sessão DEVE ter chaves efêmeras e nonces distintos e independentes do device_id
    assert session1.tx_key != session2.tx_key
    assert session1.rx_key != session2.rx_key
    assert b"session_secret" not in session1.tx_key


def test_blind_relay_envelope_secrecy():
    target = "device_target_id_1234567890123456789012345"
    source = "device_source_id_1234567890123456789012345"
    opaque = b"\x00\x01\x02\x03\x04\xff\xfe"

    envelope = EncryptedEnvelope(target, source, opaque)
    serialized = envelope.serialize()

    # O relay recebe apenas os metadados de roteamento e o blob opaco
    deserialized = EncryptedEnvelope.deserialize(serialized)
    assert deserialized.target_id == target
    assert deserialized.source_id == source
    assert deserialized.opaque_payload == opaque
    assert b"MARK-SECRET-KEY" not in serialized


def test_telemetry_sanitization():
    collector = LinkTelemetryCollector()
    collector.set_peer("super_sensitive_device_token_key_1234567890")
    collector.record_error("Falha ao validar Bearer token secretdatakey42")

    report = collector.generate_report()
    # ID mascarado
    assert "…" in report.peer_id_masked
    # Erro sanitizado sem segredos expostos
    assert report.last_error is not None
    assert "secretdatakey42" not in report.last_error
    assert "[REDACTED_DIAGNOSTIC_INFO" in report.last_error

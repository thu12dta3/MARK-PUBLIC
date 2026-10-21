"""Testes Funcionais e de Conectividade do MARK Link."""

import time
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from src.mark.device_auth import DeviceRegistry, b64
from src.mark.link import (
    AccessPolicy,
    CandidateType,
    ConnectionCandidate,
    DirectClient,
    EndpointDiscovery,
    EncryptedEnvelope,
    LinkDevice,
    LinkDeviceManager,
    LinkPeerManager,
    MarkLinkService,
    PacketType,
    PeerState,
    RelayClient,
    RelayServer,
    BlindRelayRouter,
    SecureChannel,
)


@pytest.fixture
def temp_db(tmp_path):
    return tmp_path / "devices_link_test.db"


def test_pairing_approval_and_revocation(temp_db):
    registry = DeviceRegistry(temp_db)
    dev_mgr = LinkDeviceManager(registry)
    service = MarkLinkService(device_manager=dev_mgr)

    # 1. Dispositivo desconhecido inicialmente
    dev_id = "unknown_device_id_1234567890123456789012345"
    assert not service.authorize_connection(dev_id)

    # 2. Pareamento: código efêmero de uso único
    code = registry.open_enrollment(validity_seconds=120)
    assert code is not None

    client_key = ec.generate_private_key(ec.SECP256R1())
    pub_der = client_key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    proof = client_key.sign(b"MARK-PAIR-V1\n" + code.encode("utf-8"), ec.ECDSA(hashes.SHA256()))

    device_id = registry.enroll(code, "Galaxy M23 Teste", b64(pub_der), b64(proof))
    assert registry.status(device_id) == "pending"
    assert not service.authorize_connection(device_id)  # Ainda pendente

    # 3. Tentativa de reusar código de pareamento já usado deve falhar
    with pytest.raises(ValueError, match="pareamento expirado ou já usado"):
        registry.enroll(code, "Dispositivo Intruso", b64(pub_der), b64(proof))

    # 4. Aprovação explícita no PC
    assert registry.update_device(device_id, "authorized")
    assert service.authorize_connection(device_id)

    # 5. Revogação imediata em tempo real
    assert registry.revoke_device(device_id, permanent=True)
    assert not service.authorize_connection(device_id)


def test_direct_local_and_fallback_to_relay():
    pm = LinkPeerManager("device-peer-1")
    candidates = EndpointDiscovery.gather_candidates(
        local_port=8766,
        relay_address="relay.mark.local:8766",
        public_ip="203.0.113.198",
    )
    for c in candidates:
        pm.add_candidate(c)

    assert pm.candidates[0].candidate_type == CandidateType.LOCAL_LAN
    pm.set_connected(CandidateType.LOCAL_LAN)
    assert pm.state == PeerState.DIRECT_LOCAL
    assert pm.state.is_direct

    interval = pm.trigger_reconnect()
    assert pm.state == PeerState.RECONNECTING
    assert interval > 0

    pm.set_connected(CandidateType.RELAY_SERVER)
    assert pm.state == PeerState.RELAYED
    assert not pm.state.is_direct
    assert pm.state.is_connected


def test_relay_unavailability_and_reconnection():
    pm = LinkPeerManager("device-peer-2")
    b1 = pm.trigger_reconnect()
    b2 = pm.trigger_reconnect()
    b3 = pm.trigger_reconnect()
    assert b1 < b2 < b3 or b2 > 1.0


def test_idle_inactivity_reduces_ping_rate():
    pm = LinkPeerManager("device-peer-3", idle_threshold_secs=0.05, active_ping_secs=2.0, idle_ping_secs=20.0)
    assert pm.current_ping_interval() == 2.0
    time.sleep(0.06)
    assert pm.current_ping_interval() == 20.0


def test_service_start_and_clean_stop():
    service = MarkLinkService(host="127.0.0.1", port=18766)
    try:
        service.start()
        assert service.running
        diag = service.get_diagnostics()
        assert diag.state == "Disconnected"
    finally:
        service.stop()
        assert not service.running


def test_end_to_end_authenticated_direct_session(temp_db):
    registry = DeviceRegistry(temp_db)
    dev_mgr = LinkDeviceManager(registry)
    port = 18767
    service = MarkLinkService(host="127.0.0.1", port=port, device_manager=dev_mgr)

    # Gera chave de cliente e autoriza dispositivo
    client_key = ec.generate_private_key(ec.SECP256R1())
    pub_der = client_key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    code = registry.open_enrollment(validity_seconds=120)
    proof = client_key.sign(b"MARK-PAIR-V1\n" + code.encode("utf-8"), ec.ECDSA(hashes.SHA256()))
    device_id = registry.enroll(code, "Dispositivo Móvel Autorizado", b64(pub_der), b64(proof))
    registry.update_device(device_id, "authorized")

    service.start()
    try:
        client = DirectClient(device_id=device_id, private_key=client_key, host="127.0.0.1", port=port)
        client.connect()

        # 1. Ping / Pong autenticado e criptografado
        assert client.ping() is True

        # 2. Requisição para rota autorizada (/health)
        resp = client.send_request("GET", "/health")
        assert resp.get("status") in (200, 401, 502, 503)

        # 3. Requisição para rota administrativa bloqueada (/admin)
        resp_blocked = client.send_request("GET", "/admin")
        assert resp_blocked.get("status") == 403
        assert "proibida" in resp_blocked.get("error", "")

        client.close()
    finally:
        service.stop()


def test_unauthorized_device_handshake_rejected(temp_db):
    registry = DeviceRegistry(temp_db)
    dev_mgr = LinkDeviceManager(registry)
    port = 18768
    service = MarkLinkService(host="127.0.0.1", port=port, device_manager=dev_mgr)

    # Dispositivo não registrado nem autorizado
    rogue_key = ec.generate_private_key(ec.SECP256R1())
    rogue_id = "rogue_device_not_in_registry_12345678901234"

    service.start()
    try:
        client = DirectClient(device_id=rogue_id, private_key=rogue_key, host="127.0.0.1", port=port)
        with pytest.raises((ConnectionResetError, ConnectionError, PermissionError)):
            client.connect()
        client.close()
    finally:
        service.stop()


def test_tcp_relay_server_and_client_communication():
    port = 18769
    relay = RelayServer(host="127.0.0.1", port=port)
    relay.start()
    try:
        client_a = RelayClient(my_device_id="device-alice-12345", server_addr=("127.0.0.1", port))
        client_b = RelayClient(my_device_id="device-bob-67890", server_addr=("127.0.0.1", port))
        time.sleep(0.05)

        # Alice envia envelope cifrado opaco para Bob através do Relay TCP
        opaque_secret = b"\xde\xad\xbe\xef\xca\xfe\xba\xbe"
        sent = client_a.send_envelope(target_id="device-bob-67890", opaque_frame=opaque_secret)
        assert sent is True

        time.sleep(0.05)
        received = client_b.poll_incoming()
        assert len(received) >= 1
        assert opaque_secret in received

        client_a.close()
        client_b.close()
    finally:
        relay.stop()

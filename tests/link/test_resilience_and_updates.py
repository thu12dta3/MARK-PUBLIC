"""Testes de Resiliência, Idempotência, Bateria e Atualizações Seguras (Marco C).

Fase RED / GREEN:
1. 3.1: Idempotência e Tratamento de Incertezas (Request IDs únicos e cache LRU).
2. 3.2: Eficiência e Bateria (Keep-alives Adaptativos com transição ativa/ociosa).
3. 3.3: Atualizações Seguras e Rollback (Integridade SHA-256 + ECDSA e Anti-Downgrade).
"""

from __future__ import annotations

import base64
import hashlib
import json
import socket
import time
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import MagicMock

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from src.mark.link.peer.manager import LinkPeerManager
from src.mark.link.transport.adapter import LinkTransportAdapter
from src.mark.link.transport.direct import DirectClient, DirectConnectionHandler, DirectListener
from src.mark.link.transport.framing import DirectionalSecureChannel, PacketHeader, PacketType

# Módulo de atualização segura (Marco C)
from src.mark.link.updater import (
    DowngradeError,
    UpdateIntegrityError,
    UpdateManifest,
    UpdateSecurityError,
    UpdateSignatureError,
    UpdateVerifier,
    canonical_update_manifest,
)


# ==============================================================================
# 1. 3.1 - Idempotência e Tratamento de Incertezas (Request IDs e Cache LRU)
# ==============================================================================


def test_idempotency_mutation_duplicate_request_id_not_executed_twice() -> None:
    """Envio de uma mutação duas vezes com o mesmo Request ID não deve executar duas vezes."""
    adapter = LinkTransportAdapter(target_base_url="http://127.0.0.1:8765")

    # Mock do forwarder / downstream HTTP
    call_count = 0

    def mock_urlopen(req: Any, timeout: float = 30) -> Any:
        nonlocal call_count
        call_count += 1
        resp = MagicMock()
        resp.status = 200
        resp.headers = {"Content-Type": "application/json"}
        resp.read.return_value = json.dumps({
            "message": "luz ligada com sucesso",
            "execution_id": call_count,
        }).encode("utf-8")
        resp.__enter__.return_value = resp
        return resp

    import urllib.request

    original_urlopen = urllib.request.urlopen
    urllib.request.urlopen = mock_urlopen
    try:
        req_id = "req-unique-uuid-12345"
        body = json.dumps({"device": "lampada_sala", "action": "turn_on"}).encode("utf-8")

        # Primeira execução: deve contatar o backend e executar a ação
        status1, headers1, resp_body1 = adapter.forward_request(
            method="POST",
            path="/v1/chat/completions",
            headers={"X-Request-ID": req_id},
            body=body,
            device_id="device-test-01",
            request_id=req_id,
        )

        assert status1 == 200
        assert call_count == 1
        data1 = json.loads(resp_body1.decode("utf-8"))
        assert data1["execution_id"] == 1

        # Segunda execução com o MESMO Request ID (simulando reenvio de rede):
        # O backend NÃO DEVE ser executado novamente (call_count permanece 1)
        status2, headers2, resp_body2 = adapter.forward_request(
            method="POST",
            path="/v1/chat/completions",
            headers={"X-Request-ID": req_id},
            body=body,
            device_id="device-test-01",
            request_id=req_id,
        )

        assert status2 == 200
        assert (
            call_count == 1
        ), f"FALHA RED: Mutação executada duas vezes! Backend foi chamado {call_count} vezes."
        data2 = json.loads(resp_body2.decode("utf-8"))
        assert data2["execution_id"] == 1
        assert (
            headers2.get("X-Mark-Idempotent-Replay") == "true"
            or headers2.get("x-mark-idempotent-replay") == "true"
        )
    finally:
        urllib.request.urlopen = original_urlopen


def test_idempotency_different_request_ids_execute_normally() -> None:
    """Requisições com Request IDs distintos devem ser executadas normalmente."""
    adapter = LinkTransportAdapter(target_base_url="http://127.0.0.1:8765")
    call_count = 0

    def mock_urlopen(req: Any, timeout: float = 30) -> Any:
        nonlocal call_count
        call_count += 1
        resp = MagicMock()
        resp.status = 200
        resp.headers = {"Content-Type": "application/json"}
        resp.read.return_value = json.dumps({"count": call_count}).encode("utf-8")
        resp.__enter__.return_value = resp
        return resp

    import urllib.request

    original_urlopen = urllib.request.urlopen
    urllib.request.urlopen = mock_urlopen
    try:
        body = json.dumps({"action": "ping"}).encode("utf-8")
        status1, _, _ = adapter.forward_request(
            "POST",
            "/v1/chat/completions",
            {},
            body,
            "dev-1",
            request_id="req-111",
        )
        status2, _, _ = adapter.forward_request(
            "POST",
            "/v1/chat/completions",
            {},
            body,
            "dev-1",
            request_id="req-222",
        )

        assert status1 == 200
        assert status2 == 200
        assert call_count == 2
    finally:
        urllib.request.urlopen = original_urlopen


# ==============================================================================
# 2. 3.2 - Eficiência e Bateria (Keep-alives Adaptativos)
# ==============================================================================


def test_adaptive_keepalive_transitions_to_idle_and_resets_on_data_activity() -> None:
    """Valida redução drástica da taxa de ping em ociosidade e retorno a taxa ativa com tráfego."""
    pm = LinkPeerManager(
        peer_id="device-battery-test",
        idle_threshold_secs=0.05,
        active_ping_secs=5.0,
        idle_ping_secs=45.0,
    )

    # Estado ativo recém conectado: intervalo ativo (5s)
    assert pm.current_ping_interval() == 5.0

    # Simula passagem de tempo sem atividade (repouso)
    time.sleep(0.06)
    assert (
        pm.current_ping_interval() == 45.0
    ), f"FALHA RED: Intervalo de keepalive deveria ter subido para 45.0s em repouso, mas é {pm.current_ping_interval()}"

    # Tráfego de dados transmitido/recebido deve restaurar imediatamente o modo ativo (5s)
    assert hasattr(pm, "record_activity"), "FALHA RED: LinkPeerManager não possui método record_activity"
    pm.record_activity()
    assert pm.current_ping_interval() == 5.0


def test_direct_client_adaptive_keepalive_timing() -> None:
    """Cliente direto Python deve possuir temporizador adaptativo de keepalive."""
    key = ec.generate_private_key(ec.SECP256R1())
    client = DirectClient(
        device_id="mobile-client-01",
        private_key=key,
        host="127.0.0.1",
        port=8766,
    )

    assert hasattr(
        client, "current_ping_interval"
    ), "FALHA RED: DirectClient não possui método current_ping_interval"
    assert hasattr(
        client, "record_activity"
    ), "FALHA RED: DirectClient não possui método record_activity"

    client.idle_threshold_secs = 0.05
    client.active_ping_secs = 5.0
    client.idle_ping_secs = 60.0

    client.record_activity()
    assert client.current_ping_interval() == 5.0

    time.sleep(0.06)
    assert client.current_ping_interval() == 60.0


# ==============================================================================
# 3. 3.3 - Atualizações Seguras e Rollback (Integridade SHA-256 + ECDSA e Anti-Downgrade)
# ==============================================================================


def _generate_release_keypair() -> Tuple[ec.EllipticCurvePrivateKey, ec.EllipticCurvePublicKey]:
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = priv.public_key()
    return priv, pub


def test_secure_update_valid_manifest_and_payload_succeeds() -> None:
    """Artefato de atualização válido assinado pela chave de release deve ser aceito."""
    release_priv, release_pub = _generate_release_keypair()
    verifier = UpdateVerifier(current_version="1.2.0", release_public_key=release_pub)

    payload = b"MARK-BINARY-V1.3.0-UPDATE-PAYLOAD-OK"
    payload_sha256 = hashlib.sha256(payload).hexdigest()
    timestamp = int(time.time())
    new_version = "1.3.0"

    canonical_msg = canonical_update_manifest(
        version=new_version,
        payload_sha256=payload_sha256,
        timestamp=timestamp,
        channel="stable",
    )
    sig = release_priv.sign(canonical_msg, ec.ECDSA(hashes.SHA256()))
    sig_b64 = base64.urlsafe_b64encode(sig).decode("ascii")

    manifest = UpdateManifest(
        version=new_version,
        payload_sha256=payload_sha256,
        timestamp=timestamp,
        signature_b64=sig_b64,
        channel="stable",
    )

    result = verifier.verify_update(manifest, payload)
    assert result["valid"] is True
    assert result["version"] == "1.3.0"
    assert result["payload_size"] == len(payload)


def test_secure_update_downgrade_attack_rejected() -> None:
    """Tentativa de aplicar atualização com versão menor ou igual à atual DEVE ser barrada (Anti-Downgrade)."""
    release_priv, release_pub = _generate_release_keypair()
    verifier = UpdateVerifier(current_version="2.0.0", release_public_key=release_pub)

    # Tentativa 1: versão estritamente menor (1.9.9)
    payload = b"OLD-VULNERABLE-PAYLOAD"
    payload_sha256 = hashlib.sha256(payload).hexdigest()
    timestamp = int(time.time())

    for bad_ver in ["1.9.9", "2.0.0", "1.0.0", "0.9.1"]:
        msg = canonical_update_manifest(bad_ver, payload_sha256, timestamp, "stable")
        sig = release_priv.sign(msg, ec.ECDSA(hashes.SHA256()))
        sig_b64 = base64.urlsafe_b64encode(sig).decode("ascii")

        manifest = UpdateManifest(
            version=bad_ver,
            payload_sha256=payload_sha256,
            timestamp=timestamp,
            signature_b64=sig_b64,
        )

        with pytest.raises(DowngradeError, match="downgrade|versão inferior|igual"):
            verifier.verify_update(manifest, payload)


def test_secure_update_corrupted_payload_hash_rejected() -> None:
    """Atualização com payload adulterado ou corrompido em trânsito deve ser rejeitada."""
    release_priv, release_pub = _generate_release_keypair()
    verifier = UpdateVerifier(current_version="1.0.0", release_public_key=release_pub)

    payload_original = b"AUTHENTIC-UPDATE-PAYLOAD"
    payload_corrupted = b"AUTHENTIC-UPDATE-PAYLOAD-TAMPERED-BYTE"
    expected_hash = hashlib.sha256(payload_original).hexdigest()
    timestamp = int(time.time())

    msg = canonical_update_manifest("1.1.0", expected_hash, timestamp, "stable")
    sig = release_priv.sign(msg, ec.ECDSA(hashes.SHA256()))
    sig_b64 = base64.urlsafe_b64encode(sig).decode("ascii")

    manifest = UpdateManifest(
        version="1.1.0",
        payload_sha256=expected_hash,
        timestamp=timestamp,
        signature_b64=sig_b64,
    )

    with pytest.raises(UpdateIntegrityError, match="hash|corrompido|adulterado"):
        verifier.verify_update(manifest, payload_corrupted)


def test_secure_update_invalid_or_forged_signature_rejected() -> None:
    """Manifesto assinado com chave invasora ou assinatura corrompida deve ser sumariamente bloqueado."""
    release_priv, release_pub = _generate_release_keypair()
    attacker_priv, _ = _generate_release_keypair()

    verifier = UpdateVerifier(current_version="1.0.0", release_public_key=release_pub)

    payload = b"NEW-VALID-PAYLOAD"
    payload_sha256 = hashlib.sha256(payload).hexdigest()
    timestamp = int(time.time())

    # Assinado por atacante com chave privada não autorizada
    msg = canonical_update_manifest("1.5.0", payload_sha256, timestamp, "stable")
    forged_sig = attacker_priv.sign(msg, ec.ECDSA(hashes.SHA256()))
    forged_sig_b64 = base64.urlsafe_b64encode(forged_sig).decode("ascii")

    manifest = UpdateManifest(
        version="1.5.0",
        payload_sha256=payload_sha256,
        timestamp=timestamp,
        signature_b64=forged_sig_b64,
    )

    with pytest.raises(UpdateSignatureError, match="assinatura|inválida|forjada"):
        verifier.verify_update(manifest, payload)


def test_end_to_end_socket_mutation_idempotency_after_reconnection() -> None:
    """Simula desconexão de rede e reenvio de mutação com mesmo Request ID via DirectClient/DirectListener."""
    backend_calls = 0

    def mock_urlopen(req: Any, timeout: float = 30) -> Any:
        nonlocal backend_calls
        backend_calls += 1
        resp = MagicMock()
        resp.status = 200
        resp.headers = {"Content-Type": "application/json"}
        resp.read.return_value = json.dumps({"status": "executado", "call_seq": backend_calls}).encode("utf-8")
        resp.__enter__.return_value = resp
        return resp

    import urllib.request
    original_urlopen = urllib.request.urlopen
    urllib.request.urlopen = mock_urlopen

    port = 18788
    server_priv = ec.generate_private_key(ec.SECP256R1())
    server_pub = server_priv.public_key()

    client_priv = ec.generate_private_key(ec.SECP256R1())
    client_pub_der = client_priv.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    device_id = "device-idemp-e2e"

    listener = DirectListener(
        host="127.0.0.1",
        port=port,
        key_resolver=lambda did: client_pub_der if did == device_id else None,
        server_private_key=server_priv,
    )
    listener.start()

    try:
        # 1. Primeira conexão do cliente
        client = DirectClient(
            device_id=device_id,
            private_key=client_priv,
            host="127.0.0.1",
            port=port,
            server_public_key=server_pub,
        )
        client.connect()

        req_id = "req-e2e-idemp-999"
        body = json.dumps({"action": "light_toggle"}).encode("utf-8")
        res1 = client.send_request("POST", "/v1/chat/completions", body=body, request_id=req_id)

        assert res1.get("status") == 200
        assert backend_calls == 1

        # 2. Simula queda brusca de socket (rede cai no meio da operação)
        client.close()

        # 3. Cliente reconecta e retransmite a operação com o mesmo Request ID
        client2 = DirectClient(
            device_id=device_id,
            private_key=client_priv,
            host="127.0.0.1",
            port=port,
            server_public_key=server_pub,
        )
        client2.connect()

        res2 = client2.send_request("POST", "/v1/chat/completions", body=body, request_id=req_id)
        assert res2.get("status") == 200
        assert res2.get("idempotent_replay") is True, f"Esperado idempotent_replay=True, recebido: {res2}"
        assert backend_calls == 1, f"FALHA: Backend executou a mutação {backend_calls} vezes!"
        client2.close()
    finally:
        listener.stop()
        urllib.request.urlopen = original_urlopen


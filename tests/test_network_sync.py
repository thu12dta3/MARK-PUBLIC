"""Testes unitários e de integração para o protocolo resiliente MARK Sync (Fase 4)."""

import json
import os
import secrets
import tempfile
import time
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from src.mark.network.sync import (
    IdempotencyManager,
    NetworkDeviceManager,
    PairingSession,
    ResilientSyncProtocol,
    SyncMessageType,
    SyncPacket,
    _b64d,
    _b64e,
    get_best_local_endpoint,
)
from src.mark.device_auth import DeviceRegistry, b64, unb64


@pytest.fixture
def temp_db(tmp_path):
    db_file = tmp_path / "devices_test.db"
    return db_file


def test_endpoint_detection():
    endpoints = get_best_local_endpoint(default_port=8765)
    assert "url" in endpoints
    assert "local_ip" in endpoints
    assert endpoints["port"] == "8765"


def test_idempotency_manager():
    mgr = IdempotencyManager(max_entries=10, ttl_seconds=60)
    key = "req-12345"

    assert not mgr.is_duplicate(key)
    assert mgr.get_cached_result(key) is None

    mgr.record_result(key, {"answer": "hello world", "status": "done"})
    assert mgr.is_duplicate(key)
    cached = mgr.get_cached_result(key)
    assert cached == {"answer": "hello world", "status": "done"}


def test_ephemeral_pairing_and_aes_handshake(temp_db):
    registry = DeviceRegistry(temp_db)
    pairing = PairingSession(device_registry=registry, validity_seconds=120)
    qr_payload = pairing.get_qr_payload()

    assert qr_payload.version == "MARK-PAIR-V2"
    assert qr_payload.challenge == pairing.challenge
    assert qr_payload.ephemeral_pk == pairing.ephemeral_pk_b64
    assert qr_payload.code == pairing.enrollment_code

    # Simula cliente móvel gerando chave efêmera e identidade
    client_ecdh = ec.generate_private_key(ec.SECP256R1())
    client_pub_der = client_ecdh.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    client_pub_b64 = _b64e(client_pub_der)

    # Chave de identidade do dispositivo
    client_id_key = ec.generate_private_key(ec.SECP256R1())
    client_id_pub_der = client_id_key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    client_id_pub_b64 = _b64e(client_id_pub_der)
    proof = client_id_key.sign(b"MARK-PAIR-V1\n" + qr_payload.code.encode(), ec.ECDSA(hashes.SHA256()))
    proof_b64 = _b64e(proof)

    # Cliente deriva segredo ECDH com a chave efêmera do PC
    pc_pub = serialization.load_der_public_key(_b64d(qr_payload.ephemeral_pk))
    shared_secret = client_ecdh.exchange(ec.ECDH(), pc_pub)

    # HKDF -> AES-256-GCM key
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=qr_payload.challenge.encode("utf-8"),
        info=b"mark-pairing-handshake-v2",
    )
    client_aes_key = hkdf.derive(shared_secret)

    # Cifra payload de pareamento
    handshake_data = {
        "challenge": qr_payload.challenge,
        "name": "Galaxy M23 Teste",
        "public_key": client_id_pub_b64,
        "proof": proof_b64,
    }
    nonce = os.urandom(12)
    aesgcm = AESGCM(client_aes_key)
    ciphertext = aesgcm.encrypt(nonce, json.dumps(handshake_data).encode("utf-8"), None)

    # PC processa o handshake
    result = pairing.process_handshake(
        client_ephemeral_pk_der_b64=client_pub_b64,
        encrypted_payload_b64=_b64e(ciphertext),
        nonce_b64=_b64e(nonce),
    )

    assert result["status"] == "pending"
    assert result["name"] == "Galaxy M23 Teste"
    assert registry.status(result["device_id"]) == "pending"

    registry.close()


def test_resilient_packet_flow_and_deduplication(temp_db):
    proto = ResilientSyncProtocol(temp_db)

    # Registra dispositivo e autoriza
    client_key = ec.generate_private_key(ec.SECP256R1())
    pub_der = client_key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    code = proto.device_registry.open_enrollment()
    proof = client_key.sign(b"MARK-PAIR-V1\n" + code.encode(), ec.ECDSA(hashes.SHA256()))
    device_id = proto.device_registry.enroll(code, "Relógio TicWatch", _b64e(pub_der), _b64e(proof))
    proto.device_registry.update_device(device_id, "authorized")

    # Envia primeira mensagem
    pkt1 = SyncPacket(
        packet_id="p1",
        type=SyncMessageType.MESSAGE,
        seq=1,
        ack_seq=0,
        idempotency_key="msg-turn-1",
        payload={"text": "Que horas são?"},
    )

    resp1 = proto.process_incoming_packet(device_id, pkt1)
    assert resp1.type == SyncMessageType.ACK
    assert resp1.payload["echo"] == "Que horas são?"

    # Reenvia a MESMA mensagem (queda de rede e retransmissão)
    resp2 = proto.process_incoming_packet(device_id, pkt1)
    assert resp2.type == SyncMessageType.ACK
    assert resp2.payload.get("idempotent") is True

    # Teste de Retomada de Sessão (RESUME)
    stream = proto.get_or_create_stream(device_id)
    # Servidor envia tarefas/mensagens pro relógio
    stream.create_packet(SyncMessageType.MESSAGE, {"text": "São 14:30."})
    stream.create_packet(SyncMessageType.TASK, {"action": "vibrate"})

    # Cliente reconecta e pede replay a partir do seq 1
    resume_pkt = SyncPacket(
        packet_id="r1",
        type=SyncMessageType.RESUME,
        seq=2,
        ack_seq=1,
        idempotency_key="resume-01",
        payload={"last_seq": 1},
    )
    resume_ack = proto.process_incoming_packet(device_id, resume_pkt)
    assert resume_ack.type == SyncMessageType.RESUME_ACK
    assert resume_ack.payload["resumed"] is True
    assert resume_ack.payload["replayed_count"] >= 2

    proto.close()


def test_device_manager_revocation(temp_db):
    dm = NetworkDeviceManager(temp_db)

    # Cadastra dispositivo de teste
    key = ec.generate_private_key(ec.SECP256R1())
    pub_der = key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    code = dm.registry.open_enrollment()
    proof = key.sign(b"MARK-PAIR-V1\n" + code.encode(), ec.ECDSA(hashes.SHA256()))
    device_id = dm.registry.enroll(code, "Aparelho Alvo", _b64e(pub_der), _b64e(proof))

    devices = dm.list_devices_detailed()
    assert len(devices) == 1
    assert devices[0]["status"] == "pending"

    # Aprova
    assert dm.approve_device(device_id) is True
    assert dm.registry.status(device_id) == "authorized"

    # Revoga
    assert dm.revoke_device_access(device_id) is True
    assert dm.registry.status(device_id) == "revoked"

    dm.close()

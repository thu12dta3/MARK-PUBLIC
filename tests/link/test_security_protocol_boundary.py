"""Testes de Fronteira, Fuzzing e Mitigação de Vulnerabilidades do Marco B (MARK Link).

Fase RED / GREEN:
1. P0.3: Restrição estrita de API (Allowlist rígida, bloqueio de SO/caminhos arbitrários e separação ReadOnly vs Mutate).
2. 4.2: Integridade e Framing MLNK (Anti-Fuzzing, limite estrito de payload <= 4MB, anti-replay e failsafe contra wrap-around).
3. 5.3: Blind Relay Seguro (Bloqueio de Open Proxy/IPs arbitrários, backpressure rígido e drop por fila cheia).
"""

from __future__ import annotations

import os
import secrets
from typing import Any, Dict, Final, List, Optional, Tuple

import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from src.mark.link.identity.allowlist import AccessPolicy

try:
    from src.mark.link.identity.allowlist import RouteCategory
except ImportError:
    RouteCategory = None  # type: ignore[assignment,misc]

from src.mark.link.relay.envelope import EncryptedEnvelope
from src.mark.link.relay.server import BlindRelayRouter, RelayServer
from src.mark.link.transport.framing import (
    HEADER_SIZE,
    MAX_PAYLOAD_SIZE,
    DirectionalSecureChannel,
    PacketHeader,
    PacketType,
    SecureChannel,
)


# ==============================================================================
# 1. P0.3 - Restrição da API: Allowlist e Separação ReadOnly vs Mutate
# ==============================================================================


def test_api_allowlist_blocks_arbitrary_routes_and_system_paths() -> None:
    """Cliente malicioso tenta acessar rotas de SO, administração ou ferramentas não aprovadas."""
    policy = AccessPolicy()

    forbidden_routes = [
        "/admin",
        "/admin/system",
        "/etc/shadow",
        "/etc/passwd",
        "/proc/self/environ",
        "/sys/kernel",
        "/api/v1/docker",
        "/system/reboot",
        "/root/.ssh/id_rsa",
        "../../etc/passwd",
        "/v1/history/../../admin",
        "/v1/chat/completions/../../../etc/shadow",
        "/v1/non_existent_endpoint",
    ]

    for route in forbidden_routes:
        assert not policy.is_route_allowed(route), f"Rota proibida {route} não foi bloqueada!"
        with pytest.raises(PermissionError, match="proibida|não autorizad"):
            policy.validate_request("GET", route)
        with pytest.raises(PermissionError, match="proibida|não autorizad"):
            policy.validate_request("POST", route)


def test_api_allowlist_enforces_readonly_vs_mutate_separation() -> None:
    """Endpoints de leitura (ReadOnly) rejeitam mutação; endpoints de mutação exigem POST."""
    policy = AccessPolicy()

    readonly_endpoints = [
        "/health",
        "/v1/history",
        "/v1/agenda",
        "/v1/pair/status",
        "/v1/wear/summary",
    ]

    mutate_endpoints = [
        "/v1/chat/completions",
        "/v1/voice/turn",
        "/v1/session",
        "/v1/session/renew",
        "/v1/pair/enroll",
        "/v1/device/rotate",
        "/v1/wear/telemetry",
        "/call",
    ]

    # Validação de categorias
    for ep in readonly_endpoints:
        get_cat = getattr(policy, "get_route_category", None)
        assert get_cat is not None, "Método get_route_category ausente no AccessPolicy (Fase RED)"
        cat = get_cat(ep)
        expected = getattr(RouteCategory, "READ_ONLY", "READ_ONLY") if RouteCategory else "READ_ONLY"
        assert cat == expected or str(cat) == "RouteCategory.READ_ONLY"

    for ep in mutate_endpoints:
        get_cat = getattr(policy, "get_route_category", None)
        assert get_cat is not None, "Método get_route_category ausente no AccessPolicy (Fase RED)"
        cat = get_cat(ep)
        expected = getattr(RouteCategory, "MUTATE", "MUTATE") if RouteCategory else "MUTATE"
        assert cat == expected or str(cat) == "RouteCategory.MUTATE"

    # Endpoints ReadOnly só aceitam GET e SEM payload de mutação
    for ep in readonly_endpoints:
        # GET legítimo sem corpo deve passar
        policy.validate_request("GET", ep, body=b"")

        # Tentativa de POST em endpoint ReadOnly deve ser sumariamente bloqueada
        with pytest.raises(PermissionError, match="Mutação não permitida em endpoint ReadOnly|somente leitura"):
            policy.validate_request("POST", ep, body=b"")

        # Tentativa de GET com corpo de escrita (mutação disfarçada) deve ser bloqueada
        with pytest.raises(PermissionError, match="Payload não permitido em requisição ReadOnly|corpo não permitido"):
            policy.validate_request("GET", ep, body=b'{"delete_all": true}')

        # Tentativa de injetar mutação em query string
        with pytest.raises(PermissionError, match="Argumento de mutação proibido em endpoint ReadOnly|query de mutação"):
            policy.validate_request("GET", f"{ep}?action=delete&force=true", body=b"")

    # Endpoints Mutate só aceitam POST
    for ep in mutate_endpoints:
        # GET em endpoint Mutate deve ser bloqueado
        with pytest.raises(PermissionError, match="Método 'GET' não autorizado para endpoint de mutação|requer POST"):
            policy.validate_request("GET", ep, body=b"")


# ==============================================================================
# 2. 4.2 - Integridade e Framing MLNK: Limites, Anti-Fuzzing e Wrap-Around
# ==============================================================================


def test_mlnk_framing_strictly_enforces_4mb_payload_limit() -> None:
    """Garante que payloads maiores que 4MB sejam rejeitados preventivamente para mitigar OOM."""
    assert MAX_PAYLOAD_SIZE == 4 * 1024 * 1024, "MAX_PAYLOAD_SIZE deve ser estritamente 4 MB!"

    key = b"\x11" * 32
    salt = b"\x22" * 4
    channel = SecureChannel(key, salt)

    # Payload gigante (> 4MB) deve ser rejeitado no encode e decode
    oversized_length = (4 * 1024 * 1024) + 1  # 4MB + 1 byte
    oversized_header_bytes = PacketHeader(
        packet_type=PacketType.DATA,
        flags=0,
        sequence=1,
        length=oversized_length,
    ).encode()

    with pytest.raises(ValueError, match="excede o limite máximo"):
        PacketHeader.decode(oversized_header_bytes)

    with pytest.raises(ValueError, match="excede o limite máximo|excedem tamanho máximo"):
        oversized_data = b"X" * (4 * 1024 * 1024 + 10)
        channel.seal_packet(PacketType.DATA, oversized_data)


def test_mlnk_framing_fuzzing_robustness_and_tamper_rejection() -> None:
    """Fuzzing de pacotes binários: bytes truncados, invertidos, corrompidos e lixo."""
    key = b"\x33" * 32
    salt = b"\x44" * 4
    channel = SecureChannel(key, salt)

    # 1. Truncamento de cabeçalho
    with pytest.raises(ValueError, match="Cabeçalho incompleto|menor que o cabeçalho"):
        channel.unseal_packet(b"MLNK\x01\x03\x00")

    # 2. Magic adulterado
    bad_magic_header = b"BADM" + b"\x01\x03\x00\x00\x00\x00\x00\x00\x00\x00\x00\x01\x00\x00\x00\x10"
    with pytest.raises(ValueError, match="Magic de protocolo inválido"):
        channel.unseal_packet(bad_magic_header + b"\x00" * 16)

    # 3. Versão incompatível
    bad_version_header = b"MLNK" + b"\x02\x03\x00\x00\x00\x00\x00\x00\x00\x00\x00\x01\x00\x00\x00\x10"
    with pytest.raises(ValueError, match="Versão de protocolo incompatível"):
        channel.unseal_packet(bad_version_header + b"\x00" * 16)

    # 4. Fuzzing por inversão de bits em frame legítimo
    valid_frame = bytearray(channel.seal_packet(PacketType.DATA, b"TESTE-DE-FUZZING-INTEGRIDADE-MARK"))

    # 4. Opcode desconhecido no cabeçalho
    bad_type_header = bytearray(valid_frame)
    bad_type_header[5] = 0xFC  # Opcode desconhecido
    with pytest.raises(ValueError, match="Tipo de pacote desconhecido"):
        channel.unseal_packet(bytes(bad_type_header))

    # 5. Adulteração de metadados no cabeçalho AAD (altera para outro tipo válido sem mudar tamanho)
    tampered_header = bytearray(valid_frame)
    tampered_header[5] = int(PacketType.PING)  # Altera de DATA para PING no AAD
    with pytest.raises(PermissionError, match="Falha de autenticação do frame"):
        channel.unseal_packet(bytes(tampered_header))

    # Inverte bits no meio do ciphertext
    tampered_cipher = bytearray(valid_frame)
    tampered_cipher[HEADER_SIZE + 5] ^= 0xAA
    with pytest.raises(PermissionError, match="Falha de autenticação do frame"):
        channel.unseal_packet(bytes(tampered_cipher))

    # Inverte bits da tag de autenticação Poly1305 / GCM (últimos 16 bytes)
    tampered_tag = bytearray(valid_frame)
    tampered_tag[-1] ^= 0x01
    with pytest.raises(PermissionError, match="Falha de autenticação do frame"):
        channel.unseal_packet(bytes(tampered_tag))

    # Truncamento do payload
    truncated_frame = valid_frame[:-5]
    with pytest.raises(ValueError, match="Frame incompleto"):
        channel.unseal_packet(bytes(truncated_frame))


def test_mlnk_sequence_wrap_around_and_replay_failsafe() -> None:
    """Prevenção de wrap-around de número de sequência e colisão catastrófica de nonces."""
    key = b"\x55" * 32
    salt = b"\x66" * 4
    channel = SecureChannel(key, salt)

    # Simula contador no limite máximo de sequência de 32 bits
    channel.tx_seq = (1 << 32) - 1
    with pytest.raises(OverflowError, match="esgotado|Exaustão de sequência"):
        channel.seal_packet(PacketType.DATA, b"payload-overflow")


# ==============================================================================
# 3. 5.3 - Blind Relay: Proteção contra Proxy Aberto, Backpressure e Autenticação
# ==============================================================================


def test_relay_blocks_open_proxy_attempts_and_arbitrary_ips() -> None:
    """Tentativa de usar o Relay como proxy aberto para atingir IPs arbitrários ou alvos fora do MARK."""
    arbitrary_destinations = [
        "192.168.1.1:80",
        "10.0.0.1:443",
        "8.8.8.8",
        "http://evil.com",
        "https://google.com/proxy",
        "localhost:22",
        "/dev/tcp/127.0.0.1/8080",
        "short_dev",
        "very_long_string_with_illegal_characters_and_whitespace_overflow_test!!!",
    ]

    valid_device_source = "A" * 43  # Formato canônico base64url 43 chars

    for dest in arbitrary_destinations:
        with pytest.raises(ValueError, match="Identificador de dispositivo no envelope inválido|Destino inválido"):
            EncryptedEnvelope(target_id=dest, source_id=valid_device_source, opaque_payload=b"test")


def test_relay_enforces_backpressure_and_drops_on_full_queue() -> None:
    """Aplica backpressure: drop imediato quando a fila de entrega do destinatário atinge o limite."""
    router = BlindRelayRouter(max_queue_per_peer=64)
    target_id = "T" * 43
    source_id = "S" * 43

    router.register_peer(target_id)

    # Envia 64 envelopes (capacidade máxima)
    for i in range(64):
        env = EncryptedEnvelope(target_id=target_id, source_id=source_id, opaque_payload=f"msg-{i}".encode("utf-8"))
        success = router.route_envelope(env)
        assert success, f"Falha ao enfileirar envelope {i} dentro da cota"

    # O 65º envelope DEVE ser descartado para evitar esgotamento de memória (Backpressure)
    overflow_env = EncryptedEnvelope(target_id=target_id, source_id=source_id, opaque_payload=b"overflow-drop")
    success = router.route_envelope(overflow_env)
    assert not success, "O roteador do Relay permitiu estouro de fila sem aplicar backpressure!"

    # Drena e confirma que a fila não excedeu 64 itens
    drained = router.drain_for(target_id)
    assert len(drained) == 64

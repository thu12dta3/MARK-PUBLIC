"""Testes de Diagnóstico de Camadas e Exportação Criptografada do MARK Link (Fase 4).

Valida:
1. Diagnóstico estruturado separando explicitamente falhas de Serviço, LAN, Conexão Direta, Relay e Autenticação.
2. Zero vazamento de chaves privadas, segredos, senhas ou tokens nos relatórios de diagnóstico.
3. Exportação segura da configuração de dispositivos autorizados usando AES-256-GCM.
4. Separação arquitetural entre dados recuperáveis (identidades, chaves públicas, rotas) e dados protegidos pelo SO (chaves privadas).
5. Rejeição imediata de arquivos de configuração adulterados (tampering) ou senhas incorretas.
6. Restauração/importação fidedigna roundtrip em novo registro de dispositivos.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from src.mark.device_auth import DeviceRegistry, b64
from src.mark.link import (
    AccessPolicy,
    LinkDeviceManager,
    MarkLinkService,
    PeerState,
)
from src.mark.link.export import (
    ConfigIntegrityError,
    export_link_config,
    import_link_config,
    verify_link_config_file,
)
from src.mark.link.telemetry import (
    DiagnosticReport,
    LayerStatus,
    LinkTelemetryCollector,
    NetworkLayersReport,
)


@pytest.fixture
def temp_db(tmp_path: Path) -> Path:
    return tmp_path / "devices_diag_test.db"


@pytest.fixture
def clean_db(tmp_path: Path) -> Path:
    return tmp_path / "devices_clean_test.db"


def _create_approved_device(registry: DeviceRegistry, name: str) -> tuple[str, ec.EllipticCurvePrivateKey, bytes]:
    code = registry.open_enrollment(validity_seconds=120)
    key = ec.generate_private_key(ec.SECP256R1())
    pub_der = key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    proof = key.sign(b"MARK-PAIR-V1\n" + code.encode("utf-8"), ec.ECDSA(hashes.SHA256()))
    device_id = registry.enroll(code, name, b64(pub_der), b64(proof))
    registry.update_device(device_id, "authorized")
    return device_id, key, pub_der


def test_diagnostics_layer_separation_and_zero_secret_leakage(temp_db: Path) -> None:
    """Valida a compilação do estado segregado das camadas e higienização estrita de segredos."""
    registry = DeviceRegistry(temp_db)
    dev_mgr = LinkDeviceManager(registry)
    service = MarkLinkService(device_manager=dev_mgr)

    # 1. Injeta falhas simuladas com dados altamente sensíveis em cada camada
    raw_log_service = "Service binding crashed with secret_key='mock_SuperSecretMasterKey123!'"
    service.telemetry.record_service_failure(raw_log_service)
    service.telemetry.record_lan_failure("LAN discovery failed: private_key_hex=4a5b6c7d8e9f0123456789abcdef0123456789abcdef0123456789abcdef")
    service.telemetry.record_direct_failure("Direct connection timeout: bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9 token expired")
    raw_log_relay = "Relay auth rejected: password='mock_SuperPassword456' invalid"
    service.telemetry.record_relay_failure(raw_log_relay)
    service.telemetry.record_auth_failure("Auth rejected for fake device: token=dummy_token_99999")

    # 2. Coleta o diagnóstico estruturado
    diag = service.get_diagnostics()
    assert isinstance(diag, DiagnosticReport)
    assert hasattr(diag, "layers")
    assert isinstance(diag.layers, NetworkLayersReport)

    # 3. Verifica separação explícita das camadas
    assert diag.layers.service.status in {"ok", "degraded", "down"}
    assert diag.layers.lan.status in {"ok", "degraded", "down", "standby"}
    assert diag.layers.direct.status in {"ok", "degraded", "down", "disconnected"}
    assert diag.layers.relay.status in {"ok", "degraded", "down", "standby"}
    assert diag.layers.auth.status in {"ok", "degraded", "down"}
    assert diag.layers.loopback.status in {"ok", "degraded", "down"}

    # 4. Verifica higienização absoluta de segredos (Zero Leakage)
    raw_json = json.dumps(diag.__dict__, default=lambda o: o.__dict__)

    # Nenhum segredo ou chave privada deve constar no relatório
    assert "SuperSecretMasterKey123!" not in raw_json
    assert "4a5b6c7d8e9f0123456789abcdef0123456789abcdef0123456789abcdef" not in raw_json
    assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in raw_json
    assert "SuperPassword456" not in raw_json

    # Marcadores de redação devem estar presentes
    assert "[REDACTED" in raw_json


def test_encrypted_config_export_creates_authenticated_file_without_private_keys(
    temp_db: Path, tmp_path: Path
) -> None:
    """Valida que a exportação gera um arquivo cifrado (AES-256-GCM) sem conter chaves privadas."""
    registry = DeviceRegistry(temp_db)
    dev_id1, key1, _ = _create_approved_device(registry, "MARK Mobile Alpha")
    dev_id2, key2, _ = _create_approved_device(registry, "MARK Mobile Beta")
    dev_mgr = LinkDeviceManager(registry)

    export_file = tmp_path / "mark_link_config.enc"
    passphrase = "UltraSecureMigrationPassword2026!"

    result = export_link_config(dev_mgr, export_file, passphrase)
    assert result["exported_devices"] == 2
    assert export_file.exists()

    # Valida permissões POSIX seguras (0600)
    mode = os.stat(export_file).st_mode & 0o777
    assert mode == 0o600

    # Valida formato binário autenticado (Magic 'MLCF' + Header)
    content = export_file.read_bytes()
    assert content.startswith(b"MLCF")
    assert len(content) > 64

    # Garante que o arquivo NÃO é texto plano (não contém JSON legível nem chaves)
    assert b"\"devices\"" not in content
    assert b"-----BEGIN EC PRIVATE KEY-----" not in content
    assert b"-----BEGIN PRIVATE KEY-----" not in content

    # Verifica serialização do resultado sem expor chaves privadas
    raw_result_str = json.dumps(result)
    assert "private_key" not in raw_result_str.lower()
    assert dev_id1 in raw_result_str
    assert dev_id2 in raw_result_str


def test_tampered_config_file_rejected_on_import(temp_db: Path, clean_db: Path, tmp_path: Path) -> None:
    """Valida a detecção estrita de adulteração (tampering) e recusa de senha inválida."""
    src_registry = DeviceRegistry(temp_db)
    _create_approved_device(src_registry, "Dispositivo Original")
    src_mgr = LinkDeviceManager(src_registry)

    export_file = tmp_path / "valid_export.enc"
    passphrase = "CorrectPassphrase987!"
    export_link_config(src_mgr, export_file, passphrase)

    dest_registry = DeviceRegistry(clean_db)
    dest_mgr = LinkDeviceManager(dest_registry)

    # 1. Tentativa de importação com senha incorreta
    with pytest.raises(ConfigIntegrityError, match="adulterado|senha incorreta"):
        import_link_config(export_file, "WrongPassword!", dest_mgr)

    # Garante que nenhum dispositivo foi importado após falha de autenticação
    assert len(dest_registry.list_devices()) == 0

    # 2. Adulteração de 1 único byte no ciphertext / auth tag
    data = bytearray(export_file.read_bytes())
    data[-5] ^= 0xFF  # Inverte bits na tag/ciphertext

    tampered_file = tmp_path / "tampered_export.enc"
    tampered_file.write_bytes(bytes(data))

    with pytest.raises(ConfigIntegrityError, match="adulterado|senha incorreta"):
        import_link_config(tampered_file, passphrase, dest_mgr)

    assert len(dest_registry.list_devices()) == 0

    # 3. Adulteração do Magic Header
    header_tampered = bytearray(export_file.read_bytes())
    header_tampered[0] = ord(b"X")
    bad_magic_file = tmp_path / "bad_magic.enc"
    bad_magic_file.write_bytes(bytes(header_tampered))

    with pytest.raises(ConfigIntegrityError, match=r"(?i)magic|cabeçalho|formato inválido"):
        import_link_config(bad_magic_file, passphrase, dest_mgr)


def test_config_export_import_roundtrip_restores_approved_devices(
    temp_db: Path, clean_db: Path, tmp_path: Path
) -> None:
    """Valida o ciclo completo de exportação e restauração legítima com recuperação dos aparelhos."""
    src_registry = DeviceRegistry(temp_db)
    dev_id1, _, pub_der1 = _create_approved_device(src_registry, "Aparelho Pessoal")
    dev_id2, _, pub_der2 = _create_approved_device(src_registry, "Aparelho Trabalho")
    src_mgr = LinkDeviceManager(src_registry)

    export_file = tmp_path / "backup_migration.enc"
    passphrase = "MigrationRoundtripSecret2026!"

    export_res = export_link_config(src_mgr, export_file, passphrase)
    assert export_res["exported_devices"] == 2

    # Verifica integridade do arquivo sem importar
    assert verify_link_config_file(export_file, passphrase) is True
    assert verify_link_config_file(export_file, "BadPassword") is False

    # Importa no destino limpo
    dest_registry = DeviceRegistry(clean_db)
    dest_mgr = LinkDeviceManager(dest_registry)
    import_res = import_link_config(export_file, passphrase, dest_mgr)

    assert import_res["imported_devices"] == 2

    # Valida que ambos os dispositivos agora estão autorizados no novo registro
    assert dest_mgr.is_authorized(dev_id1)
    assert dest_mgr.is_authorized(dev_id2)

    # Valida que as chaves públicas coincidem perfeitamente
    assert dest_mgr.get_public_der(dev_id1) == pub_der1
    assert dest_mgr.get_public_der(dev_id2) == pub_der2

    # Garante que conexões podem ser autorizadas pelo serviço destino
    dest_service = MarkLinkService(device_manager=dest_mgr)
    assert dest_service.authorize_connection(dev_id1) is True
    assert dest_service.authorize_connection(dev_id2) is True

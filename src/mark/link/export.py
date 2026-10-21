"""Módulo de Exportação e Importação Segura de Configuração do MARK Link (Fase 4).

Implementa:
1. Exportação segura da configuração de dispositivos autorizados e rotas em formato binário autenticado (MLCF).
2. Criptografia autenticada AEAD com AES-256-GCM e derivação de chave HKDF-SHA256 (32 bytes de salt).
3. Separação arquitetural estrita: apenas dados recuperáveis (identidades, chaves públicas, rotas)
   são exportados. Chaves privadas do servidor e segredos efêmeros NUNCA são exportados.
4. Detecção e recusa imediata de adulteração (tampering) ou senhas incorretas com ConfigIntegrityError.
5. Permissões POSIX estritas (0600) para todos os arquivos exportados.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import secrets
import time
from typing import Any

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from ..device_auth import b64, unb64
from .identity import LinkDeviceManager

MAGIC_HEADER = b"MLCF"  # MARK Link Config File
VERSION_BYTE = b"\x01"
SALT_LEN = 32
NONCE_LEN = 12
HEADER_LEN = len(MAGIC_HEADER) + len(VERSION_BYTE) + SALT_LEN + NONCE_LEN  # 4 + 1 + 32 + 12 = 49 bytes


class ConfigIntegrityError(Exception):
    """Lançada quando um arquivo de configuração foi adulterado, corrompido ou possui senha inválida."""


def derive_export_key(passphrase: str, salt: bytes) -> bytes:
    """Deriva chave AES-256 (32 bytes) a partir da senha e salt usando HKDF-SHA256."""
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        info=b"mark-link-config-export-v1",
    )
    return hkdf.derive(passphrase.encode("utf-8"))


def export_link_config(
    device_manager: LinkDeviceManager,
    output_path: str | Path,
    passphrase: str,
) -> dict[str, Any]:
    """Exporta a lista de dispositivos autorizados de forma cifrada e autenticada.

    NUNCA exporta chaves privadas do servidor.
    """
    if not isinstance(passphrase, str) or len(passphrase) < 8:
        raise ValueError("A senha de exportação deve conter no mínimo 8 caracteres.")

    out_file = Path(output_path).resolve()
    out_file.parent.mkdir(parents=True, exist_ok=True)

    # 1. Coleta os dispositivos autorizados (apenas metadados e chave pública DER)
    devices_data: list[dict[str, Any]] = []
    with device_manager.registry.lock:
        rows = device_manager.registry.db.execute(
            "SELECT id, name, public_key, status, added FROM devices WHERE status='authorized'"
        ).fetchall()
        for dev_id, name, pub_key_der, status, added in rows:
            devices_data.append(
                {
                    "id": dev_id,
                    "name": name,
                    "public_der_b64": b64(pub_key_der),
                    "status": status,
                    "added_at": added,
                }
            )

    # 2. Constrói o payload estruturado (sem materiais privados do SO)
    payload_dict = {
        "version": 1,
        "exported_at": int(time.time()),
        "devices": devices_data,
        "allowed_routes": [
            "/health",
            "/v1/chat/completions",
            "/v1/voice/turn",
            "/v1/session",
            "/v1/session/renew",
            "/v1/agenda",
            "/v1/history",
        ],
    }
    raw_payload = json.dumps(payload_dict, sort_keys=True).encode("utf-8")

    # 3. Derivação de chave e encriptação autenticada AEAD com AES-256-GCM
    salt = secrets.token_bytes(SALT_LEN)
    nonce = secrets.token_bytes(NONCE_LEN)
    key = derive_export_key(passphrase, salt)

    aesgcm = AESGCM(key)
    aad = MAGIC_HEADER + VERSION_BYTE
    ciphertext = aesgcm.encrypt(nonce, raw_payload, aad)

    output_data = aad + salt + nonce + ciphertext

    # 4. Escrita atômica garantindo modo estrito POSIX 0600
    tmp_path = out_file.with_name(f".{out_file.name}.tmp_{secrets.token_hex(4)}")
    fd = os.open(str(tmp_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(output_data)
        os.replace(tmp_path, out_file)
        os.chmod(out_file, 0o600)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass

    return {
        "exported_devices": len(devices_data),
        "devices": [d["id"] for d in devices_data],
        "output_path": str(out_file),
    }


def import_link_config(
    input_path: str | Path,
    passphrase: str,
    device_manager: LinkDeviceManager,
) -> dict[str, Any]:
    """Importa dispositivos autorizados a partir de um arquivo cifrado e autenticado.

    Valida a integridade dos dados e rejeita qualquer arquivo adulterado ou com senha incorreta.
    """
    if not isinstance(passphrase, str):
        raise ValueError("Senha inválida.")

    in_file = Path(input_path).resolve()
    if not in_file.is_file():
        raise FileNotFoundError(f"Arquivo de exportação não encontrado: {in_file}")

    data = in_file.read_bytes()
    if len(data) < HEADER_LEN + 16:  # 49 + 16 (tag AES-GCM mínima)
        raise ConfigIntegrityError("Arquivo truncado ou cabeçalho inválido.")

    if not data.startswith(MAGIC_HEADER):
        raise ConfigIntegrityError("Magic header inválido no arquivo de exportação.")

    version = data[len(MAGIC_HEADER)]
    if version != 1:
        raise ConfigIntegrityError(f"Versão de exportação não suportada: {version}")

    offset = len(MAGIC_HEADER) + len(VERSION_BYTE)
    salt = data[offset : offset + SALT_LEN]
    offset += SALT_LEN
    nonce = data[offset : offset + NONCE_LEN]
    offset += NONCE_LEN
    ciphertext = data[offset:]

    key = derive_export_key(passphrase, salt)
    aesgcm = AESGCM(key)
    aad = MAGIC_HEADER + VERSION_BYTE

    try:
        raw_payload = aesgcm.decrypt(nonce, ciphertext, aad)
    except Exception as exc:
        raise ConfigIntegrityError(
            "Arquivo de configuração adulterado ou senha incorreta."
        ) from exc

    try:
        payload_dict = json.loads(raw_payload.decode("utf-8"))
    except Exception as exc:
        raise ConfigIntegrityError("Formato JSON de exportação corrompido.") from exc

    # Restauração no registro alvo com validações rigorosas de conformidade
    imported_ids: list[str] = []
    devices = payload_dict.get("devices", [])

    with device_manager.registry.lock, device_manager.registry.db:
        for dev in devices:
            dev_id = dev.get("id")
            name = dev.get("name", "Dispositivo Importado")
            pub_b64 = dev.get("public_der_b64")
            added_at = dev.get("added_at", int(time.time()))

            if not dev_id or not pub_b64:
                continue

            try:
                pub_der = unb64(pub_b64)
                # 1. Valida conformidade da chave EC P-256
                pub_key = serialization.load_der_public_key(pub_der)
                if not isinstance(pub_key, ec.EllipticCurvePublicKey) or not isinstance(
                    pub_key.curve, ec.SECP256R1
                ):
                    continue

                # 2. Valida vínculo do ID com SHA-256 da chave pública
                expected_id = b64(hashlib.sha256(pub_der).digest())
                if expected_id != dev_id:
                    continue

                # 3. Bloqueia se a chave estiver na lista de revogadas permanentemente
                perm = device_manager.registry.db.execute(
                    "SELECT 1 FROM permanently_revoked_keys WHERE device_id=?",
                    (dev_id,),
                ).fetchone()
                if perm:
                    continue

                # 4. Registra dispositivo como autorizado
                device_manager.registry.db.execute(
                    "INSERT OR REPLACE INTO devices (id, name, public_key, status, added) "
                    "VALUES (?, ?, ?, 'authorized', ?)",
                    (dev_id, name, pub_der, added_at),
                )
                imported_ids.append(dev_id)
            except Exception:
                continue

        device_manager.registry._advance_generation()

    return {
        "imported_devices": len(imported_ids),
        "devices": imported_ids,
    }


def verify_link_config_file(input_path: str | Path, passphrase: str) -> bool:
    """Verifica autenticidade e decifrabilidade do arquivo de configuração sem mutar o banco."""
    try:
        in_file = Path(input_path).resolve()
        if not in_file.is_file():
            return False

        data = in_file.read_bytes()
        if len(data) < HEADER_LEN + 16 or not data.startswith(MAGIC_HEADER):
            return False

        version = data[len(MAGIC_HEADER)]
        if version != 1:
            return False

        offset = len(MAGIC_HEADER) + len(VERSION_BYTE)
        salt = data[offset : offset + SALT_LEN]
        offset += SALT_LEN
        nonce = data[offset : offset + NONCE_LEN]
        offset += NONCE_LEN
        ciphertext = data[offset:]

        key = derive_export_key(passphrase, salt)
        aesgcm = AESGCM(key)
        aad = MAGIC_HEADER + VERSION_BYTE
        raw = aesgcm.decrypt(nonce, ciphertext, aad)
        parsed = json.loads(raw.decode("utf-8"))
        return isinstance(parsed, dict) and "devices" in parsed
    except Exception:
        return False

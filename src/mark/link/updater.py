"""Módulo de Atualizações Seguras e Rollback do MARK (Marco C).

Verificação de integridade (SHA-256), autenticidade criptográfica (ECDSA P-256)
e prevenção estrita contra ataques de downgrade (rollback para versão vulnerável).
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec


class UpdateSecurityError(Exception):
    """Erro base para falhas de segurança no processo de atualização."""


class DowngradeError(UpdateSecurityError):
    """Lançada quando a versão da atualização é inferior ou igual à versão atual instalada."""


class UpdateIntegrityError(UpdateSecurityError):
    """Lançada quando o digest SHA-256 do payload não confere com o manifesto."""


class UpdateSignatureError(UpdateSecurityError):
    """Lançada quando a assinatura digital do manifesto de atualização é inválida ou forjada."""


def parse_semver(v: str) -> Tuple[int, ...]:
    """Converte string de versão semântica (ex: 'v1.2.3' ou '2.0.0-rc1') em tupla numérica para comparação."""
    clean = v.strip().lstrip("v").lstrip("V")
    parts: List[int] = []
    for chunk in clean.split("."):
        # Ignora sufixos de pré-release como -rc1, -beta
        sub = chunk.split("-")[0].split("+")[0]
        try:
            parts.append(int(sub))
        except ValueError:
            parts.append(0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def compare_semver(v1: str, v2: str) -> int:
    """Retorna -1 se v1 < v2, 0 se v1 == v2 e 1 se v1 > v2."""
    p1 = parse_semver(v1)
    p2 = parse_semver(v2)
    if p1 < p2:
        return -1
    elif p1 > p2:
        return 1
    return 0


def canonical_update_manifest(
    version: str,
    payload_sha256: str,
    timestamp: int,
    channel: str = "stable",
) -> bytes:
    """Gera a representação canônica da mensagem do manifesto para assinatura e verificação."""
    return f"MARK-UPDATE-V1\nversion:{version.strip()}\nsha256:{payload_sha256.strip().lower()}\ntimestamp:{int(timestamp)}\nchannel:{channel.strip().lower()}".encode(
        "utf-8"
    )


class UpdateManifest:
    """Manifesto assinado de atualização de software."""

    def __init__(
        self,
        version: str,
        payload_sha256: str,
        timestamp: int,
        signature_b64: str,
        channel: str = "stable",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.version = version
        self.payload_sha256 = payload_sha256.strip().lower()
        self.timestamp = timestamp
        self.signature_b64 = signature_b64.strip()
        self.channel = channel
        self.metadata = metadata or {}

    def canonical_bytes(self) -> bytes:
        return canonical_update_manifest(
            self.version, self.payload_sha256, self.timestamp, self.channel
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "payload_sha256": self.payload_sha256,
            "timestamp": self.timestamp,
            "signature_b64": self.signature_b64,
            "channel": self.channel,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> UpdateManifest:
        return cls(
            version=str(data["version"]),
            payload_sha256=str(data["payload_sha256"]),
            timestamp=int(data["timestamp"]),
            signature_b64=str(data["signature_b64"]),
            channel=str(data.get("channel", "stable")),
            metadata=data.get("metadata", {}),
        )


class UpdateVerifier:
    """Validador e aplicador de atualizações com verificação de assinatura, hash e anti-downgrade."""

    def __init__(
        self,
        current_version: str,
        release_public_key: Union[ec.EllipticCurvePublicKey, bytes, str, None] = None,
    ) -> None:
        self.current_version = current_version
        self.release_public_key: Optional[ec.EllipticCurvePublicKey] = None
        if release_public_key is not None:
            if isinstance(release_public_key, ec.EllipticCurvePublicKey):
                self.release_public_key = release_public_key
            elif isinstance(release_public_key, (bytes, bytearray)):
                raw = bytes(release_public_key)
                if raw.startswith(b"-----BEGIN"):
                    key = serialization.load_pem_public_key(raw)
                else:
                    key = serialization.load_der_public_key(raw)
                if not isinstance(key, ec.EllipticCurvePublicKey):
                    raise ValueError("Chave pública de release deve ser EC P-256")
                self.release_public_key = key
            elif isinstance(release_public_key, str):
                pem_bytes = release_public_key.encode("utf-8")
                key = serialization.load_pem_public_key(pem_bytes)
                if not isinstance(key, ec.EllipticCurvePublicKey):
                    raise ValueError("Chave pública de release deve ser EC P-256")
                self.release_public_key = key

    def verify_update(
        self,
        manifest: Union[UpdateManifest, Dict[str, Any]],
        payload: bytes,
    ) -> Dict[str, Any]:
        """Executa verificação tripla: Anti-Downgrade, Integridade SHA-256 e Assinatura ECDSA P-256."""
        m = manifest if isinstance(manifest, UpdateManifest) else UpdateManifest.from_dict(manifest)

        # 1. Prevenção estrita contra Downgrade (Anti-Downgrade)
        cmp_result = compare_semver(m.version, self.current_version)
        if cmp_result <= 0:
            raise DowngradeError(
                f"Tentativa de downgrade bloqueada: versão pretendida ({m.version}) "
                f"é inferior ou igual à versão atual instalada ({self.current_version})."
            )

        # 2. Verificação de integridade binária SHA-256
        actual_hash = hashlib.sha256(payload).hexdigest().lower()
        if actual_hash != m.payload_sha256.lower():
            raise UpdateIntegrityError(
                f"Hash SHA-256 corrompido ou adulterado. Calculado: {actual_hash}, esperado: {m.payload_sha256}."
            )

        # 3. Verificação da Assinatura ECDSA P-256 da chave autorizada de Release
        if self.release_public_key is None:
            raise UpdateSignatureError(
                "Chave pública de release não configurada; verificação criptográfica de assinatura obrigatória."
            )

        sig_bytes = self._decode_signature(m.signature_b64)
        canonical_data = m.canonical_bytes()
        try:
            self.release_public_key.verify(
                sig_bytes,
                canonical_data,
                ec.ECDSA(hashes.SHA256()),
            )
        except InvalidSignature as exc:
            raise UpdateSignatureError(
                "Assinatura criptográfica do manifesto de atualização inválida ou forjada."
            ) from exc
        except Exception as exc:
            raise UpdateSignatureError(f"Falha na validação da assinatura: {exc}") from exc

        return {
            "valid": True,
            "version": m.version,
            "payload_size": len(payload),
            "sha256": actual_hash,
            "timestamp": m.timestamp,
            "channel": m.channel,
        }

    def apply_update(
        self,
        manifest: Union[UpdateManifest, Dict[str, Any]],
        payload: bytes,
        target_path: Path | str,
        backup_path: Path | str | None = None,
    ) -> bool:
        """Aplica atualização atômica após validação total, permitindo rollback se necessário."""
        self.verify_update(manifest, payload)
        target = Path(target_path)

        if target.exists() and backup_path is not None:
            bkp = Path(backup_path)
            bkp.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, bkp)

        target.parent.mkdir(parents=True, exist_ok=True)
        # Escrita atômica via arquivo temporário com permissões seguras (0600)
        with tempfile.NamedTemporaryFile(
            dir=target.parent, delete=False, prefix="mark_upd_"
        ) as tmp:
            tmp.write(payload)
            tmp_path = Path(tmp.name)

        os.chmod(tmp_path, 0o600)
        tmp_path.replace(target)
        self.current_version = (
            manifest.version if isinstance(manifest, UpdateManifest) else manifest["version"]
        )
        return True

    def rollback(self, target_path: Path | str, backup_path: Path | str) -> bool:
        """Restaura versão anterior a partir de backup conhecido."""
        target = Path(target_path)
        bkp = Path(backup_path)
        if not bkp.is_file():
            raise FileNotFoundError(f"Arquivo de backup não encontrado em: {backup_path}")
        shutil.copy2(bkp, target)
        os.chmod(target, 0o600)
        return True

    @staticmethod
    def _decode_signature(sig_b64: str) -> bytes:
        sig_clean = sig_b64.strip()
        try:
            return base64.urlsafe_b64decode(sig_clean + "=" * (-len(sig_clean) % 4))
        except Exception:
            return base64.b64decode(sig_clean + "=" * (-len(sig_clean) % 4))

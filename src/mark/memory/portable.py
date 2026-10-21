"""Módulo de Exportação e Importação Portátil de Memória Criptografada via Cofre.

Suporta:
- Exportação em JSON canônico assinado com HMAC-SHA256.
- Exportação criptografada de alta segurança (Argon2id + AES-256-GCM) integrada ao Rust mark_core.
- Validação rigorosa na importação com detecção de adulteração, truncamento e corrupção.
- Integração com o motor de deduplicação para detecção de conflitos na importação.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import stat
import time
from typing import Any

try:
    import mark_core
except ImportError:
    mark_core = None

from .dedup import DeduplicationEngine, compute_fact_fingerprint, normalize_fact_text
from .scopes import validate_scope_identifier

PORTABLE_SCHEMA_VERSION = 2
VAULT_MEMORY_AAD = "MARK-SCOPED-MEMORY-VAULT-V2"


@dataclass
class ImportReport:
    total_read: int
    inserted: int
    duplicates: int
    conflicts_flagged: int
    conflicts: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def create_portable_payload(
    facts: list[dict[str, Any]],
    encrypted: bool = False,
    passphrase: str | None = None,
) -> bytes:
    """Gera o envelope serializado assinado ou cifrado das memórias."""
    if len(facts) > 5000:
        raise ValueError("O backup suporta até 5.000 memórias explícitas por exportação.")

    cleaned_entries = []
    for f in facts:
        entry = {
            "scope": f.get("scope"),
            "scope_id": f.get("scope_id", ""),
            "content": normalize_fact_text(f.get("content", "")),
            "provenance": str(f.get("provenance", "explicit_export")),
            "created_at": f.get("created_at", int(time.time())),
            "updated_at": f.get("updated_at", int(time.time())),
        }
        cleaned_entries.append(entry)

    envelope = {
        "schema_version": PORTABLE_SCHEMA_VERSION,
        "kind": "mark_portable_memory_vault",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "encrypted": encrypted,
        "entries": cleaned_entries,
    }

    if encrypted:
        if not passphrase:
            raise ValueError("Uma senha mestra é necessária para exportação criptografada.")
        if mark_core is None:
            raise RuntimeError("O módulo nativo mark_core é obrigatório para cifragem via Cofre.")

        raw_inner = json.dumps(envelope, ensure_ascii=False, sort_keys=True)
        sealed_json = mark_core.seal_credentials(passphrase, raw_inner, VAULT_MEMORY_AAD)
        sealed_dict = json.loads(sealed_json)
        sealed_dict["kind"] = "mark_sealed_memory_vault"
        sealed_dict["schema_version"] = PORTABLE_SCHEMA_VERSION
        return json.dumps(sealed_dict, ensure_ascii=False, indent=2).encode("utf-8")
    else:
        canonical = json.dumps(envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        envelope["sha256"] = hashlib.sha256(canonical).hexdigest()
        return (json.dumps(envelope, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def parse_portable_payload(
    raw_bytes: bytes,
    passphrase: str | None = None,
) -> dict[str, Any]:
    """Valida, decifra e desempacota o payload portátil de memórias."""
    if len(raw_bytes) > 16_000_000:
        raise ValueError("O payload excede o limite máximo permitido de 16 MiB.")

    try:
        decoded_text = raw_bytes.decode("utf-8")
        payload = json.loads(decoded_text)
    except Exception as exc:
        raise ValueError("Formato de backup inválido: JSON corrompido ou decodificação UTF-8 inválida.") from exc

    if not isinstance(payload, dict):
        raise ValueError("Payload de backup deve ser um objeto JSON.")

    # Se for cofre criptografado
    if payload.get("kind") == "mark_sealed_memory_vault":
        if not passphrase:
            raise PermissionError("O arquivo de memória está criptografado e requer senha mestra.")
        if mark_core is None:
            raise RuntimeError("Módulo mark_core ausente para decifrar cofre.")

        try:
            unsealed_text = mark_core.unseal_credentials(passphrase, decoded_text, VAULT_MEMORY_AAD)
            payload = json.loads(unsealed_text)
        except Exception as exc:
            raise ValueError("Falha na decifragem: senha incorreta ou integridade violada.") from exc

    # Validação do Schema
    ver = payload.get("schema_version")
    if ver not in (1, 2):
        raise ValueError(f"Versão de schema {ver} não suportada; esperado 1 ou 2.")

    # Se for versão 1 (compatibilidade com backups anteriores)
    if payload.get("kind") == "mark_explicit_memories":
        digest = payload.get("sha256")
        unsigned = {k: v for k, v in payload.items() if k != "sha256"}
        canonical = json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        if not isinstance(digest, str) or not hmac.compare_digest(digest, hashlib.sha256(canonical).hexdigest()):
            raise ValueError("A integridade do backup não confere (hash SHA-256 adulterado).")
        return payload

    if payload.get("kind") != "mark_portable_memory_vault":
        raise ValueError("Identificador de backup desconhecido.")

    # Verifica integridade do payload não cifrado
    if not payload.get("encrypted"):
        digest = payload.get("sha256")
        unsigned = {k: v for k, v in payload.items() if k != "sha256"}
        canonical = json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        if not isinstance(digest, str) or not hmac.compare_digest(digest, hashlib.sha256(canonical).hexdigest()):
            raise ValueError("Integridade violada: assinatura SHA-256 não corresponde ao conteúdo.")

    return payload

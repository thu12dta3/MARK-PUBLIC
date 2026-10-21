"""Módulo de Registro de Auditoria Estruturado e Privado do MARK (Fase P3).

Implementa trilha de auditoria contínua e imutável para registrar:
1. Identidade do dispositivo solicitante (device_id).
2. Ferramenta MCP ou agente autônomo invocado (target / tool / agent).
3. Decisão de autorização explícita (ALLOWED / DENIED / CHALLENGE_REQUIRED).
4. Resultado operacional da execução (SUCCESS / FAILED / BLOCKED / ERROR).

Garantias Estritas de Segurança e Privacidade:
- Higienização e sanitização absoluta: segredos, credenciais, tokens Bearer, JWTs,
  chaves privadas PEM, senhas e payloads confidenciais são purgados antes da persistência.
- Imutabilidade e detecção de adulteração: encadeamento criptográfico de hashes
  (SHA-256 hash chaining estilo ledger seguro).
- Permissões POSIX privadas: diretório 0700 e arquivo de log 0600.
"""

from __future__ import annotations

import copy
import dataclasses
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import threading
from typing import Any, Mapping, Sequence
import uuid

from .cognition.storage import ensure_private_dir, private_file
from .logging_sanitizer import (
    SENSITIVE_DICT_KEYS,
    sanitize_diagnostic_report,
    sanitize_log_text,
)

log = logging.getLogger("mark.audit_logger")

GENESIS_HASH = "0" * 64

# Expressão regular complementar para sanitizar pares chave-valor em strings de log
_RE_EXTRA_KV_SECRETS = re.compile(
    r"(?i)([\"']?(?:password|passwd|secret|api_key|apikey|access_token|client_secret|master_key|auth_token|key)[\"']?\s*[:=]\s*[\"']?)[^\"',\s}\n\r]+",
)

# Chaves adicionais estritamente proibidas de armazenar conteúdo bruto na auditoria
AUDIT_FORBIDDEN_KEYS = frozenset(
    SENSITIVE_DICT_KEYS
    | {
        "authorization",
        "cookie",
        "x-mark-token",
        "payload",
        "raw_payload",
        "body",
        "prompt",
        "code",
        "signature",
        "private_key_pem",
        "secret_key",
        "master_key",
        "credential",
        "credentials",
        "raw_query",
        "key",
        "api_key",
    }
)


@dataclass(frozen=True)
class AuditRecord:
    """Registro individual estruturado de auditoria de segurança."""

    record_id: str
    timestamp: str
    event_type: str
    device_id: str
    subject_id: str
    target: str
    action: str
    decision: str
    status: str
    status_code: int
    latency_ms: float
    reason: str
    metadata: dict[str, Any]
    prev_hash: str
    record_hash: str

    def to_dict(self) -> dict[str, Any]:
        """Converte o registro para dicionário serializável."""
        return asdict(self)

    def compute_canonical_hash(self) -> str:
        """Calcula o hash SHA-256 canônico do conteúdo do registro (excluindo record_hash)."""
        data = {
            "record_id": self.record_id,
            "timestamp": self.timestamp,
            "event_type": self.event_type,
            "device_id": self.device_id,
            "subject_id": self.subject_id,
            "target": self.target,
            "action": self.action,
            "decision": self.decision,
            "status": self.status,
            "status_code": self.status_code,
            "latency_ms": round(float(self.latency_ms), 3),
            "reason": self.reason,
            "metadata": self.metadata,
            "prev_hash": self.prev_hash,
        }
        canonical_bytes = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        return hashlib.sha256(canonical_bytes).hexdigest()


class AuditLogger:
    """Registrador de auditoria estruturado com hash chaining e higienização estrita de dados."""

    def __init__(self, log_path: Path | str | None = None, *, sanitize: bool = True):
        if log_path is None:
            state_dir = Path.home() / ".local" / "state" / "mark"
            self.log_path = state_dir / "audit.jsonl"
        else:
            self.log_path = Path(log_path).resolve()

        self.sanitize = bool(sanitize)
        self._lock = threading.RLock()
        self._last_hash = GENESIS_HASH

        self._ensure_storage()
        self._sync_last_hash_from_disk()

    def _ensure_storage(self) -> None:
        """Garante que o diretório pai possui permissão 0700 e o arquivo de log 0600."""
        parent = self.log_path.parent
        ensure_private_dir(parent)
        if not self.log_path.exists():
            with self.log_path.open("a", encoding="utf-8") as f:
                pass
            try:
                self.log_path.chmod(0o600)
            except OSError:
                pass
        else:
            try:
                self.log_path.chmod(0o600)
            except OSError:
                pass

    def _sync_last_hash_from_disk(self) -> None:
        """Lê o último registro gravado em disco para reconstruir o estado do hash chaining."""
        with self._lock:
            if not self.log_path.exists() or self.log_path.stat().st_size == 0:
                self._last_hash = GENESIS_HASH
                return

            last_line = ""
            try:
                with self.log_path.open("r", encoding="utf-8", errors="replace") as f:
                    for line in f:
                        line_str = line.strip()
                        if line_str:
                            last_line = line_str
                if last_line:
                    parsed = json.loads(last_line)
                    self._last_hash = str(parsed.get("record_hash", GENESIS_HASH))
                else:
                    self._last_hash = GENESIS_HASH
            except Exception as exc:
                log.warning(f"Falha ao ler último hash da trilha de auditoria: {exc}")
                self._last_hash = GENESIS_HASH

    def scrub_data(self, data: Any) -> Any:
        """Sanitiza recursivamente qualquer estrutura para garantir privacidade total."""
        if not self.sanitize:
            return data

        if isinstance(data, dict):
            scrubbed: dict[str, Any] = {}
            for k, v in data.items():
                key_str = str(k).lower().strip()
                if key_str in AUDIT_FORBIDDEN_KEYS or any(s in key_str for s in SENSITIVE_DICT_KEYS):
                    scrubbed[k] = "[REDACTED]"
                else:
                    scrubbed[k] = self.scrub_data(v)
            return scrubbed

        if isinstance(data, (list, tuple, set)):
            return [self.scrub_data(item) for item in data]

        if isinstance(data, str):
            # Higieniza segredos via logging_sanitizer e regex complementar
            sanitized = sanitize_log_text(data)
            sanitized = _RE_EXTRA_KV_SECRETS.sub(r"\1[REDACTED]", sanitized)
            # Trunca dados volumosos para evitar injeção de payloads brutos
            if len(sanitized) > 512:
                sanitized = sanitized[:512] + "...[TRUNCATED_FOR_AUDIT]"
            return sanitized

        if isinstance(data, bytes):
            return f"[BYTES:{len(data)}]"

        return data

    def log_event(
        self,
        *,
        event_type: str,
        device_id: str,
        target: str,
        decision: str,
        status: str,
        subject_id: str = "system",
        action: str = "invoke",
        status_code: int = 200,
        latency_ms: float = 0.0,
        reason: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> AuditRecord:
        """Grava um registro de auditoria estruturado com encadeamento de hash canônico."""
        with self._lock:
            now_iso = datetime.now(timezone.utc).isoformat()
            record_id = uuid.uuid4().hex

            # Sanitização estrita de todos os campos
            clean_device = str(self.scrub_data(device_id or "unknown"))
            clean_subject = str(self.scrub_data(subject_id or "system"))
            clean_target = str(self.scrub_data(target or "unspecified"))
            clean_action = str(self.scrub_data(action or "invoke"))
            clean_decision = str(decision).upper().strip()
            clean_status = str(status).upper().strip()
            clean_reason = str(self.scrub_data(reason or ""))
            clean_metadata = self.scrub_data(metadata or {})
            if not isinstance(clean_metadata, dict):
                clean_metadata = {"raw": str(clean_metadata)}

            prev_hash = self._last_hash

            # Cria registro preliminar para calcular hash canônico
            record = AuditRecord(
                record_id=record_id,
                timestamp=now_iso,
                event_type=str(event_type).upper().strip(),
                device_id=clean_device,
                subject_id=clean_subject,
                target=clean_target,
                action=clean_action,
                decision=clean_decision,
                status=clean_status,
                status_code=int(status_code),
                latency_ms=round(float(latency_ms), 3),
                reason=clean_reason,
                metadata=clean_metadata,
                prev_hash=prev_hash,
                record_hash="",
            )

            record_hash = record.compute_canonical_hash()
            final_record = dataclasses.replace(record, record_hash=record_hash)

            # Persiste no arquivo append-only JSONL
            line = json.dumps(final_record.to_dict(), separators=(",", ":"), ensure_ascii=True)
            with self.log_path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")

            # Atualiza o encadeamento
            self._last_hash = record_hash
            return final_record

    def log_auth_decision(
        self,
        *,
        device_id: str,
        target: str,
        allowed: bool,
        reason: str = "",
        metadata: dict[str, Any] | None = None,
        subject_id: str = "system",
        status_code: int | None = None,
    ) -> AuditRecord:
        """Registra especificamente uma decisão de autorização de dispositivo ou chamada."""
        decision = "ALLOWED" if allowed else "DENIED"
        status = "SUCCESS" if allowed else "BLOCKED"
        code = status_code if status_code is not None else (200 if allowed else 403)
        return self.log_event(
            event_type="AUTH_DECISION",
            device_id=device_id,
            target=target,
            action="authorize",
            decision=decision,
            status=status,
            status_code=code,
            reason=reason,
            metadata=metadata,
            subject_id=subject_id,
        )

    def log_tool_invocation(
        self,
        *,
        device_id: str,
        tool_name: str,
        allowed: bool,
        status: str = "SUCCESS",
        status_code: int = 0,
        latency_ms: float = 0.0,
        reason: str = "",
        metadata: dict[str, Any] | None = None,
        subject_id: str = "system",
    ) -> AuditRecord:
        """Registra a invocação de uma ferramenta MCP ou rotina do sistema."""
        decision = "ALLOWED" if allowed else "DENIED"
        return self.log_event(
            event_type="TOOL_INVOCATION",
            device_id=device_id,
            target=f"mcp:{tool_name}",
            action="execute",
            decision=decision,
            status=status,
            status_code=status_code,
            latency_ms=latency_ms,
            reason=reason,
            metadata=metadata,
            subject_id=subject_id,
        )

    def log_agent_invocation(
        self,
        *,
        device_id: str,
        agent_name: str,
        allowed: bool,
        status: str = "SUCCESS",
        latency_ms: float = 0.0,
        reason: str = "",
        metadata: dict[str, Any] | None = None,
        subject_id: str = "system",
    ) -> AuditRecord:
        """Registra a invocação de um agente autônomo (ex: EngineerAgent, ProfessorAgent)."""
        decision = "ALLOWED" if allowed else "DENIED"
        return self.log_event(
            event_type="AGENT_INVOCATION",
            device_id=device_id,
            target=f"agent:{agent_name}",
            action="dispatch",
            decision=decision,
            status=status,
            latency_ms=latency_ms,
            reason=reason,
            metadata=metadata,
            subject_id=subject_id,
        )

    def log_incident(
        self,
        *,
        incident_type: str,
        device_id: str,
        severity: str,
        details: str,
        action_taken: str,
        metadata: dict[str, Any] | None = None,
    ) -> AuditRecord:
        """Registra um incidente de segurança ou tentativa de ataque detectada."""
        meta = dict(metadata or {})
        meta["severity"] = str(severity).upper()
        meta["action_taken"] = str(action_taken)
        return self.log_event(
            event_type="INCIDENT_ALERT",
            device_id=device_id,
            target=f"incident:{incident_type}",
            action="containment",
            decision="DENIED",
            status="BLOCKED",
            status_code=400,
            reason=details,
            metadata=meta,
        )

    def verify_log_integrity(self) -> tuple[bool, str]:
        """Verifica a integridade criptográfica da cadeia de hashes do arquivo de log."""
        with self._lock:
            if not self.log_path.exists():
                return True, "Trilha de auditoria vazia (arquivo inexistente)."

            expected_prev_hash = GENESIS_HASH
            line_count = 0

            with self.log_path.open("r", encoding="utf-8", errors="replace") as f:
                for line_idx, line in enumerate(f, start=1):
                    line_str = line.strip()
                    if not line_str:
                        continue
                    line_count += 1
                    try:
                        record_dict = json.loads(line_str)
                    except json.JSONDecodeError as err:
                        return False, f"Linha {line_idx} corrompida (JSON inválido): {err}"

                    stored_hash = record_dict.get("record_hash", "")
                    stored_prev_hash = record_dict.get("prev_hash", "")

                    if stored_prev_hash != expected_prev_hash:
                        return False, (
                            f"Quebra de encadeamento na linha {line_idx}: "
                            f"prev_hash={stored_prev_hash} diverge do esperado={expected_prev_hash}"
                        )

                    rec = AuditRecord(
                        record_id=record_dict["record_id"],
                        timestamp=record_dict["timestamp"],
                        event_type=record_dict["event_type"],
                        device_id=record_dict["device_id"],
                        subject_id=record_dict["subject_id"],
                        target=record_dict["target"],
                        action=record_dict["action"],
                        decision=record_dict["decision"],
                        status=record_dict["status"],
                        status_code=record_dict["status_code"],
                        latency_ms=record_dict["latency_ms"],
                        reason=record_dict["reason"],
                        metadata=record_dict.get("metadata", {}),
                        prev_hash=stored_prev_hash,
                        record_hash="",
                    )
                    recomputed_hash = rec.compute_canonical_hash()
                    if recomputed_hash != stored_hash:
                        return False, (
                            f"Adulteração detectada na linha {line_idx}: "
                            f"hash armazenado={stored_hash} != hash recalculado={recomputed_hash}"
                        )

                    expected_prev_hash = stored_hash

            return True, f"Integridade verificada com sucesso em {line_count} registros."

    def query_records(
        self,
        *,
        device_id: str | None = None,
        target: str | None = None,
        decision: str | None = None,
        event_type: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[AuditRecord]:
        """Consulta registros filtrados da trilha de auditoria."""
        with self._lock:
            if not self.log_path.exists():
                return []

            matched: list[AuditRecord] = []
            with self.log_path.open("r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line_str = line.strip()
                    if not line_str:
                        continue
                    try:
                        d = json.loads(line_str)
                    except Exception:
                        continue

                    if device_id and d.get("device_id") != device_id:
                        continue
                    if target and d.get("target") != target:
                        continue
                    if decision and d.get("decision") != decision.upper():
                        continue
                    if event_type and d.get("event_type") != event_type.upper():
                        continue
                    if status and d.get("status") != status.upper():
                        continue

                    rec = AuditRecord(
                        record_id=d["record_id"],
                        timestamp=d["timestamp"],
                        event_type=d["event_type"],
                        device_id=d["device_id"],
                        subject_id=d["subject_id"],
                        target=d["target"],
                        action=d["action"],
                        decision=d["decision"],
                        status=d["status"],
                        status_code=d["status_code"],
                        latency_ms=d["latency_ms"],
                        reason=d["reason"],
                        metadata=d.get("metadata", {}),
                        prev_hash=d["prev_hash"],
                        record_hash=d["record_hash"],
                    )
                    matched.append(rec)
                    if len(matched) >= limit:
                        break

            return matched

    def get_summary(self) -> dict[str, Any]:
        """Gera resumo consolidado das operações e decisões de segurança registradas."""
        with self._lock:
            total = 0
            allowed = 0
            denied = 0
            blocked = 0
            by_target: dict[str, int] = {}

            if self.log_path.exists():
                with self.log_path.open("r", encoding="utf-8", errors="replace") as f:
                    for line in f:
                        line_str = line.strip()
                        if not line_str:
                            continue
                        try:
                            d = json.loads(line_str)
                        except Exception:
                            continue

                        total += 1
                        dec = d.get("decision", "")
                        st = d.get("status", "")
                        tgt = d.get("target", "unknown")

                        if dec == "ALLOWED":
                            allowed += 1
                        elif dec == "DENIED":
                            denied += 1

                        if st == "BLOCKED":
                            blocked += 1

                        by_target[tgt] = by_target.get(tgt, 0) + 1

            return {
                "total_events": total,
                "allowed_events": allowed,
                "denied_events": denied,
                "blocked_events": blocked,
                "unique_targets_count": len(by_target),
                "last_hash": self._last_hash,
            }


_GLOBAL_AUDIT_LOGGER: AuditLogger | None = None
_GLOBAL_LOCK = threading.Lock()


def get_audit_logger(log_path: Path | str | None = None) -> AuditLogger:
    """Retorna instância singleton do AuditLogger ou instancia uma com caminho específico."""
    global _GLOBAL_AUDIT_LOGGER
    if log_path is not None:
        return AuditLogger(log_path)

    with _GLOBAL_LOCK:
        if _GLOBAL_AUDIT_LOGGER is None:
            _GLOBAL_AUDIT_LOGGER = AuditLogger()
        return _GLOBAL_AUDIT_LOGGER

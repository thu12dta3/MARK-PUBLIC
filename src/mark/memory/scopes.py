"""Definições de Escopos e Particionamento de Memória Persistente do MARK.

Escopos suportados:
- GLOBAL: Preferências e fatos gerais do usuário válidos em qualquer sessão.
- SPECIALIZATION: Fatos vinculados ao modo de atuação (ex.: Professor, Engenheiro).
- PROJECT: Fatos restritos a um repositório ou projeto específico.
- TASK: Fatos efêmeros vinculados à execução de uma tarefa ativa específica.

Regra de Ouro:
A memória persistente só ocorre por salvamento explícito do usuário ou regras pré-definidas.
Logs de conversa rotineiros NUNCA são convertidos silenciosamente em memória persistente.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
import re
import time
from typing import Any

from ..core.context import CoreContext


class MemoryScope(str, Enum):
    GLOBAL = "global"
    SPECIALIZATION = "specialization"
    PROJECT = "project"
    TASK = "task"


_VALID_SCOPES = {s.value for s in MemoryScope}


@dataclass
class ScopedMemory:
    id: int | None
    scope: MemoryScope | str
    scope_id: str
    content: str
    fingerprint: str
    provenance: str = "explicit_user_request"
    created_at: int = field(default_factory=lambda: int(time.time()))
    updated_at: int = field(default_factory=lambda: int(time.time()))
    status: str = "ACTIVE"  # ACTIVE, CONFLICT_PENDING_RESOLUTION, ARCHIVED
    conflict_details: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if isinstance(self.scope, MemoryScope):
            d["scope"] = self.scope.value
        return d


def validate_scope_identifier(scope: str | MemoryScope, scope_id: str | None, context: CoreContext | None = None) -> str:
    """Valida o identificador de escopo de acordo com as regras rígidas de contexto."""
    scope_str = scope.value if isinstance(scope, MemoryScope) else str(scope)
    if scope_str not in _VALID_SCOPES:
        raise ValueError(f"Escopo de memória inválido: {scope_str}. Permitidos: {', '.join(_VALID_SCOPES)}")

    if scope_str == MemoryScope.GLOBAL.value:
        return ""

    if scope_id is None:
        if context is None:
            raise ValueError(f"Contexto necessário para resolver o identificador do escopo '{scope_str}'")
        resolved = {
            MemoryScope.SPECIALIZATION.value: context.specialization,
            MemoryScope.PROJECT.value: context.project_id,
            MemoryScope.TASK.value: context.task_id,
        }.get(scope_str)
        if not resolved:
            raise ValueError(f"O contexto atual não possui identificador para o escopo '{scope_str}'")
        scope_id = resolved

    if not isinstance(scope_id, str) or not scope_id.strip():
        raise ValueError(f"Identificador de escopo não pode ser vazio para '{scope_str}'")

    if len(scope_id) > 128:
        raise ValueError("Identificador de escopo longo demais (máximo 128 caracteres)")

    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:\-]{0,127}", scope_id):
        raise ValueError(f"Identificador de escopo contém caracteres inválidos: '{scope_id}'")

    return scope_id.strip()


def resolve_query_scopes(context: CoreContext | None = None) -> list[tuple[str, str]]:
    """Gera a cascata de escopos permitidos para leitura sem vazamento cruzado."""
    ctx = context if isinstance(context, CoreContext) else CoreContext.create()
    allowed = [(MemoryScope.GLOBAL.value, "")]

    if ctx.specialization:
        allowed.append((MemoryScope.SPECIALIZATION.value, ctx.specialization))

    if ctx.project_id:
        allowed.append((MemoryScope.PROJECT.value, ctx.project_id))

    if ctx.task_id:
        allowed.append((MemoryScope.TASK.value, ctx.task_id))

    return allowed

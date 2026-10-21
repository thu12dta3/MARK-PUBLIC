
from __future__ import annotations

import re
import unicodedata

from .missions import MissionStore


_DELEGATE = re.compile(r"\b(peca|pede|mande|manda|mandar|envie|envia|solicite|solicita|crie|cria|inicie|inicia|chame|chama)\b")
_ENGINEER = re.compile(r"\b(engineer|engenheiro)\b")
_WORK = re.compile(r"\b(revisar|revise|revisao|analisar|analise|corrigir|corrija|investigar|investigue|implementar|implemente|testar|teste|verificar|verifique|resolver|resolva)\b")
_STATUS = re.compile(r"\b(status|andamento|progresso|como esta|como vai|em que pe|ja terminou|terminou)\b")
_TASK = re.compile(r"\b(missao|tarefa|engineer|engenheiro|revisao|revisar|analise|projeto)\b")
_ID = re.compile(r"\b([0-9a-f]{8,32})\b", re.I)

_STATE_PT = {
    "WAITING_APPROVAL": "aguardando autorização local",
    "QUEUED": "na fila",
    "RUNNING": "em execução",
    "PAUSED": "pausada",
    "BLOCKED": "bloqueada para revisão",
    "VERIFYING": "em verificação",
    "COMPLETED": "concluída",
    "FAILED": "falhou",
    "CANCELLED": "cancelada",
    "CREATED": "rascunho",
}


def _normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def is_mission_status_question(text: str) -> bool:
    normalized = _normalize(text)
    return bool(_STATUS.search(normalized) and _TASK.search(normalized))


def is_engineer_delegation(text: str) -> bool:
    normalized = _normalize(text)
    return bool(_ENGINEER.search(normalized) and _DELEGATE.search(normalized) and _WORK.search(normalized))


def handle_general_mission_request(text: str, store: MissionStore, origin: str) -> str | None:

    normalized = _normalize(text)
    if is_mission_status_question(text):
        missions = store.list_missions()
        short_id = _ID.search(text)
        if short_id:
            matches = [item for item in missions if item["id"].startswith(short_id.group(1).lower())]
        else:
            active = {"WAITING_APPROVAL", "QUEUED", "RUNNING", "PAUSED", "BLOCKED", "VERIFYING"}
            candidates = [item for item in missions if item["state"] in active]
            if re.search(r"\b(revisao|revisar|review)\b", normalized):
                matching = [item for item in candidates if re.search(r"\b(revisao|revisar|review)\b", _normalize(item["objective"]))]
                if matching:
                    candidates = matching
            matches = candidates[:1] or missions[:1]
        if not matches:
            return "Não há missões registradas para consultar ainda."
        if not short_id and len(candidates) > 1:
            recent = candidates[:5]
            options = "; ".join(f"{item['id'][:8]} · {_STATE_PT.get(item['state'], item['state'])}: {item['objective'][:100]}" for item in recent)
            return "Encontrei mais de uma missão ativa. Diga os primeiros caracteres do ID: " + options
        mission = matches[0]
        steps = mission.get("steps", [])
        completed = sum(step.get("state") == "COMPLETED" for step in steps)
        counts = f"{completed} de {len(steps)} etapa(s) concluída(s)" if steps else "nenhuma etapa registrada"
        return (f"Missão {mission['id'][:8]}: {_STATE_PT.get(mission['state'], mission['state'])}. "
                f"{mission['objective']}. {counts}.")

    if is_engineer_delegation(text):
        if origin not in {"pc", "celular", "relógio"}:
            return "Posso registrar esse pedido depois que ele vier de uma conversa autenticada do MARK."
        mission_id = store.create_conversation_draft(text.strip())
        return (f"Registrei o rascunho da missão {mission_id[:8]}. Nenhum arquivo foi lido ou alterado. "
                "Abra Engenheiro → Tarefas no PC, escolha o workspace e revise as permissões para autorizar. "
                "A missão só começa depois dessa autorização local.")
    return None

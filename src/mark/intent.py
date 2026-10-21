
from __future__ import annotations

import json
import logging
import re

from .brain import _ollama, normalize

log = logging.getLogger("mark.intent")


def control_command(text: str) -> str | None:

    command = " ".join(normalize(text).split())
    memory_patterns = (
        r"(?:limpar|limpa|limpe|apagar|apaga|apague) (?:a )?memoria(?: recente)?(?: por favor)?",
        r"(?:esquecer|esquece|esqueca) (?:a )?(?:conversa|memoria|tudo)(?: por favor)?",
    )
    shutdown_pattern = r"(?:desligar|desliga|desligue|encerrar|encerra|encerre|parar|para|pare)(?: mark)?(?: por favor)?"
    if any(re.fullmatch(pattern, command) for pattern in memory_patterns):
        return "clear_memory"
    if re.fullmatch(shutdown_pattern, command):
        return "shutdown"
    return None


def sleep_command(text: str, cfg: dict | None = None) -> bool:

    command = " ".join(normalize(text).split())
    command = re.sub(r"^(?:mark|marc|marque|marck)\s+", "", command)
    command = re.sub(r"(?:\s+(?:por favor|obrigado|obrigada))+$", "", command)
    endings = r"(?:\s+(?:agora|por agora|por enquanto|por hoje|um pouco|ai))?"
    patterns = (
        rf"(?:(?:voce|vc)\s+)?(?:(?:ja\s+)?pode|poderia|vai|va|volta|volte)\s+(?:ir\s+)?(?:descansar|dormir|repousar|se retirar|embora){endings}",
        rf"(?:descansa|descanse|durma|dorme|repouse|se retire){endings}",
        rf"(?:pode\s+ir|vai\s+embora|va\s+embora|pode\s+sair){endings}",
        r"(?:tchau|ate logo|ate mais|ate a proxima|boa noite)",
        r"(?:pode parar|para|pare|encerra|encerre)\s+(?:a conversa\s+)?(?:por agora|por enquanto|por hoje)",
        r"(?:nao preciso mais de voce|nao preciso de voce agora|ja chega por hoje)",
    )
    if any(re.fullmatch(pattern, command) for pattern in patterns):
        return True
    if not cfg or len(command.split()) > 16:
        return False
    if re.match(r"^(?:como|quando|porque|por que|o que|me explique|explique|qual)", command):
        return False
    if not re.search(r"\b(?:descans|dorm|repous|embora|soneca|retir|tchau|parar|encerra|deslig|sair|sozinh|paz|pausa|silencio|encerrar)\w*\b", command):
        return False
    prompt = ("Classifique se a frase é um pedido ao assistente para encerrar a conversa e voltar a esperar a palavra MARK. "
              "Responda apenas JSON com a chave booleana sleep. Perguntas sobre sono ou sobre o funcionamento não são pedidos. "
              "Frase: " + text)
    try:
        result = _ollama(prompt, cfg, "Classificador de intenção. Apenas JSON.",
                         num_predict=96, output_format="json")
        return json.loads(result).get("sleep") is True
    except Exception as exc:
        log.debug("Não foi possível classificar despedida (%s).", type(exc).__name__)
        return False

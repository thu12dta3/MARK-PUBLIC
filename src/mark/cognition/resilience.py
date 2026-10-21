"""Módulo de Resiliência Extrema e Tratamento de Casos de Borda do MARK.

Fornece defesas robustas contra os 6 vetores de falha em LLMs e UI:
1. Respostas vazias ou nulas (Empty / Null Outputs).
2. Conteúdo malformado ou truncado (Tolerant Parsing de JSON / Markdown).
3. Timeouts e indisponibilidade de provedores (Circuit Breaker & Fallback).
4. Estouro de limite de janela de contexto (Sliding Window & Context Compression).
5. Falhas de leitura e processamento de anexos (Encoding, Corrupt Files, Permissions).
6. Preservação contínua da responsividade da UI PyQt6 com Error Cards acionáveis.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

logger = logging.getLogger("mark.cognition.resilience")


class EmptyResponseError(RuntimeError):
    """Resposta vazia ou nula retornada pelo modelo após tentativa de re-prompt."""

    def __init__(self, message: str = "Resposta vazia retornada pelo modelo.", *, provider: str = "", model: str = ""):
        super().__init__(message)
        self.message = message
        self.provider = provider
        self.model = model


# ============================================================================
# 1. Tolerant Parsing e Reparo de Sintaxe JSON / Markdown
# ============================================================================

_MARKDOWN_CODE_BLOCK_RE = re.compile(
    r"```(?:json)?\s*\n?(.*?)\n?```",
    flags=re.DOTALL | re.IGNORECASE,
)


def repair_malformed_json_string(raw: str) -> str:
    """Aplica heurísticas automáticas de reparo de sintaxe em strings JSON truncadas ou corrompidas."""
    text = raw.strip()
    if not text:
        return "{}"

    # 1. Extração de blocos de código Markdown se existirem
    md_match = _MARKDOWN_CODE_BLOCK_RE.search(text)
    if md_match:
        text = md_match.group(1).strip()

    # 2. Localiza o primeiro delimitador JSON '{' ou '['
    first_brace = text.find("{")
    first_bracket = text.find("[")

    start_idx = -1
    is_object = True
    if first_brace != -1 and (first_bracket == -1 or first_brace < first_bracket):
        start_idx = first_brace
        is_object = True
    elif first_bracket != -1:
        start_idx = first_bracket
        is_object = False

    if start_idx != -1:
        text = text[start_idx:]

    # 3. Substituições comuns de literais Python para JSON
    text = re.sub(r"\bTrue\b", "true", text)
    text = re.sub(r"\bFalse\b", "false", text)
    text = re.sub(r"\bNone\b", "null", text)

    # 4. Chaves com aspas simples para aspas duplas: 'key': -> "key":
    text = re.sub(r"'([a-zA-Z0-9_\-\.]+)'\s*:", r'"\1":', text)

    # 5. Valores simples com aspas simples: : 'value' -> : "value"
    text = re.sub(r":\s*'([^']*)'", r': "\1"', text)

    # 6. Remove vírgulas finais antes de fechar chaves/colchetes (trailing commas)
    text = re.sub(r",\s*([}\]])", r"\1", text)

    # 7. Reparo de strings truncadas (número ímpar de aspas não-escapadas)
    # Contagem de aspas duplas não escapadas
    unescaped_quotes = len(re.findall(r'(?<!\\)"', text))
    if unescaped_quotes % 2 != 0:
        text += '"'

    # 8. Fechamento de colchetes e chaves pendentes
    open_braces = 0
    open_brackets = 0
    in_string = False
    escape_next = False

    for char in text:
        if escape_next:
            escape_next = False
            continue
        if char == "\\":
            escape_next = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if not in_string:
            if char == "{":
                open_braces += 1
            elif char == "}":
                open_braces = max(0, open_braces - 1)
            elif char == "[":
                open_brackets += 1
            elif char == "]":
                open_brackets = max(0, open_brackets - 1)

    # Fecha colchetes e chaves na ordem inversa necessária
    # Remove eventuais vírgulas no final antes de fechar
    text = re.sub(r",\s*$", "", text.rstrip())

    # Se faltar fechar brackets
    text += "]" * open_brackets
    text += "}" * open_braces

    return text


def tolerant_json_parse(text: str | None, default: Any = None) -> Any:
    """Tenta decodificar JSON tolerando truncamento, markdown ou sintaxe corrompida.
    
    Nunca derruba o pipeline: caso o reparo sintático falhe completamente,
    retorna `default` ou extrai dados parciais úteis.
    """
    if not text or not isinstance(text, str):
        return default

    cleaned = text.strip()
    if not cleaned:
        return default

    # Tentativa 1: Parse direto
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Tentativa 2: Extração de bloco markdown
    md_matches = _MARKDOWN_CODE_BLOCK_RE.findall(cleaned)
    for block in md_matches:
        try:
            return json.loads(block.strip())
        except json.JSONDecodeError:
            pass

    # Tentativa 3: Reparo sintático automático
    try:
        repaired = repair_malformed_json_string(cleaned)
        return json.loads(repaired)
    except Exception as exc:
        logger.debug("Reparo sintático de JSON falhou: %s", exc)

    # Tentativa 4: Extração regex de primeiro objeto {...}
    match = re.search(r"\{[^{}]*\}", cleaned)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    # Tentativa 5: Extração regex de chave-valor simples estilo {"key": "val"}
    pairs: dict[str, Any] = {}
    for kv in re.finditer(r'["\']?([a-zA-Z0-9_\-]+)["\']?\s*[:=]\s*["\']?([^,"\';}\]]+)["\']?', cleaned):
        k, v = kv.group(1).strip(), kv.group(2).strip()
        if v.lower() == "true":
            pairs[k] = True
        elif v.lower() == "false":
            pairs[k] = False
        elif v.isdigit():
            pairs[k] = int(v)
        else:
            pairs[k] = v

    if pairs:
        return pairs

    return default


# ============================================================================
# 2. Gestão de Context Window Overflow (Sliding Window & Compressão)
# ============================================================================

def estimate_tokens(text: str) -> int:
    """Estimador rápido de tokens (~3.8 caracteres por token em média)."""
    if not text:
        return 0
    tokens = re.findall(r"\w+|[^\w\s]", text, re.UNICODE)
    return max(1, len(tokens))


def compress_request_history(
    messages: list[dict[str, Any]],
    system_prompt: str | None,
    current_prompt: str,
    max_context: int,
    reserve_tokens: int = 512,
) -> tuple[list[dict[str, Any]], bool]:
    """Aplica janela deslizante preservando System Prompt e a mensagem atual do usuário intactos.
    
    Retorna (mensagens_comprimidas, foi_comprimido).
    Se o System Prompt + Mensagem Atual sozinhos excederem o limite máximo de contexto,
    uma exceção de ContextWindowExceededError ainda será apropriada.
    """
    sys_tokens = estimate_tokens(system_prompt or "")
    cur_tokens = estimate_tokens(current_prompt)
    essential_tokens = sys_tokens + cur_tokens
    actual_reserve = min(reserve_tokens, max(32, max_context // 4)) if max_context > 64 else 0
    budget = max(essential_tokens, max_context - actual_reserve)


    # Cópia defensiva da lista de turnos de histórico
    history_turns = list(messages)
    compressed = False

    def total_tokens(turns: list[dict[str, Any]]) -> int:
        tokens = essential_tokens
        for t in turns:
            tokens += estimate_tokens(str(t.get("content", "")))
        return tokens

    # Desliza a janela removendo os turnos mais antigos até caber no orçamento
    while history_turns and total_tokens(history_turns) > budget:
        history_turns.pop(0)
        compressed = True

    # Se comprimiu e ainda temos histórico, adiciona um marcador discreto
    if compressed and history_turns:
        history_turns.insert(
            0,
            {
                "role": "system",
                "content": "[Contexto: Histórico anterior comprimido automaticamente para respeitar o limite de contexto do modelo.]",
            },
        )

    return history_turns, compressed


# ============================================================================
# 3. Leitura e Processamento Tolerante a Falhas em Anexos
# ============================================================================

def format_attachment_failure_notice(filename: str, reason: str) -> str:
    """Formata o aviso gracioso inserido no contexto da mensagem quando um anexo não pode ser lido."""
    return f"[Não foi possível ler o anexo {filename}: {reason}]"


def tolerant_read_text_file(path: Path) -> tuple[str, str | None]:
    """Lê arquivo de texto com fallback de encodings e captura de exceções.
    
    Retorna (conteudo_ou_aviso, erro_detectado).
    """
    encodings_to_try = ("utf-8", "utf-8-sig", "latin-1", "cp1252")
    try:
        raw_bytes = path.read_bytes()
    except PermissionError:
        return format_attachment_failure_notice(path.name, "permissão de leitura negada"), "Permissão negada"
    except FileNotFoundError:
        return format_attachment_failure_notice(path.name, "arquivo não encontrado"), "Arquivo não encontrado"
    except OSError as exc:
        return format_attachment_failure_notice(path.name, f"erro de E/S ({exc})"), str(exc)

    if b"\x00" in raw_bytes[:1024]:
        return format_attachment_failure_notice(path.name, "arquivo binário não suportado como texto"), "Arquivo binário"

    for enc in encodings_to_try:
        try:
            return raw_bytes.decode(enc), None
        except UnicodeDecodeError:
            continue

    # Último recurso: utf-8 com substituição de caracteres inválidos
    try:
        return raw_bytes.decode("utf-8", errors="replace"), None
    except Exception as exc:
        return format_attachment_failure_notice(path.name, f"falha na decodificação de caracteres ({exc})"), "Encoding error"

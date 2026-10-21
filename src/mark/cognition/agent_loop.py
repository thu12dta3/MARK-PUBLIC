"""Motor de Execução Agentizada (ReAct & Tool Calling Loop) do MARK.

Implementa o ciclo Ação-Observação unificado com:
1. Suporte nativo a Tool Calling (Qwen, Llama3, Ollama e provedores de nuvem).
2. Validação estrita de permissões por modo (Geral, Engenheiro, Professor).
3. Prevenção de loops infinitos (trava rígida max_steps=3).
4. Suporte a cancelamento gracioso via CancelSignal.
5. Human-in-the-Loop: consentimento explícito para ferramentas mutáveis/destrutivas.
6. Notificação de estados para observabilidade do God's Eye View:
   - "Ferramenta Solicitada" (Aguardando Ferramenta)
   - "Ação Executada" (Lendo Repositório, Pesquisando na Web, etc.)
   - "Sucesso Confirmado" (Analisando Resultado)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import json
import logging
import re
import threading
import time
from typing import Any, Callable, Mapping, Sequence

from .resilience import compress_request_history, tolerant_json_parse
from .tools import ActionRequest, CapabilityToken, ToolContract, ToolRegistry, default_registry

logger = logging.getLogger("mark.cognition.agent_loop")


class AgentState(str, Enum):
    """Estados observáveis do ciclo de vida agentizado."""
    IDLE = "IDLE"
    THINKING = "Pensando"
    TOOL_REQUESTED = "Ferramenta Solicitada"
    TOOL_EXECUTING = "Ação Executada"
    SUCCESS_CONFIRMED = "Sucesso Confirmado"
    COMPLETED = "Concluído"
    CANCELLED = "Cancelado"
    FAILED = "Falha"


class CancelSignal:
    """Sinalizador de cancelamento gracioso e seguro entre threads."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def reset(self) -> None:
        self._event.clear()


@dataclass(frozen=True)
class ToolExecutionRecord:
    tool_name: str
    arguments: dict[str, Any]
    observation: Any
    duration_ms: float
    success: bool
    requires_consent: bool = False
    consent_granted: bool = True


@dataclass(frozen=True)
class AgentResult:
    output: str
    success: bool
    steps: int
    tool_calls: tuple[ToolExecutionRecord, ...] = ()
    cancelled: bool = False
    error: str | None = None


# Mapeamento de ações amigáveis para o God's Eye View
ACTION_DESCRIPTIONS: dict[str, str] = {
    "github.read_file": "Lendo Repositório",
    "github.list_files": "Consultando Estrutura do Repositório",
    "github.get_metadata": "Verificando Metadados do Repositório",
    "github.update_file": "Comitando Alterações no Repositório",
    "web.search": "Pesquisando na Web",
    "calculator.eval": "Calculando Expressão",
    "system.clock": "Consultando Relógio do Sistema",
    "memory.recall": "Consultando Memória Compartilhada",
    "knowledge.search": "Pesquisando na Biblioteca Local",
    "knowledge.stats": "Verificando Estatísticas da Biblioteca",
    "sandbox.sandbox_run_pytest": "Executando Testes no Sandbox",
    "sandbox.sandbox_run_rust_build": "Compilando no Sandbox",
    "sandbox.sandbox_run_command": "Executando Comando no Sandbox",
    "sandbox.sandbox_run_python": "Executando Script no Sandbox",
}


def get_action_label(tool_name: str, arguments: Mapping[str, Any] | None = None) -> str:
    """Retorna o rótulo descritivo da ação para o God's Eye View."""
    base = ACTION_DESCRIPTIONS.get(tool_name)
    if base:
        if tool_name == "github.read_file" and arguments and "path" in arguments:
            return f"Lendo Repositório: {arguments['path']}"
        if tool_name == "web.search" and arguments and "query" in arguments:
            return f"Pesquisando na Web: {arguments['query'][:32]}"
        return base
    if tool_name.startswith("gods_eye."):
        return f"Consultando God's Eye View ({tool_name.split('.', 1)[-1]})"
    if tool_name.startswith("sandbox."):
        return f"Executando no Sandbox Seguro ({tool_name.split('.', 1)[-1]})"
    return f"Executando {tool_name}"


def extract_tool_call(content: str, raw_response: dict[str, Any] | None = None) -> tuple[str, dict[str, Any]] | None:
    """Extrai chamadas de ferramenta de forma tolerante a múltiplos formatos (Qwen/Llama3/Ollama/OpenAI)."""
    # 1. Verificação nativa em tool_calls (formato de API estruturada OpenAI/Ollama) ou Anthropic tool_use
    if raw_response:
        msg = raw_response.get("message", raw_response)
        tool_calls = msg.get("tool_calls") or raw_response.get("tool_calls")
        if tool_calls and isinstance(tool_calls, (list, tuple)) and len(tool_calls) > 0:
            first_call = tool_calls[0]
            func = first_call.get("function", first_call)
            name = func.get("name", "")
            args_raw = func.get("arguments", {})
            if isinstance(args_raw, str):
                args = tolerant_json_parse(args_raw, {})
            elif isinstance(args_raw, dict):
                args = args_raw
            else:
                args = {}
            if name:
                return name.strip(), args

        # Formato Anthropic tool_use
        anth_content = raw_response.get("content") or msg.get("content")
        if isinstance(anth_content, list):
            for block in anth_content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    name = block.get("name", "")
                    args = block.get("input", {})
                    if name:
                        return name.strip(), args if isinstance(args, dict) else {}

    if not content or not isinstance(content, str):
        return None

    # 2. Formato XML <tool_call>...</tool_call>
    xml_match = re.search(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", content, re.DOTALL | re.IGNORECASE)
    if xml_match:
        parsed = tolerant_json_parse(xml_match.group(1), None)
        if isinstance(parsed, dict):
            name = parsed.get("name") or parsed.get("tool") or parsed.get("function")
            args = parsed.get("arguments") or parsed.get("parameters") or parsed.get("args") or {}
            if name and isinstance(name, str):
                return name.strip(), args if isinstance(args, dict) else {}

    # 3. Formato Markdown ```tool_call ... ```
    md_tool_match = re.search(r"```(?:tool_call|tool|action)\s*(\{.*?\})\s*```", content, re.DOTALL | re.IGNORECASE)
    if md_tool_match:
        parsed = tolerant_json_parse(md_tool_match.group(1), None)
        if isinstance(parsed, dict):
            name = parsed.get("name") or parsed.get("tool") or parsed.get("action")
            args = parsed.get("arguments") or parsed.get("parameters") or parsed.get("args") or {}
            if name and isinstance(name, str):
                return name.strip(), args if isinstance(args, dict) else {}

    # 4. Formato Markdown ```json ... ``` com chave "tool" ou "name"
    md_json_match = re.search(r"```json\s*(\{.*?\})\s*```", content, re.DOTALL | re.IGNORECASE)
    if md_json_match:
        parsed = tolerant_json_parse(md_json_match.group(1), None)
        if isinstance(parsed, dict):
            name = parsed.get("tool") or parsed.get("name")
            args = parsed.get("arguments") or parsed.get("parameters") or parsed.get("args")
            if name and isinstance(name, str) and isinstance(args, dict):
                return name.strip(), args

    # 5. Formato ReAct textual: Action: <tool> \n Action Input: <json>
    react_match = re.search(r"(?i)Action:\s*([a-zA-Z0-9_.-]+)\s*(?:\n|\r\n)+Action Input:\s*(\{.*?\})", content, re.DOTALL)
    if react_match:
        name = react_match.group(1).strip()
        args = tolerant_json_parse(react_match.group(2).strip(), {})
        return name, args if isinstance(args, dict) else {}

    return None


def is_tool_call_mutating(contract: ToolContract | None, tool_name: str) -> bool:
    """Verifica se a ferramenta acarreta alteração externa de dados ou exige consentimento prévio."""
    if contract is not None:
        if contract.risk_level in ("MUTATING", "DESTRUCTIVE", "WRITE"):
            return True
        if contract.side_effects:
            return True
    mutating_names = {
        "github.update_file",
        "sandbox.sandbox_run_command",
        "sandbox.sandbox_run_python",
        "engineer_solve_task",
    }
    return tool_name in mutating_names


def format_observation_for_prompt(result: Any) -> str:
    """Formata a observação do resultado de uma ferramenta para injeção no contexto do modelo."""
    if isinstance(result, str):
        return result
    if isinstance(result, (dict, list)):
        return json.dumps(result, ensure_ascii=False, indent=2)
    return str(result)


def build_system_prompt_for_mode(base_system: str, mode: str, registry: ToolRegistry) -> str:
    """Constrói o prompt do sistema injetando esquemas de ferramentas disponíveis para o modo ativo."""
    tools = registry.list_for_mode(mode)
    tool_descriptions: list[str] = []
    for t in tools:
        params_str = json.dumps(t.input_schema.get("properties", {}), ensure_ascii=False)
        tool_descriptions.append(f"- `{t.name}`: {t.description}. Parâmetros: {params_str}")

    tools_block = "\n".join(tool_descriptions) if tool_descriptions else "Nenhuma ferramenta externa liberada para este modo."

    instructions = (
        f"\n\n--- INSTRUÇÕES DE EXECUÇÃO AGENTIZADA (MODO: {mode.upper()}) ---\n"
        "Você possui acesso a ferramentas locais para investigar dados, arquivos e cálculos antes de responder.\n"
        "FERRAMENTAS DISPONÍVEIS:\n"
        f"{tools_block}\n\n"
        "REGRAS DE CHAMADA:\n"
        "1. Para chamar uma ferramenta, responda EXCLUSIVAMENTE com o bloco:\n"
        "<tool_call>\n"
        '{"name": "<nome_da_ferramenta>", "arguments": {<argumentos_em_json>}}\n'
        "</tool_call>\n"
        "2. Não acrescente comentários ou texto antes ou depois da chamada quando invocar uma ferramenta.\n"
        "3. Quando receber uma <observation>, analise os dados fornecidos. Se forem suficientes para cumprir o objetivo, responda diretamente ao usuário.\n"
        "4. Caso precise de mais informações, chame a próxima ferramenta.\n"
        "------------------------------------------------------------------"
    )
    return base_system + instructions


class AgenticExecutor:
    """Executor central do Ciclo de Ação e Observação do MARK (Agentic Workflow)."""

    def __init__(
        self,
        registry: ToolRegistry | None = None,
        max_steps: int = 3,
        knowledge_path: str | None = None,
    ) -> None:
        self.registry = registry if registry is not None else default_registry(knowledge_path=knowledge_path)
        self.max_steps = max(1, int(max_steps))

    def run(
        self,
        task: str,
        *,
        mode: str = "general",
        model_caller: Callable[[list[dict[str, Any]]], str],
        cancel_signal: CancelSignal | None = None,
        consent_callback: Callable[[str, dict[str, Any]], bool] | None = None,
        state_callback: Callable[[AgentState, str, str, str], None] | None = None,
        initial_history: list[dict[str, Any]] | None = None,
        system_prompt: str = "Você é o assistente inteligente MARK com execução agentizada.",
        token_budget: int = 8192,
    ) -> AgentResult:
        """Executa o loop unificado ReAct (Recebe intenção -> Executa ferramenta -> Injeta observação -> Gera resposta)."""
        signal = cancel_signal or CancelSignal()
        records: list[ToolExecutionRecord] = []
        step_count = 0

        def emit_state(state: AgentState, action_label: str, tool_name: str = "", details: str = ""):
            if state_callback is not None:
                try:
                    state_callback(state, action_label, tool_name, details)
                except Exception as exc:
                    logger.warning("Falha no state_callback do God's Eye View: %s", exc)

        clean_mode = str(mode).strip().lower()
        if clean_mode not in ("general", "professor", "engineer"):
            clean_mode = "general"

        system_instruction = build_system_prompt_for_mode(system_prompt, clean_mode, self.registry)

        messages: list[dict[str, Any]] = [{"role": "system", "content": system_instruction}]
        if initial_history:
            for item in initial_history:
                if isinstance(item, dict) and "role" in item and "content" in item:
                    messages.append({"role": item["role"], "content": str(item["content"])})

        # Adiciona a tarefa inicial do usuário
        task_objective = task.strip()
        messages.append({"role": "user", "content": task_objective})

        def _compress_messages(msgs: list[dict[str, Any]]) -> list[dict[str, Any]]:
            if len(msgs) <= 2:
                return list(msgs)
            sys_msg = msgs[0]["content"] if msgs[0].get("role") == "system" else ""
            cur_msg = str(msgs[-1].get("content", ""))
            intermediate = msgs[1:-1]
            compressed_intermediate, _ = compress_request_history(
                intermediate,
                system_prompt=sys_msg,
                current_prompt=cur_msg,
                max_context=token_budget,
            )
            return [msgs[0]] + compressed_intermediate + [msgs[-1]]

        while True:
            # 1. Verificação prévia de Cancelamento
            if signal.is_cancelled():
                emit_state(AgentState.CANCELLED, "Cancelado", "", "Execução interrompida a pedido do usuário.")
                return AgentResult(
                    output="Tarefa cancelada pelo usuário.",
                    success=False,
                    steps=step_count,
                    tool_calls=tuple(records),
                    cancelled=True,
                )

            # 2. Verificação de Limite de Passos (Prevenção de loops infinitos)
            if step_count >= self.max_steps:
                emit_state(AgentState.THINKING, "Sintetizando Resultado Final", "", "Limite de passos atingido.")
                messages.append({
                    "role": "user",
                    "content": (
                        f"[Limite de passos atingido ({self.max_steps}/{self.max_steps})]. "
                        f"Por favor, sintetize todas as observações obtidas até agora e forneça a resposta final para o usuário: {task_objective}"
                    ),
                })
                # Aplica janela deslizante antes da chamada final
                compressed = _compress_messages(messages)
                try:
                    final_reply = model_caller(compressed)
                except Exception as exc:
                    final_reply = f"Não foi possível concluir a síntese final após {step_count} passos: {exc}"
                emit_state(AgentState.COMPLETED, "Concluído", "", "Resposta final sintetizada.")
                return AgentResult(
                    output=final_reply,
                    success=True,
                    steps=step_count,
                    tool_calls=tuple(records),
                    cancelled=False,
                )

            # 3. Chamada de Inferência do Modelo
            emit_state(AgentState.THINKING, "Analisando Solicitação", "", f"Passo {step_count + 1} de {self.max_steps}")
            compressed_messages = _compress_messages(messages)

            try:
                model_output = model_caller(compressed_messages)
            except Exception as exc:
                emit_state(AgentState.FAILED, "Erro de Inferência", "", str(exc))
                return AgentResult(
                    output=f"Falha na inferência do modelo no passo {step_count + 1}: {exc}",
                    success=False,
                    steps=step_count,
                    tool_calls=tuple(records),
                    error=str(exc),
                )

            if signal.is_cancelled():
                emit_state(AgentState.CANCELLED, "Cancelado", "", "Execução interrompida após inferência.")
                return AgentResult(
                    output="Tarefa cancelada pelo usuário.",
                    success=False,
                    steps=step_count,
                    tool_calls=tuple(records),
                    cancelled=True,
                )

            # 4. Análise de Chamada de Ferramenta
            tool_call = extract_tool_call(model_output)

            # Se não houve chamada de ferramenta, o modelo respondeu com a conclusão final
            if tool_call is None:
                emit_state(AgentState.COMPLETED, "Concluído", "", "Resposta final recebida.")
                return AgentResult(
                    output=model_output,
                    success=True,
                    steps=step_count,
                    tool_calls=tuple(records),
                    cancelled=False,
                )

            # 5. Processamento da Ferramenta Solicitada
            tool_name, tool_args = tool_call
            step_count += 1
            t_start = time.perf_counter()

            # Transição God's Eye: "Ferramenta Solicitada" -> "Aguardando Ferramenta"
            emit_state(
                AgentState.TOOL_REQUESTED,
                "Aguardando Ferramenta",
                tool_name,
                f"Passo {step_count}/{self.max_steps} · Argumentos: {tool_args}",
            )

            # Validação de permissões baseada no modo ativo
            contract_entry = self.registry.get_tool(tool_name)
            if contract_entry is None:
                obs_text = f"Erro: Ferramenta '{tool_name}' não está registrada no sistema."
                records.append(ToolExecutionRecord(
                    tool_name=tool_name,
                    arguments=tool_args,
                    observation=obs_text,
                    duration_ms=(time.perf_counter() - t_start) * 1000,
                    success=False,
                ))
                messages.append({"role": "assistant", "content": model_output})
                messages.append({"role": "user", "content": f"<observation>\n{obs_text}\n</observation>"})
                continue

            contract, _ = contract_entry
            if clean_mode not in contract.modes:
                obs_text = (
                    f"Acesso negado: a ferramenta '{tool_name}' não está autorizada no modo '{clean_mode.upper()}'. "
                    f"Permitida apenas nos modos: {', '.join(contract.modes)}."
                )
                records.append(ToolExecutionRecord(
                    tool_name=tool_name,
                    arguments=tool_args,
                    observation=obs_text,
                    duration_ms=(time.perf_counter() - t_start) * 1000,
                    success=False,
                ))
                messages.append({"role": "assistant", "content": model_output})
                messages.append({"role": "user", "content": f"<observation>\n{obs_text}\n</observation>"})
                continue

            # 6. Human-in-the-Loop (Consentimento para operações mutáveis ou destrutivas)
            is_mutating = is_tool_call_mutating(contract, tool_name)
            consent_granted = True
            if is_mutating:
                if consent_callback is not None:
                    consent_granted = bool(consent_callback(tool_name, tool_args))
                else:
                    consent_granted = False

                if not consent_granted:
                    obs_text = (
                        f"[Operação cancelada pelo usuário]: O consentimento para executar a ferramenta "
                        f"sensível/mutável '{tool_name}' foi negado pelo operador."
                    )
                    records.append(ToolExecutionRecord(
                        tool_name=tool_name,
                        arguments=tool_args,
                        observation=obs_text,
                        duration_ms=(time.perf_counter() - t_start) * 1000,
                        success=False,
                        requires_consent=True,
                        consent_granted=False,
                    ))
                    messages.append({"role": "assistant", "content": model_output})
                    messages.append({"role": "user", "content": f"<observation>\n{obs_text}\n</observation>"})
                    continue

            # 7. Execução da Ferramenta
            action_desc = get_action_label(tool_name, tool_args)
            # Transição God's Eye: "Ação Executada" (Lendo Repositório, etc.)
            emit_state(
                AgentState.TOOL_EXECUTING,
                action_desc,
                tool_name,
                f"Executando {tool_name}...",
            )

            tool_success = True
            try:
                # Concede privilégio de execução se for mutável e o usuário consentiu
                raw_result = self.registry.invoke(
                    ActionRequest(tool_name, tool_args),
                    grants=frozenset(contract.permissions),
                    allow_privileged=is_mutating and consent_granted,
                )
                obs_text = format_observation_for_prompt(raw_result)
            except Exception as exc:
                tool_success = False
                obs_text = f"Erro na execução de '{tool_name}': {exc}"

            duration_ms = (time.perf_counter() - t_start) * 1000
            records.append(ToolExecutionRecord(
                tool_name=tool_name,
                arguments=tool_args,
                observation=obs_text,
                duration_ms=duration_ms,
                success=tool_success,
                requires_consent=is_mutating,
                consent_granted=consent_granted,
            ))

            # Transição God's Eye: "Sucesso Confirmado" (Analisando Resultado)
            emit_state(
                AgentState.SUCCESS_CONFIRMED,
                "Analisando Resultado",
                tool_name,
                f"Observação recebida ({len(obs_text)} caracteres)",
            )

            # 8. Injeção da Observação no Contexto do Modelo
            messages.append({"role": "assistant", "content": model_output})
            messages.append({
                "role": "user",
                "content": (
                    f"<observation>\n{obs_text}\n</observation>\n"
                    f"Considere a observação acima e continue para atender ao objetivo: {task_objective}. "
                    "Se você já possui todas as respostas para a solicitação, apresente a conclusão final diretamente ao usuário."
                ),
            })


def run_agentic_loop(
    prompt: str,
    *,
    mode: str = "general",
    registry: ToolRegistry | None = None,
    model_caller: Callable[[list[dict[str, Any]]], str],
    max_steps: int = 3,
    cancel_signal: CancelSignal | None = None,
    consent_callback: Callable[[str, dict[str, Any]], bool] | None = None,
    state_callback: Callable[[AgentState, str, str, str], None] | None = None,
    initial_history: list[dict[str, Any]] | None = None,
    system_prompt: str = "Você é o assistente inteligente MARK com execução agentizada.",
) -> AgentResult:
    """Função de conveniência para iniciar o ciclo de execução agentizada."""
    executor = AgenticExecutor(registry=registry, max_steps=max_steps)
    return executor.run(
        prompt,
        mode=mode,
        model_caller=model_caller,
        cancel_signal=cancel_signal,
        consent_callback=consent_callback,
        state_callback=state_callback,
        initial_history=initial_history,
        system_prompt=system_prompt,
    )

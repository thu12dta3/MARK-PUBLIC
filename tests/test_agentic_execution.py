"""Suíte de Testes de Integração de Execução Agentizada (Fase 2 - Workflow & Tool Calling).

Valida os 4 resultados obrigatórios:
1. Ciclo de Ação-Observação (ReAct/Tool Calling): fluxo unificado intencao -> execucao -> observacao -> sintese.
2. Registro Extensivel de Ferramentas: ToolRegistry centralizado mapeado por modos (Geral, Engenheiro, Professor).
3. Seguranca e Controle (Human-in-the-loop): Consentimento na UI para mutacoes, trava max_steps=3 e CancelSignal.
4. Integracao UI (God's Eye View): Painel com fases "Ferramenta Solicitada", "Ação Executada", "Sucesso Confirmado".
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from typing import Any
from unittest.mock import MagicMock, patch

from PyQt6.QtWidgets import QApplication

from mark.cognition.agent_loop import (
    AgenticExecutor,
    AgentResult,
    AgentState,
    CancelSignal,
    ToolExecutionRecord,
    extract_tool_call,
    is_tool_call_mutating,
    run_agentic_loop,
)
from mark.cognition.tools import (
    ActionRequest,
    ToolContract,
    ToolRegistry,
    default_registry,
)
from mark.ui.widgets import (
    ActionConsentDialog,
    ConsentBridge,
    GodsEyeStatusWidget,
)


class TestAgenticExecutionE2E(unittest.TestCase):
    """Teste de Integração de Ponta a Ponta (Golden Path).
    
    Cascata validada:
    1. Tarefa recebida: "Verifique o arquivo X no GitHub e resuma".
    2. Chamada de listagem/busca interceptada e confirmada.
    3. Leitura do arquivo simulada.
    4. Resumo final gerado combinando todas as observações.
    """

    def setUp(self) -> None:
        self.registry = default_registry(allow_mutating=True)

    @patch("mark.github.read_file")
    @patch("mark.github.list_root")
    def test_golden_path_github_workflow_and_summary(self, mock_list_root, mock_read_file) -> None:
        mock_list_root.return_value = ["README.md", "pyproject.toml", "src"]
        mock_read_file.return_value = (
            "# MARK Desktop\nAssistente Pessoal Soberano e Seguro com Inteligência Local.",
            "sha12345",
        )

        task = "Verifique o arquivo README.md no GitHub e resuma o projeto."
        state_history: list[tuple[AgentState, str, str, str]] = []

        def on_state(state: AgentState, action_label: str, tool_name: str, details: str) -> None:
            state_history.append((state, action_label, tool_name, details))

        model_turn = 0

        def mock_llm_turn_by_turn(messages: list[dict[str, Any]]) -> str:
            nonlocal model_turn
            model_turn += 1

            if model_turn == 1:
                # Passo 1: Modelo decide listar arquivos
                return '<tool_call>{"name": "github.list_files", "arguments": {"repo": "thu12dta3/MARK"}}</tool_call>'
            elif model_turn == 2:
                # Passo 2: Modelo observa a listagem e decide ler o README.md
                # Valida que a observação anterior está no contexto
                last_msg = messages[-1]["content"]
                self.assertIn("README.md", last_msg)
                return '<tool_call>{"name": "github.read_file", "arguments": {"repo": "thu12dta3/MARK", "path": "README.md"}}</tool_call>'
            elif model_turn == 3:
                # Passo 3: Modelo sintetiza as observações no resumo final
                last_msg = messages[-1]["content"]
                self.assertIn("Assistente Pessoal Soberano", last_msg)
                return "Resumo do MARK: Assistente pessoal seguro e soberano com inteligência local."
            return "Concluído."

        result = run_agentic_loop(
            task,
            mode="engineer",
            registry=self.registry,
            model_caller=mock_llm_turn_by_turn,
            max_steps=3,
            state_callback=on_state,
        )

        # Asserções do Golden Path
        self.assertTrue(result.success)
        self.assertFalse(result.cancelled)
        self.assertEqual(result.steps, 2)
        self.assertEqual(len(result.tool_calls), 2)
        self.assertEqual(result.tool_calls[0].tool_name, "github.list_files")
        self.assertEqual(result.tool_calls[1].tool_name, "github.read_file")
        self.assertIn("Resumo do MARK", result.output)

        mock_list_root.assert_called_once_with("thu12dta3/MARK", "main")
        mock_read_file.assert_called_once_with("thu12dta3/MARK", "main", "README.md")

        # Verificação da distinção exata no God's Eye View:
        # "Ferramenta Solicitada" -> "Ação Executada" -> "Sucesso Confirmado"
        states_only = [st[0] for st in state_history]
        self.assertIn(AgentState.TOOL_REQUESTED, states_only)
        self.assertIn(AgentState.TOOL_EXECUTING, states_only)
        self.assertIn(AgentState.SUCCESS_CONFIRMED, states_only)

        requested_indices = [i for i, st in enumerate(state_history) if st[0] == AgentState.TOOL_REQUESTED]
        executing_indices = [i for i, st in enumerate(state_history) if st[0] == AgentState.TOOL_EXECUTING]
        confirmed_indices = [i for i, st in enumerate(state_history) if st[0] == AgentState.SUCCESS_CONFIRMED]

        self.assertTrue(len(requested_indices) >= 2)
        self.assertTrue(requested_indices[0] < executing_indices[0] < confirmed_indices[0])


class TestModePermissions(unittest.TestCase):
    """Validação de permissões estritas por modo ativo da Sidebar."""

    def setUp(self) -> None:
        self.registry = default_registry()

    def test_engineer_mode_has_full_access(self) -> None:
        tools = self.registry.list_for_mode("engineer")
        tool_names = {t.name for t in tools}
        self.assertIn("github.read_file", tool_names)
        self.assertIn("github.list_files", tool_names)
        self.assertIn("github.update_file", tool_names)
        self.assertIn("calculator.eval", tool_names)
        self.assertIn("web.search", tool_names)

    def test_professor_mode_research_focus(self) -> None:
        tools = self.registry.list_for_mode("professor")
        tool_names = {t.name for t in tools}
        self.assertIn("web.search", tool_names)
        self.assertIn("memory.recall", tool_names)
        # Modo Professor não pode acessar ferramentas de código do GitHub
        self.assertNotIn("github.read_file", tool_names)
        self.assertNotIn("github.update_file", tool_names)

    def test_general_mode_basic_tools_only(self) -> None:
        tools = self.registry.list_for_mode("general")
        tool_names = {t.name for t in tools}
        self.assertIn("calculator.eval", tool_names)
        self.assertIn("system.clock", tool_names)
        self.assertIn("memory.recall", tool_names)
        # Modo Geral não acessa GitHub ou ferramentas complexas
        self.assertNotIn("github.read_file", tool_names)
        self.assertNotIn("github.update_file", tool_names)

    def test_permission_denial_injected_as_observation(self) -> None:
        """Se o modelo tentar chamar github.read_file no modo Geral, recebe observação de acesso negado."""
        turn = 0

        def caller_trying_forbidden_tool(messages: list[dict[str, Any]]) -> str:
            nonlocal turn
            turn += 1
            if turn == 1:
                return '<tool_call>{"name": "github.read_file", "arguments": {"path": "file.txt"}}</tool_call>'
            # Na segunda rodada, verifica se recebeu o erro e responde
            last_msg = messages[-1]["content"]
            assert "Acesso negado" in last_msg
            return "Não tenho permissão para acessar o GitHub no Modo Geral."

        result = run_agentic_loop(
            "Leia o arquivo no GitHub",
            mode="general",
            registry=self.registry,
            model_caller=caller_trying_forbidden_tool,
            max_steps=3,
        )

        self.assertTrue(result.success)
        self.assertEqual(len(result.tool_calls), 1)
        self.assertFalse(result.tool_calls[0].success)
        self.assertIn("Acesso negado", str(result.tool_calls[0].observation))
        self.assertIn("Não tenho permissão", result.output)


class TestLoopPreventionAndCancellation(unittest.TestCase):
    """Prevenção de loops infinitos (max_steps) e cancelamento seguro (CancelSignal)."""

    def setUp(self) -> None:
        self.registry = default_registry()

    def test_strict_max_steps_limit_prevents_infinite_loop(self) -> None:
        """Garante corte rígido em max_steps=3 quando o modelo insiste em chamar ferramentas."""
        calls = 0

        def endless_tool_caller(messages: list[dict[str, Any]]) -> str:
            nonlocal calls
            calls += 1
            # Verifica se o loop injetou o aviso de limite de passos
            last_msg = messages[-1]["content"]
            if "[Limite de passos atingido" in last_msg:
                return "Síntese forçada: cálculo atingiu o limite de passos."
            # Continua solicitando ferramentas
            return f'<tool_call>{{"name": "calculator.eval", "arguments": {{"expression": "{calls} + 1"}}}}</tool_call>'

        result = run_agentic_loop(
            "Calcule algo indefinidamente",
            mode="general",
            registry=self.registry,
            model_caller=endless_tool_caller,
            max_steps=3,
        )

        self.assertTrue(result.success)
        self.assertEqual(result.steps, 3)
        self.assertEqual(len(result.tool_calls), 3)
        self.assertIn("Síntese forçada", result.output)

    def test_cancel_signal_gracefully_interrupts_loop(self) -> None:
        signal = CancelSignal()
        turn = 0

        def cancellable_caller(messages: list[dict[str, Any]]) -> str:
            nonlocal turn
            turn += 1
            if turn == 1:
                # Simula cancelamento pelo usuário no primeiro passo
                signal.cancel()
                return '<tool_call>{"name": "calculator.eval", "arguments": {"expression": "2 * 2"}}</tool_call>'
            return "Nunca deve chegar aqui"

        result = run_agentic_loop(
            "Executar tarefa",
            mode="general",
            registry=self.registry,
            model_caller=cancellable_caller,
            cancel_signal=signal,
            max_steps=3,
        )

        self.assertFalse(result.success)
        self.assertTrue(result.cancelled)
        self.assertIn("cancelada pelo usuário", result.output)


class TestHumanInTheLoopConsent(unittest.TestCase):
    """Validação de Consentimento Explícito (ActionConsentDialog / Human-in-the-Loop)."""

    def setUp(self) -> None:
        self.registry = default_registry(allow_mutating=True)

    def test_is_tool_call_mutating_detection(self) -> None:
        contract_read, _ = self.registry.get_tool("github.read_file")
        contract_write, _ = self.registry.get_tool("github.update_file")

        self.assertFalse(is_tool_call_mutating(contract_read, "github.read_file"))
        self.assertTrue(is_tool_call_mutating(contract_write, "github.update_file"))

    @patch("mark.github.update_file")
    def test_mutating_tool_blocked_when_consent_rejected(self, mock_update) -> None:
        turn = 0

        def caller(messages: list[dict[str, Any]]) -> str:
            nonlocal turn
            turn += 1
            if turn == 1:
                return (
                    '<tool_call>{"name": "github.update_file", "arguments": {'
                    '"repo": "thu12dta3/MARK", "path": "data.txt", "message": "update", '
                    '"content": "hacked", "sha": "123"}}</tool_call>'
                )
            last_msg = messages[-1]["content"]
            assert "Operação cancelada pelo usuário" in last_msg
            return "Não foi possível atualizar o arquivo pois o usuário negou a autorização."

        # Simula rejeição no pop-up de consentimento
        def consent_rejected(tool_name: str, args: dict[str, Any]) -> bool:
            return False

        result = run_agentic_loop(
            "Altere o arquivo data.txt",
            mode="engineer",
            registry=self.registry,
            model_caller=caller,
            consent_callback=consent_rejected,
            max_steps=3,
        )

        self.assertTrue(result.success)
        self.assertEqual(len(result.tool_calls), 1)
        self.assertFalse(result.tool_calls[0].consent_granted)
        mock_update.assert_not_called()
        self.assertIn("negou a autorização", result.output)

    @patch("mark.github.update_file")
    def test_mutating_tool_executed_when_consent_granted(self, mock_update) -> None:
        mock_update.return_value = {"commit": "sha999"}
        turn = 0

        def caller(messages: list[dict[str, Any]]) -> str:
            nonlocal turn
            turn += 1
            if turn == 1:
                return (
                    '<tool_call>{"name": "github.update_file", "arguments": {'
                    '"repo": "thu12dta3/MARK", "path": "data.txt", "message": "edit", '
                    '"content": "authorized edit", "sha": "123"}}</tool_call>'
                )
            last_msg = messages[-1]["content"]
            assert "ok" in last_msg or "sha999" in last_msg
            return "Arquivo atualizado com sucesso."

        # Simula clique em "Autorizar" no pop-up de consentimento
        def consent_approved(tool_name: str, args: dict[str, Any]) -> bool:
            return True

        result = run_agentic_loop(
            "Altere o arquivo data.txt",
            mode="engineer",
            registry=self.registry,
            model_caller=caller,
            consent_callback=consent_approved,
            max_steps=3,
        )

        self.assertTrue(result.success)
        self.assertEqual(len(result.tool_calls), 1)
        self.assertTrue(result.tool_calls[0].consent_granted)
        mock_update.assert_called_once_with("thu12dta3/MARK", "main", "data.txt", "123", "edit", "authorized edit")
        self.assertIn("atualizado com sucesso", result.output)


class TestUIComponentsIntegration(unittest.TestCase):
    """Validação dos componentes visuais do God's Eye View e Human-in-the-Loop na UI PyQt6."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_gods_eye_status_widget_lifecycle(self) -> None:
        widget = GodsEyeStatusWidget()
        self.assertFalse(widget.isVisible())

        # 1. Transição: Ferramenta Solicitada -> "Aguardando Ferramenta"
        widget.update_state("Ferramenta Solicitada", "github.read_file", "Aguardando Ferramenta", "Passo 1/3")
        self.assertTrue(widget.isVisible())
        self.assertEqual(widget.phase_badge.text(), "Ferramenta Solicitada")
        self.assertEqual(widget.action_label.text(), "Aguardando Ferramenta")
        self.assertEqual(widget.step_label.text(), "Passo 1/3")

        # 2. Transição: Ação Executada -> "Lendo Repositório"
        widget.update_state("Ação Executada", "github.read_file", "Lendo Repositório: README.md", "Passo 1/3")
        self.assertEqual(widget.phase_badge.text(), "Ação Executada")
        self.assertEqual(widget.action_label.text(), "Lendo Repositório: README.md")

        # 3. Transição: Sucesso Confirmado -> "Analisando Resultado"
        widget.update_state("Sucesso Confirmado", "github.read_file", "Analisando Resultado", "Passo 1/3")
        self.assertEqual(widget.phase_badge.text(), "Sucesso Confirmado")
        self.assertEqual(widget.action_label.text(), "Analisando Resultado")

        # 4. Teste do Botão Cancelar
        cancelled = False

        def on_cancel():
            nonlocal cancelled
            cancelled = True

        widget.cancel_requested.connect(on_cancel)
        widget.cancel_button.click()
        self.assertTrue(cancelled)

        # 5. Reset oculta o widget
        widget.reset()
        self.assertFalse(widget.isVisible())

    def test_action_consent_dialog_authorization(self) -> None:
        dialog = ActionConsentDialog(
            tool_name="github.update_file",
            arguments={"path": "src/main.py", "content": "# update"},
        )
        self.assertFalse(dialog.granted)
        dialog._on_authorize()
        self.assertTrue(dialog.granted)

    def test_action_consent_dialog_rejection(self) -> None:
        dialog = ActionConsentDialog(
            tool_name="github.update_file",
            arguments={"path": "src/main.py"},
        )
        dialog._on_cancel()
        self.assertFalse(dialog.granted)


class TestToolCallParsers(unittest.TestCase):
    """Validação de tolerância a múltiplos formatos de chamada de ferramentas (Qwen, Llama3, Ollama, ReAct)."""

    def test_extract_xml_format(self) -> None:
        text = 'Vou verificar o arquivo:\n<tool_call>\n{"name": "github.read_file", "arguments": {"path": "README.md"}}\n</tool_call>'
        parsed = extract_tool_call(text)
        self.assertIsNotNone(parsed)
        name, args = parsed
        self.assertEqual(name, "github.read_file")
        self.assertEqual(args.get("path"), "README.md")

    def test_extract_markdown_tool_call_fence(self) -> None:
        text = '```tool_call\n{"name": "calculator.eval", "arguments": {"expression": "2 + 2"}}\n```'
        parsed = extract_tool_call(text)
        self.assertIsNotNone(parsed)
        name, args = parsed
        self.assertEqual(name, "calculator.eval")
        self.assertEqual(args.get("expression"), "2 + 2")

    def test_extract_json_markdown_fence(self) -> None:
        text = '```json\n{"tool": "web.search", "arguments": {"query": "MARK AI architecture"}}\n```'
        parsed = extract_tool_call(text)
        self.assertIsNotNone(parsed)
        name, args = parsed
        self.assertEqual(name, "web.search")
        self.assertEqual(args.get("query"), "MARK AI architecture")

    def test_extract_react_text_format(self) -> None:
        text = 'Thought: Preciso calcular a expressao\nAction: calculator.eval\nAction Input: {"expression": "10 * 5"}'
        parsed = extract_tool_call(text)
        self.assertIsNotNone(parsed)
        name, args = parsed
        self.assertEqual(name, "calculator.eval")
        self.assertEqual(args.get("expression"), "10 * 5")

    def test_extract_native_structured_tool_calls(self) -> None:
        raw = {
            "message": {
                "tool_calls": [
                    {
                        "function": {
                            "name": "system.clock",
                            "arguments": '{"timezone": "UTC"}',
                        }
                    }
                ]
            }
        }
        parsed = extract_tool_call("", raw_response=raw)
        self.assertIsNotNone(parsed)
        name, args = parsed
        self.assertEqual(name, "system.clock")
        self.assertEqual(args.get("timezone"), "UTC")


if __name__ == "__main__":
    unittest.main()

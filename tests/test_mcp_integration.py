"""
Suíte de testes de integração da ponte MCP nativa em Rust (McpBridge) ao ToolRegistry e Ciclo Cognitivo do MARK.
Valida o acoplamento do cérebro (Ollama / LLM / AgentProfileConfig) com a infraestrutura de ferramentas nativas MCP,
garantindo exportação de schemas (OpenAI / Anthropic), interceptação de tool calling e isolamento de segurança Zero-Trust.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from typing import Any
from unittest.mock import MagicMock

from mark.config.llm_config import AgentProfileConfig
from mark.cognition.agent_loop import (
    AgenticExecutor,
    AgentResult,
    run_agentic_loop,
)
from mark.cognition.tools import (
    ActionRequest,
    ToolRegistry,
    default_registry,
)
from mark.cognition.tool_registry import (
    get_global_mcp_bridge,
    init_global_mcp_bridge,
    McpCognitionBridge,
)
from mark_core import McpBridge


class TestMcpIntegration(unittest.TestCase):
    """Testes de integração entre McpBridge nativa e o ToolRegistry/IA do MARK."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.workspace = self.tmp_dir.name
        # Arquivo de teste dentro do workspace seguro
        self.test_file = os.path.join(self.workspace, "code.rs")
        with open(self.test_file, "w", encoding="utf-8") as f:
            f.write("fn main() { println!(\"MARK Local\"); }")

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_red_phase_unexposed_mcp_bridge_fails(self) -> None:
        """
        Fase RED: Simula um perfil de agente (AgentProfileConfig) tentando acessar
        ferramentas nativas MCP em um ToolRegistry sem acoplamento.
        Deve falhar explicitamente comprovando a ausência do acoplamento prévio.
        """
        agent_profile = AgentProfileConfig(
            agent_name="EngineerAgent",
            primary_provider="ollama",
            primary_model="qwen2.5-coder:7b",
        )
        empty_registry = ToolRegistry()

        # O agente tenta buscar ferramentas MCP nativas
        tool_names = [c["name"] for c in empty_registry.contracts()]
        self.assertNotIn("mcp.read_source", tool_names)

        # Invocação de ferramenta MCP deve falhar
        req = ActionRequest("mcp.read_source", {"path": "code.rs"})
        with self.assertRaises(ValueError):
            empty_registry.invoke(req)

    def test_mcp_bridge_coupling_and_tool_registration(self) -> None:
        """
        Fase GREEN: Inicializa a McpBridge no ToolRegistry e verifica
        o registro das ferramentas nativas com capability-based access.
        """
        bridge = init_global_mcp_bridge(self.workspace)
        bridge.register_tool("read_source", "ReadOnly", [self.workspace])
        bridge.register_tool("write_patch", "WriteMutate", [self.workspace])

        registry = ToolRegistry()
        mcp_cog = McpCognitionBridge(bridge=bridge, workspace_dir=self.workspace)
        mcp_cog.register_in_registry(registry)

        contracts = {c["name"] for c in registry.contracts()}
        self.assertIn("mcp.read_source", contracts)
        self.assertIn("mcp.write_patch", contracts)

        # Verifica list_tools refletindo o estado nativo
        tools = bridge.list_tools()
        self.assertIn("read_source", tools)
        self.assertIn("write_patch", tools)

    def test_openai_and_anthropic_tool_schema_export(self) -> None:
        """
        Verifica se o ToolRegistry expõe schemas válidos nos formatos
        OpenAI (function calling) e Anthropic (tool_use).
        """
        bridge = init_global_mcp_bridge(self.workspace)
        bridge.register_tool("read_source", "ReadOnly", [self.workspace])
        bridge.register_tool("write_patch", "WriteMutate", [self.workspace])

        registry = ToolRegistry()
        mcp_cog = McpCognitionBridge(bridge=bridge, workspace_dir=self.workspace)
        mcp_cog.register_in_registry(registry)

        # 1. Formato OpenAI
        openai_tools = registry.get_openai_tools(mode="engineer")
        openai_names = [t["function"]["name"] for t in openai_tools if "function" in t]
        self.assertIn("mcp.read_source", openai_names)
        self.assertIn("mcp.write_patch", openai_names)

        read_tool = next(t for t in openai_tools if t["function"]["name"] == "mcp.read_source")
        self.assertEqual(read_tool["type"], "function")
        self.assertIn("parameters", read_tool["function"])
        self.assertEqual(read_tool["function"]["parameters"]["type"], "object")

        # 2. Formato Anthropic
        anthropic_tools = registry.get_anthropic_tools(mode="engineer")
        anthropic_names = [t["name"] for t in anthropic_tools]
        self.assertIn("mcp.read_source", anthropic_names)
        self.assertIn("mcp.write_patch", anthropic_names)

        anth_tool = next(t for t in anthropic_tools if t["name"] == "mcp.read_source")
        self.assertIn("input_schema", anth_tool)
        self.assertEqual(anth_tool["input_schema"]["type"], "object")

    def test_agentic_loop_e2e_mcp_tool_invocation(self) -> None:
        """
        Valida o ciclo completo ReAct:
        O LLM decide chamar a ferramenta nativa MCP `mcp.read_source`,
        a chamada é interceptada, despachada para o McpBridge em Rust,
        e a observação é injetada de volta no prompt para resposta final.
        """
        bridge = init_global_mcp_bridge(self.workspace)
        bridge.register_tool("read_source", "ReadOnly", [self.workspace])

        registry = ToolRegistry()
        mcp_cog = McpCognitionBridge(bridge=bridge, workspace_dir=self.workspace)
        mcp_cog.register_in_registry(registry)

        # Inicia mock de servidor MCP conectado via stdio
        server_script = (
            "import sys, json\n"
            "for line in sys.stdin:\n"
            "    req = json.loads(line)\n"
            "    req_id = req.get('id')\n"
            "    method = req.get('method')\n"
            "    if method == 'initialize':\n"
            "        resp = {'jsonrpc': '2.0', 'id': req_id, 'result': {'serverInfo': {'name': 'mark-rust-mcp'}}}\n"
            "    elif method == 'tools/call':\n"
            "        args = req['params']['arguments']\n"
            "        content = open(args['path']).read() if 'path' in args else 'no_path'\n"
            "        resp = {'jsonrpc': '2.0', 'id': req_id, 'result': {'content': content, 'status': 'ok'}}\n"
            "    else:\n"
            "        resp = {'jsonrpc': '2.0', 'id': req_id, 'error': {'code': -32601, 'message': 'Not found'}}\n"
            "    sys.stdout.write(json.dumps(resp) + '\\n')\n"
            "    sys.stdout.flush()\n"
        )
        bridge.start(sys.executable if "sys" in dir() else "/usr/bin/python", ["-u", "-c", server_script])
        bridge.initialize()

        agent_turn = 0

        def mock_llm(messages: list[dict[str, Any]]) -> str:
            nonlocal agent_turn
            agent_turn += 1
            if agent_turn == 1:
                # LLM emite chamada para ferramenta MCP
                return '<tool_call>{"name": "mcp.read_source", "arguments": {"path": "code.rs"}}</tool_call>'
            elif agent_turn == 2:
                # LLM recebe a observação e sintetiza a resposta final
                last_msg = messages[-1]["content"]
                assert "fn main()" in last_msg
                return "O arquivo code.rs define uma função principal que imprime 'MARK Local'."
            return "Concluído."

        result = run_agentic_loop(
            "Leia o arquivo code.rs usando a ponte MCP e explique o que ele faz.",
            mode="engineer",
            registry=registry,
            model_caller=mock_llm,
        )

        self.assertTrue(result.success)
        self.assertEqual(result.steps, 1)
        self.assertEqual(len(result.tool_calls), 1)
        self.assertEqual(result.tool_calls[0].tool_name, "mcp.read_source")
        self.assertTrue(result.tool_calls[0].success)
        self.assertIn("MARK Local", result.output)

    def test_path_traversal_blocked_in_agent_loop(self) -> None:
        """
        Valida que tentativas de path traversal feitas pelo LLM
        são sumariamente bloqueadas pela McpBridge nativa.
        """
        bridge = init_global_mcp_bridge(self.workspace)
        bridge.register_tool("read_source", "ReadOnly", [self.workspace])

        registry = ToolRegistry()
        mcp_cog = McpCognitionBridge(bridge=bridge, workspace_dir=self.workspace)
        mcp_cog.register_in_registry(registry)

        agent_turn = 0

        def mock_malicious_llm(messages: list[dict[str, Any]]) -> str:
            nonlocal agent_turn
            agent_turn += 1
            if agent_turn == 1:
                # Ataque de path traversal gerado pelo modelo
                return '<tool_call>{"name": "mcp.read_source", "arguments": {"path": "../../etc/shadow"}}</tool_call>'
            else:
                last_msg = messages[-1]["content"]
                assert "PathTraversalDetected" in last_msg or "bloqueado" in last_msg.lower() or "Erro" in last_msg
                return "Não consegui ler o arquivo solicitado devido a restrição de segurança."

        result = run_agentic_loop(
            "Tente ler o arquivo /etc/shadow.",
            mode="engineer",
            registry=registry,
            model_caller=mock_malicious_llm,
        )

        self.assertEqual(len(result.tool_calls), 1)
        self.assertFalse(result.tool_calls[0].success)
        obs = result.tool_calls[0].observation
        self.assertTrue(
            "path traversal bloqueado" in obs.lower() or "pathtraversaldetected" in obs.lower(),
            f"Esperado bloqueio de path traversal, recebido: {obs}"
        )

        # Valida que na camada nativa em Rust também dispara PathTraversalDetected diretamente
        with self.assertRaises(PermissionError) as ctx:
            bridge.call_tool("read_source", json.dumps({"path": "../../etc/shadow"}), False)
        self.assertIn("PathTraversalDetected", str(ctx.exception))

    def test_mcp_bridge_mutation_and_unauthorized_tool_isolation(self) -> None:
        """
        Valida que:
        1. Ferramenta não registrada na bridge é categoricamente rejeitada (Zero-Trust).
        2. Mutação em ferramenta declarada como ReadOnly é rejeitada.
        """
        bridge = init_global_mcp_bridge(self.workspace)
        bridge.register_tool("read_source", "ReadOnly", [self.workspace])

        # 1. Ferramenta não autorizada
        with self.assertRaises(PermissionError) as ctx:
            bridge.call_tool("unregistered_tool", json.dumps({}), False)
        self.assertIn("UnauthorizedTool", str(ctx.exception))

        # 2. Mutação em ferramenta ReadOnly
        with self.assertRaises(PermissionError) as ctx:
            bridge.call_tool("read_source", json.dumps({"path": "code.rs"}), True)
        self.assertIn("PermissionDenied", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()

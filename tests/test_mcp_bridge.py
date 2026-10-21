"""
Suíte de testes de validação da ponte MCP nativa em Rust (McpBridge)
Verifica Isolamento Real de Capacidades (Zero-Trust), Bloqueio de Path Traversal,
Limites de Payload (Anti-OOM) e Proteção contra Mensagens Fora de Ordem.
"""

import json
import os
import sys
import tempfile
import pytest

from mark_core import McpBridge


def test_mcp_bridge_instantiation():
    with tempfile.TemporaryDirectory() as tmp_dir:
        bridge = McpBridge(tmp_dir)
        assert bridge is not None
        assert bridge.list_tools() == []


def test_mcp_bridge_tool_registration_and_listing():
    with tempfile.TemporaryDirectory() as tmp_dir:
        bridge = McpBridge(tmp_dir)
        bridge.register_tool("read_source", "ReadOnly", [tmp_dir])
        bridge.register_tool("write_patch", "WriteMutate", [tmp_dir])

        tools = bridge.list_tools()
        assert "read_source" in tools
        assert "write_patch" in tools


def test_mcp_bridge_invalid_capability_rejected():
    with tempfile.TemporaryDirectory() as tmp_dir:
        bridge = McpBridge(tmp_dir)
        with pytest.raises(ValueError, match="Capacidade inválida"):
            bridge.register_tool("hack_tool", "SuperAdminPrivilege", [tmp_dir])


def test_mcp_bridge_path_traversal_detection():
    with tempfile.TemporaryDirectory() as tmp_dir:
        bridge = McpBridge(tmp_dir)
        
        # Ataques de path traversal devem disparar PermissionError nativo
        malicious_paths = [
            "../../etc/shadow",
            "/etc/shadow",
            "folder/../../../../etc/passwd",
            "test/\x00/malicious",
            "....//....//etc/passwd",
        ]

        for bad in malicious_paths:
            with pytest.raises(PermissionError, match="PathTraversalDetected"):
                bridge.normalize_and_validate_path(bad)

        # Caminho válido dentro do workspace deve ser normalizado com sucesso
        valid = "subfolder/code.rs"
        normalized = bridge.normalize_and_validate_path(valid)
        assert normalized == os.path.join(tmp_dir, "subfolder/code.rs")


def test_mcp_bridge_memory_capability_isolation():
    with tempfile.TemporaryDirectory() as tmp_dir:
        bridge = McpBridge(tmp_dir)
        bridge.register_tool("inspect_ast", "ReadOnly", [tmp_dir])
        bridge.register_tool("save_ast", "WriteMutate", [tmp_dir])

        # 1. Ferramenta não registrada é categoricamente negada
        with pytest.raises(PermissionError, match="UnauthorizedTool"):
            bridge.call_tool("unregistered_cmd", json.dumps({}), False)

        # 2. Mutação em ferramenta ReadOnly é categoricamente negada
        with pytest.raises(PermissionError, match="PermissionDenied.*ReadOnly"):
            bridge.call_tool("inspect_ast", json.dumps({"path": "file.rs"}), True)

        # 3. Invocação de ferramenta de escrita em contexto ReadOnly é negada
        with pytest.raises(PermissionError, match="PermissionDenied.*somente leitura"):
            bridge.call_tool("save_ast", json.dumps({"path": "file.rs"}), False)


def test_mcp_bridge_oversized_payload_rejected():
    with tempfile.TemporaryDirectory() as tmp_dir:
        bridge = McpBridge(tmp_dir)
        
        # 5 MB excede limite estrito de 4 MB
        oversized = b"a" * (5 * 1024 * 1024)
        with pytest.raises(ValueError, match="PayloadTooLarge"):
            bridge.simulate_process_response(1, oversized)


def test_mcp_bridge_out_of_order_message_rejected():
    with tempfile.TemporaryDirectory() as tmp_dir:
        bridge = McpBridge(tmp_dir)
        
        # Resposta com ID fraudulento ou fora de ordem
        fraudulent_payload = json.dumps({
            "jsonrpc": "2.0",
            "id": 999,
            "result": {"status": "pwned"}
        }).encode("utf-8")

        # bridge espera ID 1
        with pytest.raises(RuntimeError, match="OutOfOrderMessage"):
            bridge.simulate_process_response(1, fraudulent_payload)

        # Resposta com ID correto é aceita
        valid_payload = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"status": "success"}
        }).encode("utf-8")
        res = bridge.simulate_process_response(1, valid_payload)
        assert json.loads(res) == {"status": "success"}


def test_mcp_bridge_real_subprocess_e2e():
    """
    Teste ponta a ponta com servidor MCP real simulado em Python via stdio.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        bridge = McpBridge(tmp_dir)
        bridge.register_tool("echo_tool", "ReadOnly", [tmp_dir])

        # Servidor mock MCP que implementa initialize e tools/call via stdio
        server_script = (
            "import sys, json\n"
            "for line in sys.stdin:\n"
            "    req = json.loads(line)\n"
            "    req_id = req.get('id')\n"
            "    method = req.get('method')\n"
            "    if method == 'initialize':\n"
            "        resp = {'jsonrpc': '2.0', 'id': req_id, 'result': {'serverInfo': {'name': 'mock'}}}\n"
            "    elif method == 'tools/call':\n"
            "        resp = {'jsonrpc': '2.0', 'id': req_id, 'result': {'content': req['params']['arguments']}}\n"
            "    else:\n"
            "        resp = {'jsonrpc': '2.0', 'id': req_id, 'error': {'code': -32601, 'message': 'Not found'}}\n"
            "    sys.stdout.write(json.dumps(resp) + '\\n')\n"
            "    sys.stdout.flush()\n"
        )

        bridge.start(sys.executable, ["-u", "-c", server_script])
        
        # 1. Initialize
        init_res = json.loads(bridge.initialize())
        assert init_res["serverInfo"]["name"] == "mock"

        # 2. Call allowed tool with path normalization
        call_res = json.loads(bridge.call_tool(
            "echo_tool",
            json.dumps({"path": "sub/test.txt", "msg": "hello"}),
            False
        ))
        
        # Caminho deve ter sido normalizado com segurança
        expected_path = os.path.join(tmp_dir, "sub/test.txt")
        assert call_res["content"]["path"] == expected_path
        assert call_res["content"]["msg"] == "hello"

        # 3. Path traversal attack over subprocess must be blocked locally before sending
        with pytest.raises(PermissionError, match="PathTraversalDetected"):
            bridge.call_tool(
                "echo_tool",
                json.dumps({"path": "../../etc/shadow"}),
                False
            )

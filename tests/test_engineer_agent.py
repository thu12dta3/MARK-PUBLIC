from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from mark.engineer_agent import EngineerAgent, extract_code_from_llm_response
from mark.mcp_sandbox import SandboxMCPBridge
from mark.sandbox import ExecutionSandbox, SandboxSecurityError


class EngineerAgentTests(unittest.TestCase):
    def test_extract_code_from_llm_response_supports_markdown_and_json(self):
        md = "Aqui está a correção:\n```python\ndef add(a, b):\n    return a + b\n```\n"
        self.assertEqual(extract_code_from_llm_response(md).strip(), "def add(a, b):\n    return a + b")

        js = '{"code": "def sub(a, b):\\n    return a - b\\n"}'
        self.assertEqual(extract_code_from_llm_response(js).strip(), "def sub(a, b):\n    return a - b")

    def test_self_healing_loop_fixes_bug_after_capturing_test_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            src_file = ws / "calculator.py"
            test_file = ws / "test_calculator.py"

            # Código inicial com bug de divisão por zero / soma invertida
            src_file.write_text(
                "def calculate_discount(price: float, rate: float) -> float:\n"
                "    return price + (price * rate)\n",
                encoding="utf-8",
            )
            test_file.write_text(
                "from calculator import calculate_discount\n\n"
                "def test_discount():\n"
                "    assert calculate_discount(100.0, 0.2) == 80.0\n",
                encoding="utf-8",
            )

            captured_traces: list[str] = []

            def fake_llm(*, prompt, task, target_file, current_code, error_trace, attempt, route_decision):
                captured_traces.append(error_trace)
                if attempt == 1:
                    # Primeira tentativa introduz um erro que falha na asserção do pytest
                    return (
                        "```python\n"
                        "def calculate_discount(price: float, rate: float) -> float:\n"
                        "    return price - rate\n"
                        "```"
                    )
                # Segunda tentativa analisa o stack trace realimentado e corrige definitivamente
                assert "AssertionError" in error_trace or "assert" in error_trace
                return (
                    "```python\n"
                    "def calculate_discount(price: float, rate: float) -> float:\n"
                    "    return price - (price * rate)\n"
                    "```"
                )

            with ExecutionSandbox(workspace=ws) as sandbox:
                agent = EngineerAgent(sandbox=sandbox, llm_callable=fake_llm, max_retries=3)
                report = agent.solve(
                    task="Corrija o erro no cálculo de desconto em calculator.py",
                    target_file="calculator.py",
                    test_target="test_calculator.py",
                )

                self.assertTrue(report.success, msg=report.message)
                self.assertEqual(report.attempts, 2)
                self.assertEqual(len(report.history), 2)
                self.assertFalse(report.history[0].test_passed)
                self.assertTrue(report.history[1].lint_passed)
                self.assertTrue(report.history[1].test_passed)
                self.assertIn("assert", captured_traces[1])
                self.assertIn("price - (price * rate)", src_file.read_text(encoding="utf-8"))

    def test_self_healing_stops_at_max_3_attempts_and_restores_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            src_file = ws / "broken.py"
            test_file = ws / "test_broken.py"

            original = "def value():\n    return 0\n"
            src_file.write_text(original, encoding="utf-8")
            test_file.write_text(
                "from broken import value\n\n"
                "def test_value():\n"
                "    assert value() == 42\n",
                encoding="utf-8",
            )

            call_count = 0

            def always_wrong_llm(**kwargs):
                nonlocal call_count
                call_count += 1
                return f"```python\ndef value():\n    return {call_count}\n```"

            with ExecutionSandbox(workspace=ws) as sandbox:
                agent = EngineerAgent(sandbox=sandbox, llm_callable=always_wrong_llm, max_retries=3)
                report = agent.solve(
                    task="Analise o código deste módulo e corrija o retorno de value() para 42",
                    target_file="broken.py",
                    test_target="test_broken.py",
                )

                self.assertFalse(report.success)
                self.assertEqual(call_count, 3)
                self.assertEqual(report.attempts, 3)
                self.assertEqual(report.route_decision, "CLOUD_FALLBACK_TRIGGERED")
                # Garante que o arquivo original foi restaurado após esgotar as 3 tentativas
                self.assertEqual(src_file.read_text(encoding="utf-8"), original)

    def test_agent_rejects_target_file_outside_sandbox_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            with ExecutionSandbox(workspace=tmp) as sandbox:
                agent = EngineerAgent(sandbox=sandbox, llm_callable=lambda **_: "print(1)")
                with self.assertRaises(SandboxSecurityError):
                    agent.solve("Corrija arquivo externo", "../escape.py")

    def test_mcp_bridge_triggers_engineer_agent_solve(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            (ws / "math_mod.py").write_text("def double(x):\n    return x\n", encoding="utf-8")
            (ws / "test_math_mod.py").write_text(
                "from math_mod import double\n\ndef test_double():\n    assert double(5) == 10\n",
                encoding="utf-8",
            )

            sandbox = ExecutionSandbox(workspace=ws)
            agent = EngineerAgent(
                sandbox=sandbox,
                llm_callable=lambda **_: "```python\ndef double(x):\n    return x * 2\n```",
            )
            bridge = SandboxMCPBridge(sandbox=sandbox, agent=agent)
            res = bridge.execute_tool(
                "engineer_solve_task",
                {
                    "task": "Corrija a função double em math_mod.py",
                    "target_file": "math_mod.py",
                    "test_target": "test_math_mod.py",
                },
            )
            self.assertEqual(res["status"], "ok")
            self.assertTrue(res["result"]["success"])
            self.assertEqual(res["result"]["attempts"], 1)
            sandbox.close()

    def test_p0_confines_engineer_agent_and_blocks_path_traversal_symlinks_and_protected_files(self):
        from mark.engineer_agent import EngineerIntegrityError

        with tempfile.TemporaryDirectory() as tmp_ws, tempfile.TemporaryDirectory() as tmp_ext:
            ws = Path(tmp_ws)
            ext = Path(tmp_ext)
            secret_outside = ext / "outside_secret.py"
            secret_outside.write_text("SECRET = 'top_secret'\n", encoding="utf-8")

            symlink_inside = ws / "linked_escape.py"
            symlink_inside.symlink_to(secret_outside)

            with ExecutionSandbox(workspace=ws) as sandbox:
                agent = EngineerAgent(sandbox=sandbox, llm_callable=lambda **_: "x = 1\n")

                # 1. Bloqueia path traversal explícito, caminhos absolutos fora de /workspace e bytes nulos
                for forbidden_target in (
                    "../outside_secret.py",
                    "../../etc/passwd",
                    str(secret_outside),
                    "/etc/hosts",
                    "subdir/../../escape.py",
                    ".env",
                    "config.token",
                    "id_ed25519",
                    "src/mark/sandbox.py",
                    "src/mark/engineer_agent.py",
                ):
                    with self.assertRaises(SandboxSecurityError, msg=f"Deveria bloquear: {forbidden_target}"):
                        agent._resolve_workspace_path(forbidden_target)

                # 2. Bloqueia resolução de symlink apontando para fora do workspace
                with self.assertRaises(SandboxSecurityError):
                    agent._resolve_workspace_path("linked_escape.py")

                # 3. Aceita caminho virtualizado /workspace/... mapeando com segurança para dentro do workspace_root
                safe_mod = ws / "module_ok.py"
                safe_mod.write_text("val = 10\n", encoding="utf-8")
                resolved_virtual = agent._resolve_workspace_path("/workspace/module_ok.py")
                self.assertEqual(resolved_virtual, safe_mod.resolve())
                self.assertEqual(agent.read_workspace_file("/workspace/module_ok.py"), "val = 10\n")

                # 4. Preserva backup automático verificável com SHA-256 e faz rollback se a integridade falhar
                backup_snap = agent.create_backup(safe_mod)
                self.assertTrue(backup_snap.backup_path.exists())

                with self.assertRaises(EngineerIntegrityError):
                    agent.write_workspace_file(
                        "/workspace/module_ok.py",
                        "import subprocess\nsubprocess.Popen('sh', shell=True)\n",
                        backup=backup_snap,
                    )
                # Verifica que o arquivo original permaneceu intacto após tentativa de injeção bloqueada
                self.assertEqual(safe_mod.read_text(encoding="utf-8"), "val = 10\n")


if __name__ == "__main__":
    unittest.main()

import os
from pathlib import Path
import tempfile
import threading
import time
import unittest

from mark.missions import MissionStore, MissionRunner, MissionTool, default_workspace_tools
from mark.engineer_workspace import WorkspaceTools


class MissionStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "project"
        self.root.mkdir()
        self.db = Path(self.temp.name) / "missions.sqlite3"
        self.store = MissionStore(self.db)

    def tearDown(self):
        self.temp.cleanup()

    def mission(self):
        mission_id = self.store.create("Corrigir o projeto", workspace=self.root)
        grant = self.store.approve(mission_id, {"workspace.read", "workspace.write", "workspace.list"})
        return mission_id, grant

    def test_authorization_bound_to_mission_workspace_and_operation(self):
        mission_id, grant = self.mission()
        tools = WorkspaceTools(self.store)
        self.assertTrue(self.store.validate_grant(grant, "workspace.read", self.root))
        other = Path(self.temp.name) / "other"
        other.mkdir()
        self.assertFalse(self.store.validate_grant(grant, "workspace.read", other))
        with self.assertRaises(ValueError):
            tools.read_file(grant, self.root, "missing.py")
        self.assertFalse(self.store.validate_grant(grant, "workspace.verify", self.root))

    def test_mobile_or_non_desktop_mission_creation_rejected(self):
        with self.assertRaises(PermissionError):
            self.store.create("Test", source="mobile")

    def test_workspace_rejects_traversal_symlinks_secrets_and_policy_files(self):
        mission_id, grant = self.mission()
        tools = WorkspaceTools(self.store)
        external = Path(self.temp.name) / "secret.txt"
        external.write_text("outside", encoding="utf-8")
        (self.root / "link.txt").symlink_to(external)
        for path in ("../secret.txt", "link.txt", ".env", "src/mark/evolution/policy.py"):
            with self.subTest(path=path), self.assertRaises((ValueError, PermissionError)):
                tools.read_file(grant, self.root, path)

    def test_write_is_atomic_reports_diff_and_preserves_existing_mode(self):
        mission_id, grant = self.mission()
        target = self.root / "main.py"
        target.write_text("old = 1\n", encoding="utf-8")
        target.chmod(0o640)
        result = WorkspaceTools(self.store).write_file(grant, self.root, "main.py", "new = 2\n")
        self.assertTrue(result["changed"])
        self.assertIn("-old = 1", result["diff"])
        self.assertEqual(target.read_text(), "new = 2\n")
        self.assertEqual(target.stat().st_mode & 0o777, 0o640)

    def test_steps_execute_serially_and_retain_results(self):
        mission_id, grant = self.mission()
        tests = self.root / "tests"
        tests.mkdir()
        (tests / "test_result.py").write_text("import unittest\nclass T(unittest.TestCase):\n def test_value(self): self.assertEqual(2, 1 + 1)\n", encoding="utf-8")
        self.store.add_step(mission_id, "workspace.write", {"relative": "note.txt", "content": "presente"})
        self.store.add_step(mission_id, "workspace.read", {"relative": "note.txt"})

        runner = MissionRunner(self.store, default_workspace_tools(self.store))
        try:
            runner.enqueue(mission_id, grant).result(timeout=5)
        finally:
            runner.close()
        mission = self.store.list_missions()[0]
        self.assertEqual(mission["state"], "COMPLETED")
        self.assertEqual([step["state"] for step in mission["steps"]], ["COMPLETED", "COMPLETED"])
        self.assertIn("presente", mission["steps"][1]["result"])

    def test_restart_blocks_uncertain_running_step_without_replay(self):
        mission_id, grant = self.mission()
        self.store.add_step(mission_id, "workspace.write", {"relative": "note.txt", "content": "x"})
        self.store.set_step_state(mission_id, 1, "RUNNING")
        reopened = MissionStore(self.db)
        mission = reopened.list_missions()[0]
        self.assertEqual(mission["state"], "BLOCKED")
        self.assertEqual(mission["steps"][0]["state"], "BLOCKED")

    def test_expired_grant_rejected(self):
        mission_id = self.store.create("Test", workspace=self.root)
        grant = self.store.approve(mission_id, {"workspace.read"}, ttl_seconds=60)
        with self.store._db() as db:
            db.execute("UPDATE mission_grants SET expires_at=0 WHERE mission_id=?", (mission_id,))
        self.assertFalse(self.store.validate_grant(grant, "workspace.read", self.root))

    def test_transition_graph_and_terminal_state(self):
        mission_id = self.store.create("Test", workspace=self.root)
        with self.assertRaises(ValueError):
            self.store.transition(mission_id, "COMPLETED")
        self.store.transition(mission_id, "CANCELLED")
        with self.assertRaises(ValueError):
            self.store.transition(mission_id, "WAITING_APPROVAL")

    def test_arguments_are_allowlisted(self):
        mission_id, grant = self.mission()
        with self.assertRaises(ValueError):
            self.store.add_step(mission_id, "workspace.write", {"relative": "a", "content": "x", "shell": "id"})
        with self.assertRaises(ValueError):
            self.store.add_step(mission_id, "os.system", {"command": "id"})

    def test_qwen_plan_is_local_validated_and_only_proposed(self):
        from mark.missions import generate_plan
        class Response:
            def __init__(self, text): self.text = text
        class Provider:
            def __init__(self, responses): self.responses = list(responses); self.requests = []
            def generate(self, request): self.requests.append(request); return Response(self.responses.pop(0))
        mission_id = self.store.create("Corrigir o código", workspace=self.root)
        grant = self.store.approve(mission_id, {"workspace.list", "workspace.read", "workspace.write", "workspace.verify"})
        (self.root / "README.md").write_text("Conteúdo de fixture tratado como dado.", encoding="utf-8")
        provider = Provider([
            '{"read_paths":["README.md"]}',
            '{"steps":[{"tool":"workspace.write","arguments":{"relative":"solution.py","content":"value = 1\\n"}},{"tool":"workspace.verify","arguments":{"test_root":"tests"}}]}',
        ])
        cfg = {"ollama":{"url":"http://127.0.0.1:11434","model":"qwen","timeout":90},
               "models":{"chat":"qwen-chat"},"cognition":{}}
        result = generate_plan("Corrigir o código", self.root, grant, cfg, self.store, provider)
        self.assertEqual([step["tool"] for step in result], ["workspace.write", "workspace.verify"])
        self.assertFalse((self.root / "solution.py").exists())
        self.assertEqual(provider.requests[0].output_format, "json")

    def test_plan_refuses_remote_model_and_unregistered_tool(self):
        from mark.missions import generate_plan
        mission_id = self.store.create("Objetivo", workspace=self.root)
        grant = self.store.approve(mission_id, {"workspace.list", "workspace.read", "workspace.write", "workspace.verify"})
        class Response:
            def __init__(self, text): self.text = text
        class Provider:
            def __init__(self): self.calls = 0
            def generate(self, request):
                self.calls += 1
                value = '{"read_paths":[]}' if self.calls == 1 else '{"steps":[{"tool":"os.system","arguments":{"command":"id"}}]}'
                return Response(value)
        external = {"ollama":{"url":"http://example.com:11434","model":"qwen"},"models":{"chat":"qwen"}}
        provider = Provider()
        with self.assertRaises(PermissionError):
            generate_plan("Objetivo", self.root, grant, external, self.store, provider)
        self.assertEqual(provider.calls, 0)
        (self.root / "README.md").write_text("fixture", encoding="utf-8")
        local = {"ollama":{"url":"http://localhost:11434","model":"qwen","timeout":90},"models":{"chat":"qwen"},"cognition":{}}
        with self.assertRaises((PermissionError, ValueError)):
            generate_plan("Objetivo", self.root, grant, local, self.store, provider)

    def test_actual_python_tests_run_inside_isolation(self):
        mission_id = self.store.create("Verificar fixture Python", workspace=self.root)
        grant = self.store.approve(mission_id, {"workspace.verify"})
        tests = self.root / "tests"
        tests.mkdir()
        (tests / "test_fixture.py").write_text("import unittest\nclass T(unittest.TestCase):\n def test_ok(self): self.assertEqual('ok', 'ok')\n", encoding="utf-8")
        result = WorkspaceTools(self.store).verify_python(grant, self.root, "tests", threading.Event(), time.monotonic() + 30)
        self.assertTrue(result["executed"])
        self.assertEqual(result["status"], "PASS", result["output"])

    def test_laboratory_engineer_flow_modifies_and_verifies_real_files(self):
        mission_id = self.store.create("Corrigir soma e validar o projeto", workspace=self.root)
        grant = self.store.approve(mission_id, {"workspace.write", "workspace.verify"})
        (self.root / "tests").mkdir()
        (self.root / "tests" / "test_math.py").write_text(
            "import unittest\nfrom math_util import add\nclass MathTests(unittest.TestCase):\n def test_add(self): self.assertEqual(add(2, 3), 5)\n",
            encoding="utf-8")
        self.store.add_step(mission_id, "workspace.write", {"relative": "math_util.py", "content": "def add(a, b):\n    return a + b\n"})
        self.store.add_step(mission_id, "workspace.verify", {"test_root": "tests"})
        runner = MissionRunner(self.store, default_workspace_tools(self.store))
        try:
            runner.enqueue(mission_id, grant).result(timeout=10)
        finally:
            runner.close()
        mission = next(row for row in self.store.list_missions() if row["id"] == mission_id)
        self.assertEqual(mission["state"], "COMPLETED")
        self.assertEqual([step["state"] for step in mission["steps"]], ["COMPLETED", "COMPLETED"])
        self.assertIn('"status": "PASS"', mission["steps"][1]["result"])
        self.assertEqual((self.root / "math_util.py").read_text(), "def add(a, b):\n    return a + b\n")


if __name__ == "__main__":
    unittest.main()

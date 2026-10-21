import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[1] / "agent.py"
SPEC = importlib.util.spec_from_file_location("qwen_agent_under_test", SOURCE)
agent = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(agent)
import web_access


class ImmediateRoot:
    def after(self, _delay, callback):
        callback()


class ValueSink:
    def __init__(self):
        self.value = None

    def set(self, value):
        self.value = value


class AgentWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="qwen-agent-test-")
        self.base = Path(self.temp.name)
        self.workspace = self.base / "workspace"
        self.state = self.base / "state"
        self.workspace.mkdir()
        self.state.mkdir()
        self.app = object.__new__(agent.AgentApp)
        self.app.workspace = self.workspace
        self.app.project_dir = self.state
        self.app.plan_path = self.state / "current_plan.json"
        self.app.plan = {"title": "", "steps": []}
        self.app.root = ImmediateRoot()
        self.app.plan_var = ValueSink()

    def tearDown(self):
        self.temp.cleanup()

    def test_plan_persists_and_updates_visible_progress(self):
        result = self.app._update_plan(
            '{"title":"Refinar agente","steps":['
            '{"title":"Inspecionar","status":"done"},'
            '{"title":"Corrigir","status":"in_progress"},'
            '{"title":"Verificar","status":"pending"}]}'
        )
        self.assertEqual(result, "Plano atualizado: 1/3 etapas concluídas.")
        self.assertIn("Etapa 2/3 · Corrigir", self.app.plan_var.value)
        self.assertEqual(agent.safe_json(self.app.plan_path, {}), self.app.plan)
        self.assertEqual(self.app.plan_path.stat().st_mode & 0o777, 0o600)

    def test_plan_rejects_multiple_active_steps(self):
        raw = '{"title":"x","steps":[' \
              '{"title":"a","status":"in_progress"},' \
              '{"title":"b","status":"in_progress"}]}'
        with self.assertRaisesRegex(ValueError, "Só uma etapa"):
            self.app._update_plan(raw)
        self.assertFalse(self.app.plan_path.exists())

    def test_plan_rejects_non_object_json(self):
        with self.assertRaisesRegex(ValueError, "objeto JSON"):
            self.app._update_plan("[]")

    def test_plan_rejects_more_than_eight_steps(self):
        raw = '{"title":"x","steps":[' + ",".join(
            '{"title":"etapa","status":"pending"}' for _ in range(9)
        ) + ']}'
        with self.assertRaisesRegex(ValueError, "1 a 8"):
            self.app._update_plan(raw)

    def test_workspace_paths_reject_traversal_and_symlink(self):
        with self.assertRaisesRegex(ValueError, "fora do projeto"):
            self.app._inside("../fora.txt")
        outside = self.base / "outside.txt"
        outside.write_text("privado", encoding="utf-8")
        (self.workspace / "atalho.txt").symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "links simbólicos"):
            self.app._inside("atalho.txt")

    def test_approved_edit_backs_up_original_before_replacing(self):
        target = self.workspace / "src" / "sample.py"
        target.parent.mkdir()
        target.write_text("antes\n", encoding="utf-8")
        self.app._confirm = lambda *_args: True
        result = self.app._approve_edit("src/sample.py", "depois\n")
        self.assertIn("gravado após aprovação", result)
        self.assertEqual(target.read_text(encoding="utf-8"), "depois\n")
        backups = list((self.state / "backups").glob("*/src/sample.py"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(encoding="utf-8"), "antes\n")

    def test_rejected_edit_does_not_change_file_or_make_backup(self):
        target = self.workspace / "sample.py"
        target.write_text("antes\n", encoding="utf-8")
        self.app._confirm = lambda *_args: False
        self.assertIsNone(self.app._approve_edit("sample.py", "depois\n"))
        self.assertEqual(target.read_text(encoding="utf-8"), "antes\n")
        self.assertFalse((self.state / "backups").exists())

    def test_compound_commands_are_rejected_before_approval(self):
        self.app._confirm = lambda *_args: self.fail("Não deve pedir aprovação")
        with self.assertRaisesRegex(ValueError, "Comandos compostos"):
            self.app._approve_command("printf seguro; printf fora")

    def test_model_tool_selection_mislabeled_as_final_is_recovered(self):
        action = {"action": "final", "tool": "update_plan", "arguments": {}, "content": ""}
        normalized = agent.normalize_action(action)
        self.assertEqual(normalized["action"], "tool")
        self.assertEqual(action["action"], "final")

    def test_final_answer_without_tool_stays_final(self):
        action = {"action": "final", "tool": "none", "arguments": {}, "content": "Pronto."}
        self.assertEqual(agent.normalize_action(action), action)

    def test_links_from_markdown_html_and_plain_text_are_clickable_segments(self):
        text = 'Veja [Docs](https://example.org/docs), <a href="https://example.org/api?a=1&amp;b=2">API</a> e https://example.org/end.'
        segments = agent.link_segments(text)
        links = [url for _label, url in segments if url]
        self.assertEqual(links, ["https://example.org/docs", "https://example.org/api?a=1&b=2", "https://example.org/end"])
        self.assertIn((".", None), segments)

    def test_web_blocks_local_hosts_and_non_web_protocols(self):
        for url in ("http://127.0.0.1/", "http://[::1]/", "http://localhost/", "file:///etc/passwd", "https://example.com:8443/"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                web_access.validate_public_url(url)

    def test_web_refuses_redirects_to_local_addresses(self):
        handler = web_access._SafeRedirectHandler()
        request = web_access.urllib.request.Request("https://example.org/")
        with self.assertRaisesRegex(ValueError, "não é público"):
            handler.redirect_request(request, None, 302, "Found", {}, "http://127.0.0.1/")

    def test_web_search_extracts_direct_public_result_url(self):
        html = (
            '<a class="result__a" href="https://duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Fdocs">Docs</a>'
            '<a class="result__snippet">Official documentation</a>'
        )
        with patch.object(web_access, "_open", return_value=("https://html.duckduckgo.com/html/?q=docs", "text/html", html)):
            result = web_access.search_web("official docs", 2)
        self.assertIn("URL: https://example.org/docs", result)
        self.assertIn("Official documentation", result)

    def test_web_tools_are_reachable_from_agent_dispatch(self):
        self.app.root = ImmediateRoot()
        self.app.status_var = ValueSink()
        with patch.object(agent, "search_web", return_value="resultado da busca") as search:
            self.assertEqual(self.app._run_tool("web_search", {"query": "exemplo", "limit": "3"}), "resultado da busca")
        search.assert_called_once_with("exemplo", "3")
        self.assertIn("Pesquisando", self.app.status_var.value)
        with patch.object(agent, "fetch_page", return_value="texto da página") as fetch:
            self.assertEqual(self.app._run_tool("fetch_url", {"url": "https://example.org"}), "texto da página")
        fetch.assert_called_once_with("https://example.org")
        self.assertIn("Lendo página", self.app.status_var.value)

    def test_page_extraction_omits_scripts_and_keeps_public_links(self):
        html = '<title>Docs</title><script>secretNoise()</script><h1>Useful page</h1><p>Readable text</p><a href="/next">Next page</a>'
        with patch.object(web_access, "_open", return_value=("https://example.org/start", "text/html", html)):
            result = web_access.fetch_page("https://example.org/start")
        self.assertIn("Título: Docs", result)
        self.assertIn("Readable text", result)
        self.assertNotIn("secretNoise", result)
        self.assertIn("https://example.org/next", result)


if __name__ == "__main__":
    unittest.main(verbosity=2)

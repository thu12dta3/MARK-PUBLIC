
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from mark import free_ai

class FreeRouterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.p = patch.object(free_ai, "_STATE_PATH", Path(self.tmp.name) / "daily.sqlite3")
        self.p.start()
        self.addCleanup(self.p.stop)
        free_ai._UNTIL.clear()
        self.query = "Explique a diferença entre algoritmos de ordenação e suas complexidades."

    def test_default_off(self):
        self.assertIsNone(free_ai.try_public_answer(self.query, {}, origin="pc"))
        self.assertIsNone(free_ai.try_public_answer(self.query, {"free_ai": {"enabled": False}}, origin="pc"))

    def test_public_gate(self):
        self.assertTrue(free_ai.public_question(self.query))
        self.assertFalse(free_ai.public_question("Explique meu histórico pessoal."))
        self.assertFalse(free_ai.public_question("Explique o arquivo /home/example/private.txt."))
        self.assertFalse(free_ai.public_question("Explique o conteúdo de https://example.org/example."))
        self.assertFalse(free_ai.public_question("Explique esta mensagem.\nOutra linha"))

    def test_remote_never_sees_attachments(self):
        settings = {"free_ai": {"enabled": True, "mode": "auto_public"}}
        self.assertIsNone(free_ai.try_public_answer(self.query, settings, origin="pc",
                                                     attachment_context="document text"))
        self.assertIsNone(free_ai.try_public_answer(self.query, settings, origin="pc",
                                                     images=["image"]))
        self.assertIsNone(free_ai.try_public_answer(self.query, settings, origin="celular"))

    def test_only_allowlisted_free_models(self):
        self.assertEqual(free_ai.BY_NAME["openrouter-free"].model, "openrouter/free")
        self.assertTrue(all(ep.url.startswith("https://") for ep in free_ai.ENDPOINTS))

    def test_daily_budget(self):
        self.assertTrue(free_ai._claim_daily_slot("test-provider", 2, 2))
        self.assertTrue(free_ai._claim_daily_slot("test-provider", 2, 2))
        self.assertFalse(free_ai._claim_daily_slot("test-provider", 2, 2))

    def test_external_success_and_local_fallback(self):
        cfg = {"free_ai": {"enabled": True, "mode": "auto_public",
                            "max_attempts": 1, "daily_per_endpoint": 2}}
        endpoint = free_ai.BY_NAME["groq-reason"]
        with patch.object(free_ai, "route_candidates", return_value=[endpoint]):
            with patch.object(free_ai, "_call", return_value="Uma resposta pública") as call:
                answer = free_ai.try_public_answer(self.query, cfg, origin="pc")
                self.assertIn("Uma resposta pública", answer)
                self.assertEqual(call.call_args.args[1], self.query)
            with patch.object(free_ai, "_call", side_effect=OSError("offline")):
                self.assertIsNone(free_ai.try_public_answer(self.query, cfg, origin="pc"))
            with patch.object(free_ai, "_call") as call:
                self.assertIsNone(free_ai.try_public_answer(self.query, cfg, origin="pc"))
                call.assert_not_called()

    def test_no_configured_provider_means_local(self):
        with patch.object(free_ai, "route_candidates", return_value=[]):
            self.assertIsNone(free_ai.try_public_answer(self.query, {
                "free_ai": {"enabled": True, "mode": "auto_public"}}, origin="pc"))

if __name__ == "__main__":
    unittest.main()

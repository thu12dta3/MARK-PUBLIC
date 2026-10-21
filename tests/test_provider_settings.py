import json
import stat
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mark import provider_settings as providers


class _Response:
    def __init__(self, body, status=200):
        self.body = body
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, limit):
        return self.body[:limit]


class ProviderSettingsTests(unittest.TestCase):
    def test_key_is_sent_to_secret_tool_stdin_and_never_to_argv(self):
        secret = "private-provider-key"
        with patch.object(providers.shutil, "which", return_value="/usr/bin/secret-tool"), \
             patch.object(providers.subprocess, "run", return_value=type("R", (), {"returncode": 0})()) as run:
            providers.store_api_key("groq", secret)
        args, kwargs = run.call_args
        self.assertNotIn(secret, args[0])
        self.assertEqual(kwargs["input"], secret + "\n")
        self.assertIn("provider", args[0])
        self.assertIn("groq", args[0])

    def test_missing_secret_service_never_falls_back_to_a_plaintext_file(self):
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(providers, "CONFIG_PATH", Path(folder) / "providers.json"), \
             patch.object(providers.shutil, "which", return_value=None):
            with self.assertRaises(providers.ProviderError):
                providers.store_api_key("gemini", "private-key")
            self.assertFalse(providers.CONFIG_PATH.exists())

    def test_provider_options_are_private_and_contain_no_api_key(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(providers, "CONFIG_PATH", Path(folder) / "providers.json"):
            providers.set_provider_settings("groq", model="qwen/qwen3.8-27b", enabled=True,
                                            free_only_confirmed=True)
            raw = providers.CONFIG_PATH.read_text()
            self.assertNotIn("private-provider-key", raw)
            self.assertEqual(stat.S_IMODE(providers.CONFIG_PATH.stat().st_mode), 0o600)
            self.assertTrue(providers.provider_enabled("groq"))
            self.assertEqual(providers.selected_model("groq"), "qwen/qwen3.8-27b")

    def test_paid_openrouter_model_is_rejected_by_free_router(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(providers, "CONFIG_PATH", Path(folder) / "providers.json"):
            with self.assertRaises(ValueError):
                providers.set_provider_settings("openrouter", model="vendor/model", enabled=True,
                                                free_only_confirmed=True)
            providers.set_provider_settings("openrouter", model="vendor/model:free", enabled=True,
                                            free_only_confirmed=True)

    def test_catalog_request_sends_no_question_and_filters_gemini_capability(self):
        body = json.dumps({"models": [
            {"name": "models/gemini-1", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/embed-1", "supportedGenerationMethods": ["embedContent"]},
        ]}).encode()
        with patch.object(providers, "get_api_key", return_value="key-hidden"), \
             patch.object(providers.urllib.request, "urlopen", return_value=_Response(body)) as urlopen:
            models = providers.list_models("gemini")
        self.assertEqual(models, [{"id": "gemini-1", "free_candidate": False}])
        request = urlopen.call_args.args[0]
        self.assertEqual(request.method, "GET")
        self.assertEqual(request.data, None)
        self.assertEqual(request.get_header("X-goog-api-key"), "key-hidden")

    def test_provider_denial_is_reported_without_server_body(self):
        with patch.object(providers, "get_api_key", return_value="key-hidden"), \
             patch.object(providers.urllib.request, "urlopen", side_effect=[
                 urllib.error.HTTPError("https://example", 403, "denied", {}, None),
                 urllib.error.HTTPError("https://example", 429, "rate limited", {}, None),
             ]):
            with self.assertRaises(providers.ProviderError) as caught:
                providers.list_models("groq")
            self.assertIn("403", str(caught.exception))
            with self.assertRaises(providers.ProviderError) as limited:
                providers.list_models("groq")
        self.assertIn("429", str(limited.exception))


if __name__ == "__main__":
    unittest.main()

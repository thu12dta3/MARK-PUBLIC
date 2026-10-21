import json
import http.client
import os
import stat
import sys
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mark.config import load_config
from mark.mobile_api import MobileServer, mobile_token
from mark.model_state import ModelSession
from mark.memory import SharedMemory, ShortMemory
from mark.agenda import Agenda


class FakeSession:
    def __init__(self):
        self.awake = False

    def wake(self):
        self.awake = True

    def sleep(self):
        self.awake = False


class MobileAPITests(unittest.TestCase):
    def setUp(self):
        self.agenda_directory = TemporaryDirectory()
        agenda_path = Path(self.agenda_directory.name) / "agenda.sqlite3"
        missing_cfg = Path(self.agenda_directory.name) / "no-config.json"
        with patch("mark.config.CONFIG_FILE", missing_cfg), \
             patch("mark.mobile_api.Agenda", side_effect=lambda: Agenda(agenda_path)):
            self.server = MobileServer(load_config(), "test-private-token", 0, memory=ShortMemory())
        self.server.model_sessions = {"chat": FakeSession(), "call": FakeSession()}
        self.server.model_session = self.server.model_sessions["call"]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.agenda_directory.cleanup()

    def request(self, path, payload=None, token="test-private-token"):
        headers = {"Authorization": "Bearer " + token}
        body = None if payload is None else json.dumps(payload).encode()
        if body is not None:
            headers["Content-Type"] = "application/json"
        return urllib.request.Request(self.base + path, data=body, headers=headers)

    def test_models_requires_token_and_exposes_both_profiles(self):
        with self.assertRaises(urllib.error.HTTPError) as denied:
            urllib.request.urlopen(self.request("/v1/models", token="wrong"), timeout=3)
        self.assertEqual(denied.exception.code, 401)
        denied.exception.close()
        with urllib.request.urlopen(self.request("/v1/models"), timeout=3) as response:
            catalog = json.load(response)
        self.assertEqual([item["id"] for item in catalog["data"]], ["mark", "mark-call"])

    def test_agenda_endpoint_requires_token_and_returns_shared_items(self):
        with self.assertRaises(urllib.error.HTTPError) as denied:
            urllib.request.urlopen(self.request("/v1/agenda", token="wrong"), timeout=3)
        self.assertEqual(denied.exception.code, 401)
        denied.exception.close()
        with urllib.request.urlopen(self.request("/v1/agenda"), timeout=3) as response:
            self.assertEqual(json.load(response), {"items": []})

    def test_history_endpoint_requires_token_and_returns_shared_conversation(self):
        self.server.memory.add("pergunta no PC", "resposta no PC")
        with self.assertRaises(urllib.error.HTTPError) as denied:
            urllib.request.urlopen(self.request("/v1/history", token="wrong"), timeout=3)
        self.assertEqual(denied.exception.code, 401)
        denied.exception.close()
        with urllib.request.urlopen(self.request("/v1/history"), timeout=3) as response:
            history = json.load(response)
        self.assertEqual(history["items"][0]["user"], "pergunta no PC")
        self.assertEqual(history["items"][0]["assistant"], "resposta no PC")

    def test_call_page_and_root_close_idle_http_connections(self):
        from urllib.parse import urlsplit
        target = urlsplit(self.base)
        conn = http.client.HTTPConnection(target.hostname, target.port, timeout=3)
        try:
            conn.request("GET", "/")
            redirected = conn.getresponse()
            self.assertEqual(redirected.status, 302)
            self.assertEqual(redirected.getheader("Connection"), "close")
        finally:
            conn.close()
        conn = http.client.HTTPConnection(target.hostname, target.port, timeout=3)
        try:
            conn.request("GET", "/call")
            response = conn.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(response.getheader("Connection"), "close")
        finally:
            conn.close()

    def test_chat_uses_mark_brain_and_conversation_history(self):
        payload = {"model": "mark", "messages": [
            {"role": "system", "content": "Persona do app"},
            {"role": "user", "content": "oi"},
            {"role": "assistant", "content": "olá"},
            {"role": "user", "content": "que horas são?"},
        ]}
        with patch("mark.mobile_api.answer", return_value="Agora são 10h00.") as brain:
            with urllib.request.urlopen(self.request("/v1/chat/completions", payload), timeout=3) as response:
                result = json.load(response)
        self.assertEqual(brain.call_args.args[0], "que horas são?")
        self.assertIn("Usuário: oi\nMARK: olá", brain.call_args.args[2].render())
        self.assertEqual(brain.call_args.args[1]["ollama"]["model"], "haervwe/qwen3-vl-4b-heretic:latest")
        self.assertEqual(result["choices"][0]["message"]["content"], "Agora são 10h00.")

    def test_voice_profile_uses_fast_model_and_shared_answer_path(self):
        handler = self._handler()
        with patch("mark.mobile_api.answer", return_value="Resposta rápida") as brain:
            reply = handler._answer("alô", self.server.memory, "call")
        self.assertEqual(reply, "Resposta rápida")
        self.assertEqual(brain.call_args.args[1]["ollama"]["model"], "haervwe/qwen3-vl-4b-heretic:latest")

    def test_localmind_mark_call_alias_selects_fast_profile(self):
        payload = {"model": "mark-call", "messages": [{"role": "user", "content": "olá"}]}
        with patch("mark.mobile_api.answer", return_value="Olá.") as brain:
            with urllib.request.urlopen(self.request("/v1/chat/completions", payload), timeout=3):
                pass
        self.assertEqual(brain.call_args.args[1]["ollama"]["model"], "haervwe/qwen3-vl-4b-heretic:latest")

    def test_desktop_request_is_tagged_separately_from_legacy_client(self):
        payload = {"model": "mark", "messages": [{"role": "user", "content": "oi"}]}
        request = self.request("/v1/chat/completions", payload)
        request.add_header("X-Mark-Client", "desktop")
        with patch("mark.mobile_api.answer", return_value="Olá") as brain:
            with urllib.request.urlopen(request, timeout=3):
                pass
        self.assertEqual(brain.call_args.kwargs["origin"], "pc")
        self.assertEqual(self.server.memory.items[-1]["source"], "desktop-chat")

    def test_signed_device_is_identified_as_phone(self):
        handler = self._handler()
        handler._device_id = "test-device"
        self.assertEqual(handler._origin(), "celular")
        self.assertEqual(handler._source("call"), "mobile-call")

    def test_agenda_command_does_not_wake_either_model(self):
        handler = self._handler()
        with patch("mark.mobile_api.answer", side_effect=AssertionError("agenda local")):
            reply = handler._answer("o que tenho hoje?", self.server.memory, "chat")
        self.assertIn("agenda está vazia", reply)
        self.assertFalse(self.server.model_sessions["chat"].awake)
        self.assertFalse(self.server.model_sessions["call"].awake)

    def _handler(self):
        from mark.mobile_api import MobileHandler
        handler = object.__new__(MobileHandler)
        handler.server = self.server
        return handler

    def test_streaming_uses_openai_sse_format(self):
        payload = {"model": "mark", "stream": True,
                   "messages": [{"role": "user", "content": "diga olá"}]}
        with patch("mark.mobile_api.answer", return_value="Olá."):
            with urllib.request.urlopen(self.request("/v1/chat/completions", payload), timeout=3) as response:
                stream = response.read().decode()
        self.assertIn('"object": "chat.completion.chunk"', stream)
        self.assertIn('"content": "Olá."', stream)
        self.assertTrue(stream.endswith("data: [DONE]\n\n"))

    def test_mobile_goodbye_returns_to_sleep_without_calling_brain(self):
        payload = {"model": "mark", "messages": [{"role": "user", "content": "pode descansar"}]}
        self.server.model_session.wake()
        with patch("mark.mobile_api.answer", side_effect=AssertionError("Qwen desnecessário")):
            with urllib.request.urlopen(self.request("/v1/chat/completions", payload), timeout=3) as response:
                result = json.load(response)
        self.assertFalse(self.server.model_session.awake)
        self.assertIn("descansar", result["choices"][0]["message"]["content"])

    def test_mobile_token_is_private_and_stable(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "mobile.token"
            first = mobile_token(path)
            self.assertEqual(mobile_token(path), first)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertGreaterEqual(len(first), 40)


class ModelSessionTests(unittest.TestCase):
    def test_qwen_stays_loaded_until_last_active_session_sleeps(self):
        with TemporaryDirectory() as directory, patch("mark.model_state._model_command") as command:
            first_cfg = load_config()
            second_cfg = load_config()
            first = ModelSession(first_cfg, Path(directory))
            second = ModelSession(second_cfg, Path(directory))
            first.wake()
            second.wake()
            self.assertEqual(first_cfg["ollama"]["keep_alive"], -1)
            first.sleep()
            self.assertTrue(second.awake)
            self.assertEqual([args.args[1] for args in command.call_args_list], [-1, -1])
            second.sleep()
            self.assertEqual([args.args[1] for args in command.call_args_list], [-1, -1, 0])
            self.assertEqual(second_cfg["ollama"]["keep_alive"], 0)

    def test_sessions_keep_chat_and_call_models_independently(self):
        with TemporaryDirectory() as directory, patch("mark.model_state._model_command") as command:
            chat_cfg = load_config()
            chat_cfg["ollama"]["model"] = "qwen3-vl:4b"
            call_cfg = load_config()
            call_cfg["ollama"]["model"] = "qwen3-vl:4b-instruct"
            chat = ModelSession(chat_cfg, Path(directory))
            call = ModelSession(call_cfg, Path(directory))
            chat.wake()
            call.wake()
            chat.sleep()
            self.assertFalse(chat.awake)
            self.assertTrue(call.awake)
            self.assertEqual(command.call_args_list[-1].args[0]["ollama"]["model"], "qwen3-vl:4b")
            call.sleep()

    def test_explicit_sleep_cleans_a_stale_session_after_restart(self):
        with TemporaryDirectory() as directory, patch("mark.model_state._model_command") as command:
            state = Path(directory)
            stale = state / "session-99999999-obsolete"
            stale.touch(mode=0o600)
            session = ModelSession(load_config(), state)
            session.sleep()
            self.assertFalse(stale.exists())
            self.assertEqual(command.call_count, 1)
            self.assertEqual(command.call_args.args[1], 0)


class SharedMemoryTests(unittest.TestCase):
    def test_same_conversation_is_visible_to_two_process_instances(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "state" / "conversation.sqlite3"
            phone = SharedMemory(6, path)
            helmet = SharedMemory(6, path)
            phone.add("meu nome é Alex", "Prazer, Alex.", source="mobile-chat")
            self.assertIn("meu nome é Alex", helmet.render())
            helmet.add("qual é meu nome?", "Alex.", source="pc-voice")
            self.assertIn("qual é meu nome?", phone.render())
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            helmet.clear()
            self.assertEqual(phone.render(), "Nenhuma.")


if __name__ == "__main__":
    unittest.main()

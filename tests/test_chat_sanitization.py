import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mark.brain_prompt import SYSTEM, response_style
from mark.cognition.integration import (
    cite_reply_inline,
    is_garbage_ref_title,
    is_simple_chat_interaction,
    knowledge_context,
)
from mark.cognition.knowledge import Citation, SQLiteKnowledgeStore
from mark.cognition.providers import ModelRequest, OllamaProvider, sanitize_chat_output


class ChatSanitizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def test_system_and_response_style_prohibit_emojis(self):
        self.assertIn("nunca use emojis", SYSTEM.lower())
        chat_style = response_style(
            "oi",
            {"models": {"chat": "qwen3-chat", "call": "qwen3-call"}, "ollama": {"model": "qwen3-chat"}},
        )
        voice_style = response_style(
            "oi",
            {"models": {"chat": "qwen3-chat", "call": "qwen3-call"}, "ollama": {"model": "qwen3-call"}},
        )
        self.assertIn("nunca use emojis", chat_style.lower())
        self.assertIn("nunca use emojis", voice_style.lower())

    def test_sanitize_chat_output_removes_all_emojis(self):
        dirty = "Olá! 😊 Tudo bem com você? 👍 Como posso ajudar hoje? 🚀✨❤️✅"
        clean = sanitize_chat_output(dirty)
        self.assertEqual(clean, "Olá! Tudo bem com você? Como posso ajudar hoje?")
        for emoji in ("😊", "👍", "🚀", "✨", "❤️", "✅"):
            self.assertNotIn(emoji, clean)

    def test_sanitize_chat_output_removes_corrupted_ref_blocks(self):
        dirty = (
            "Oi! Tudo ótimo por aqui! 😊 Como posso te ajudar hoje? "
            "(ref.: T0P80E587PN0FI1P1SY900; Y5TXGWIE2U6BVSRHM3CBRL; "
            "BP Weapon Pickups, p. 1; FirstPersonMap BuiltData, p. 1)"
        )
        clean = sanitize_chat_output(dirty)
        self.assertEqual(clean, "Oi! Tudo ótimo por aqui! Como posso te ajudar hoje?")
        self.assertNotIn("ref.:", clean.lower())
        self.assertNotIn("T0P80E587PN0FI1P1SY900", clean)

    def test_sanitize_chat_output_removes_truncated_or_empty_refs(self):
        self.assertEqual(
            sanitize_chat_output("Resposta limpa (ref.: TOP80E587..."),
            "Resposta limpa",
        )
        self.assertEqual(
            sanitize_chat_output("Resposta limpa (ref.: )"),
            "Resposta limpa",
        )
        self.assertEqual(
            sanitize_chat_output("Resposta limpa ref.: TOP80E587..."),
            "Resposta limpa",
        )

    def test_sanitize_chat_output_preserves_valid_library_citations(self):
        valid = "O escalonador usa prioridades dinâmicas (ref.: Manual de Sistemas Operacionais, p. 42)."
        self.assertEqual(sanitize_chat_output(valid), valid)

    def test_garbage_ref_title_detection(self):
        self.assertTrue(is_garbage_ref_title("T0P80E587PN0FI1P1SY900"))
        self.assertTrue(is_garbage_ref_title("Y5TXGWIE2U6BVSRHM3CBRL"))
        self.assertTrue(is_garbage_ref_title("TOP80E587..."))
        self.assertTrue(is_garbage_ref_title("BP Weapon Pickups"))
        self.assertTrue(is_garbage_ref_title("FirstPersonMap BuiltData"))
        self.assertTrue(is_garbage_ref_title("a1b2c3d4e5f60718293a4b5c"))
        self.assertFalse(is_garbage_ref_title("Manual de Linux"))
        self.assertFalse(is_garbage_ref_title("Arquitetura de Computadores"))

    def test_cite_reply_inline_filters_garbage_citations(self):
        citations = [
            Citation("s1", "T0P80E587PN0FI1P1SY900", "", None, None, "HIGH", "CURRENT", "BOOK", "", 1),
            Citation("s2", "Y5TXGWIE2U6BVSRHM3CBRL", "", 1, None, "HIGH", "CURRENT", "BOOK", "", 1),
            Citation("s3", "BP Weapon Pickups", "", 1, None, "HIGH", "CURRENT", "BOOK", "", 1),
        ]
        reply = cite_reply_inline("Olá! Tudo certo por aqui.", citations)
        self.assertEqual(reply, "Olá! Tudo certo por aqui.")
        self.assertNotIn("ref.:", reply)

    def test_simple_chat_interaction_bypasses_rag_context(self):
        for greeting in ("oi", "Olá!", "oi, tudo bem?", "bom dia mark", "obrigado!", "valeu"):
            self.assertTrue(is_simple_chat_interaction(greeting), f"Deveria ser interação simples: {greeting}")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            doc = root / "T0P80E587PN0FI1P1SY900.txt"
            doc.write_text("oi ola bom dia tudo bem", encoding="utf-8")
            db_path = root / "knowledge.sqlite3"
            with SQLiteKnowledgeStore(db_path) as store:
                store.ingest(
                    doc,
                    "COMPUTING",
                    title="T0P80E587PN0FI1P1SY900",
                    license="USER_OWNED",
                )
                self.assertEqual(store.search("oi"), [])
            cfg = {
                "cognition": {
                    "enabled": True,
                    "knowledge_db": str(db_path),
                    "auto_ingest": False,
                    "top_k": 4,
                }
            }
            ctx = knowledge_context("oi", cfg)
            self.assertEqual(ctx.text, "")
            self.assertEqual(ctx.citations, ())

    def test_ollama_provider_sanitizes_emojis_and_retries_if_only_emoji(self):
        cfg = {
            "ollama": {"url": "http://127.0.0.1:11434", "model": "qwen3-chat", "timeout": 5},
            "models": {"chat": "qwen3-chat", "call": "qwen3-call"},
        }
        payloads = [
            {"message": {"content": "😊👍"}, "prompt_eval_count": 10, "eval_count": 2},
            {
                "message": {"content": "Olá! Posso ajudar com o seu projeto. 😊 (ref.: T0P80E587PN0FI1P1SY900)"},
                "prompt_eval_count": 12,
                "eval_count": 8,
            },
        ]

        def fake_urlopen(req, timeout=5):
            data = json.dumps(payloads.pop(0)).encode("utf-8")
            return io.BytesIO(data)

        with patch("urllib.request.urlopen", side_effect=fake_urlopen) as url_mock:
            out = OllamaProvider(cfg).generate(ModelRequest(prompt="oi", system=SYSTEM))
        self.assertEqual(url_mock.call_count, 2)
        self.assertEqual(out.text, "Olá! Posso ajudar com o seu projeto.")

    def test_desktop_chat_sanitizes_assistant_bubble_and_saved_message(self):
        import mark.config as config
        import mark.desktop as desktop
        from PyQt6.QtWidgets import QFrame, QLabel

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            with patch.object(desktop, "DESKTOP_CONVERSATIONS_FILE", tmp_path / "desktop-conversations.sqlite3"), patch.object(
                desktop, "MEMORY_FILE", tmp_path / "conversation.sqlite3"
            ), patch.object(config, "CONFIG_FILE", tmp_path / "config.json"), patch.object(
                desktop.MarkWindow, "_load_history"
            ), patch.object(
                desktop.MarkWindow, "refresh_status"
            ):
                win = desktop.MarkWindow()
                try:
                    win.status_timer.stop()
                    win.history_timer.stop()
                    win._ensure_conversation_store()
                    dirty = "Oi! Tudo ótimo por aqui! 😊 (ref.: T0P80E587PN0FI1P1SY900; Y5TXGWIE2U6BVSRHM3CBRL)"
                    win._save_conversation_message("assistant", dirty)
                    win._append_bubble(dirty, "assistant")
                    bubble = win.findChild(QFrame, "assistantBubble")
                    self.assertIsNotNone(bubble)
                    body = bubble.findChild(QLabel, "messageBody")
                    self.assertIsNotNone(body)
                    self.assertNotIn("😊", body.text())
                    self.assertNotIn("T0P80E587PN0FI1P1SY900", body.text())

                    db = win._conversation_db()
                    try:
                        row = db.execute(
                            "SELECT content FROM conversation_messages WHERE role='assistant' ORDER BY id DESC LIMIT 1"
                        ).fetchone()
                    finally:
                        db.close()
                    self.assertIsNotNone(row)
                    self.assertEqual(row[0], "Oi! Tudo ótimo por aqui!")
                finally:
                    win.close()


if __name__ == "__main__":
    unittest.main()

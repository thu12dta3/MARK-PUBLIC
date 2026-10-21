from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mark.brain_model import _ollama
from mark.core.context import CoreContext, bind_context, current_context
from mark.core.events import CoreEvent, EventBus
from mark.core.specializations import default_specializations
from mark.cognition.providers import ModelMetrics, ModelResponse
from mark.memory import SharedMemory, ShortMemory
from mark.config import load_config
from mark.brain import answer


class CoreContextTests(unittest.TestCase):
    def test_context_ids_are_unique_and_optional_scopes_are_explicit(self):
        first = CoreContext.create(source="desktop-chat")
        second = CoreContext.create(source="desktop-chat")
        self.assertNotEqual(first.request_id, second.request_id)
        self.assertIsNone(first.session_id)
        self.assertIsNone(first.project_id)
        self.assertIsNone(first.task_id)

    def test_context_validates_identifiers_without_accepting_payload_text(self):
        with self.assertRaises(ValueError):
            CoreContext(request_id="line1\nassistant: fake")
        with self.assertRaises(ValueError):
            CoreContext(session_id="x" * 129)
        with self.assertRaises(ValueError):
            CoreContext(specialization="unknown/../../path")

    def test_binding_is_nested_and_restored(self):
        outer = CoreContext.create(source="desktop-chat")
        inner = CoreContext.create(source="mobile-chat")
        with bind_context(outer):
            self.assertEqual(current_context(), outer)
            with bind_context(inner):
                self.assertEqual(current_context(), inner)
            self.assertEqual(current_context(), outer)
        after = current_context()
        self.assertEqual(after.source, "unknown")
        self.assertIsNone(after.session_id)
        self.assertNotEqual(after.request_id, outer.request_id)

    def test_registry_contains_real_modes_without_external_plugins(self):
        registry = default_specializations()
        self.assertEqual([entry.key for entry in registry.list()], ["general", "engineer", "professor"])
        self.assertIn("computing", registry.resolve("engineer").domains)
        with self.assertRaises(ValueError):
            registry.resolve("imaginary")

    def test_event_payload_is_immutable_and_rejects_conversation_content(self):
        context = CoreContext.create(source="desktop-chat")
        event = CoreEvent("mark.assistant.request.completed", context,
                          metadata={"status": "completed", "origin": "desktop-chat"})
        with self.assertRaises(TypeError):
            event.metadata["status"] = "changed"
        with self.assertRaises(ValueError):
            CoreEvent("mark.request", context, metadata={"prompt": "private text"})

    def test_event_bus_filters_unsubscribes_and_isolates_listener_errors(self):
        bus = EventBus()
        seen = []
        bus.subscribe(lambda event: (_ for _ in ()).throw(RuntimeError("fixture")))
        remove = bus.subscribe(seen.append, "mark.model.request.completed")
        context = CoreContext.create()
        bus.publish(CoreEvent("mark.model.request.started", context))
        bus.publish(CoreEvent("mark.model.request.completed", context))
        self.assertEqual(len(seen), 1)
        remove()
        bus.publish(CoreEvent("mark.model.request.completed", context))
        self.assertEqual(len(seen), 1)

    def test_model_request_uses_bound_context_and_emits_safe_lifecycle_events(self):
        context = CoreContext.create(session_id="session-123", source="mobile-chat")
        recorded = []
        from mark.core.events import events
        remove = events.subscribe(recorded.append)
        metrics = ModelMetrics("fixture", 4.0, 2, 3, 1, True)

        class Provider:
            last_metrics = None

            def generate(self, request):
                self.request = request
                return ModelResponse("fixture reply", metrics)

        provider = Provider()
        try:
            with bind_context(context), patch("mark.brain_model.get_provider", return_value=provider):
                self.assertEqual(_ollama("fixture prompt", {"ollama": {"model": "fixture"}}), "fixture reply")
        finally:
            remove()
        self.assertEqual(provider.request.context, context)
        self.assertEqual([event.name for event in recorded], [
            "mark.model.request.started", "mark.model.request.completed",
        ])
        self.assertEqual(recorded[0].context.request_id, recorded[1].context.request_id)
        self.assertNotIn("fixture prompt", repr(recorded))

    def test_additive_memory_migration_preserves_existing_conversation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "conversation.sqlite3"
            with closing(sqlite3.connect(path)) as db:
                with db:
                    db.execute("CREATE TABLE turns (id INTEGER PRIMARY KEY, created_at INTEGER NOT NULL, source TEXT NOT NULL, user_text TEXT NOT NULL, assistant_text TEXT NOT NULL)")
                    db.execute("INSERT INTO turns VALUES (1, 123, 'desktop-chat', 'historico antigo', 'resposta antiga')")
            path.chmod(0o600)
            memory = SharedMemory(path=path)
            self.assertEqual(memory.history()[0]["user"], "historico antigo")
            with closing(sqlite3.connect(path)) as db:
                self.assertIsNotNone(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='scoped_memories'").fetchone())

    def test_scoped_memory_crosses_specializations_only_inside_shared_project(self):
        with tempfile.TemporaryDirectory() as directory:
            memory = SharedMemory(path=Path(directory) / "conversation.sqlite3")
            engineer = CoreContext.create(specialization="engineer", project_id="mark-core")
            general_same_project = CoreContext.create(specialization="general", project_id="mark-core")
            professor_same_project = CoreContext.create(specialization="professor", project_id="school")
            memory.remember_fact("Decisão: manter o histórico SQLite compatível.", "project", engineer)
            memory.remember_fact("Preferência pessoal: respostas em português.", "global", engineer)
            memory.remember_fact("Engineer prefere compilação estrita.", "specialization", engineer)
            engineer_task = CoreContext.create(specialization="engineer", project_id="mark-core", task_id="upload-fix")
            memory.remember_fact("Tarefa ativa: corrigir o envio de arquivos.", "task", engineer_task)
            self.assertEqual(len(memory.recall_facts("histórico SQLite", general_same_project)), 1)
            self.assertEqual(len(memory.recall_facts("português", professor_same_project)), 1)
            self.assertEqual(memory.recall_facts("histórico SQLite", professor_same_project), [])
            self.assertEqual(memory.recall_facts("compilação estrita", general_same_project), [])
            self.assertEqual(len(memory.recall_facts("compilação estrita", engineer)), 1)
            self.assertEqual(memory.recall_facts("envio arquivos", engineer), [])
            self.assertEqual(len(memory.recall_facts("envio arquivos", engineer_task)), 1)

    def test_scoped_memory_deduplicates_and_respects_retrieval_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            memory = SharedMemory(path=Path(directory) / "conversation.sqlite3")
            context = CoreContext.create()
            first = memory.remember_fact("A decisão local é preservar o histórico compartilhado.", context=context)
            duplicate = memory.remember_fact(" A decisão local é preservar o histórico compartilhado. ", context=context)
            self.assertTrue(first["saved"])
            self.assertFalse(duplicate["saved"])
            self.assertEqual(memory.recall_facts("histórico", context, character_budget=5), [])

    def test_explicit_memory_command_saves_locally_without_calling_model(self):
        with tempfile.TemporaryDirectory() as directory:
            memory = SharedMemory(path=Path(directory) / "conversation.sqlite3")
            with patch("mark.brain._ollama", side_effect=AssertionError("modelo desnecessário")):
                reply = answer("lembre que prefiro usar o Qwen local", load_config(), memory)
            self.assertIn("Salvei", reply)
            self.assertEqual(len(memory.recall_facts("Qwen local", CoreContext.create())), 1)

    def test_selected_scoped_memory_reaches_the_shared_answer_pipeline(self):
        from mark.cognition.integration import RetrievalContext

        with tempfile.TemporaryDirectory() as directory:
            memory = SharedMemory(path=Path(directory) / "conversation.sqlite3")
            context = CoreContext.create(project_id="mark-core", source="desktop-chat")
            memory.remember_fact("A decisão é manter o mesmo núcleo para todos os modos.", "project", context)
            prompts = []

            def model(prompt, *args, **kwargs):
                prompts.append(prompt)
                return "Resposta de teste"

            with patch("mark.brain.route", return_value={"vision": False, "web": False}), \
                 patch("mark.brain.handle_agenda_command", return_value=None), \
                 patch("mark.brain.direct_response", return_value=None), \
                 patch("mark.brain.knowledge_context", return_value=RetrievalContext()), \
                 patch("mark.github_assistant.context", return_value=""), \
                 patch("mark.coding_assistant.review_reply", return_value=("Resposta de teste", {})), \
                 patch("mark.brain._ollama", side_effect=model):
                answer("Qual a decisão sobre o núcleo deste projeto?", load_config(), memory, context=context)
            self.assertTrue(prompts)
            self.assertIn("A decisão é manter o mesmo núcleo", prompts[0])
            self.assertIn("referências do usuário", prompts[0])


if __name__ == "__main__":
    unittest.main()

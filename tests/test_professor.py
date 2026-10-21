import sys
import tempfile
import unittest
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mark.cognition.professor import StudyPlanStore, build_study_prompt
from mark.cognition.knowledge import KnowledgeStore
from mark.cognition.integration import knowledge_context


class ProfessorTests(unittest.TestCase):
    def test_modes_build_bounded_interactive_prompts(self):
        prompt = build_study_prompt(
            "Circuitos elétricos", "quiz", "intermediário",
            objective="calcular corrente", source="Manual de circuitos.pdf",
        )
        self.assertIn("uma pergunta por vez", prompt)
        self.assertIn('"Manual de circuitos.pdf"', prompt)
        self.assertIn("somente minha biblioteca local", prompt)
        with self.assertRaises(ValueError):
            build_study_prompt("", "explain")
        with self.assertRaises(ValueError):
            build_study_prompt("Física", "unknown")
        with self.assertRaises(ValueError):
            build_study_prompt("x" * 161, "explain")

    def test_progress_is_explicit_private_resumable_and_resettable(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "study.sqlite3"
            with StudyPlanStore(path) as store:
                plan = store.start("Python", "escrever testes", level="intermediário", mode="quiz")
                self.assertEqual(store.get(plan.id), plan)
                self.assertEqual(plan.mode, "quiz")
                self.assertEqual(len(store.list()), 1)
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                self.assertTrue(store.complete(plan.id))
                self.assertEqual(store.get(plan.id).status, "COMPLETED")
                self.assertEqual(store.reset(), 1)
                self.assertEqual(store.list(), [])

    def test_source_selected_in_professor_mode_uses_exact_document_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "knowledge.sqlite3"
            with KnowledgeStore(db) as store:
                store.ingest_pages(
                    Path(tmp) / "manual.txt",
                    [(3, "Capacitores armazenam energia elétrica no campo eletrostático.")],
                    "a" * 64, "COMPUTING", title="Manual de circuitos.pdf",
                    license="USER_OWNED",
                )
            prompt = build_study_prompt(
                "capacitores", "explain", source="Manual de circuitos.pdf"
            )
            context = knowledge_context(prompt, {"cognition": {"knowledge_db": str(db)}})
            self.assertEqual(len(context.citations), 1)
            self.assertEqual(context.citations[0].title, "Manual de circuitos.pdf")
            self.assertEqual(context.citations[0].page, 3)
            self.assertIn("Capacitores armazenam energia", context.text)

    def test_desktop_professor_prepares_a_shared_conversation(self):
        from unittest.mock import patch
        from PyQt6.QtWidgets import QApplication
        from mark.desktop import MarkWindow

        app = QApplication.instance() or QApplication([])
        app.setQuitOnLastWindowClosed(False)
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(MarkWindow, "_load_history"), patch.object(MarkWindow, "refresh_status"):
                window = MarkWindow()
            window._study_store = lambda: StudyPlanStore(Path(tmp) / "study.sqlite3")
            try:
                window.status_timer.stop()
                window.history_timer.stop()
                window.professor_subject.setText("Circuitos")
                window.professor_objective.setText("calcular corrente")
                window.professor_source.setText("Manual.pdf")
                window.professor_mode.setCurrentIndex(window.professor_mode.findData("quiz"))
                window.prepare_study()
                app.processEvents()
                self.assertEqual(window.current_specialization, "professor")
                self.assertIn("uma pergunta por vez", window.input.toPlainText())
                self.assertIn('"Manual.pdf"', window.input.toPlainText())
                self.assertEqual(window.page_stack.currentWidget(), window.chat_page)
            finally:
                window.close()
                window.deleteLater()
                app.processEvents()
            with patch.object(MarkWindow, "_load_history"), patch.object(MarkWindow, "refresh_status"):
                resumed = MarkWindow()
            resumed._study_store = lambda: StudyPlanStore(Path(tmp) / "study.sqlite3")
            try:
                resumed.status_timer.stop()
                resumed.history_timer.stop()
                resumed.resume_study_plan()
                app.processEvents()
                self.assertEqual(resumed.professor_mode.currentData(), "quiz")
                self.assertEqual(resumed.professor_subject.text(), "Circuitos")
                self.assertIn("uma pergunta por vez", resumed.input.toPlainText())
            finally:
                resumed.close()
                resumed.deleteLater()
                app.processEvents()


if __name__ == "__main__":
    unittest.main()

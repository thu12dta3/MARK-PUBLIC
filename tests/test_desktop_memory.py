import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PyQt6.QtWidgets import QApplication, QInputDialog, QMessageBox
from mark.desktop_memory import MemoryDialog
from mark.core.context import CoreContext
from mark.memory import SharedMemory


class MemoryDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def test_dialog_lists_only_explicit_memories_and_filters_scope(self):
        with tempfile.TemporaryDirectory() as folder:
            memory = SharedMemory(path=Path(folder) / "conversation.sqlite3")
            memory.add("conversation secret", "chat response")
            memory.remember_fact("Project preference: short reports.", "project",
                                 CoreContext.create(project_id="lab"))
            dialog = MemoryDialog(memory=memory)
            try:
                self.assertEqual(dialog.table.rowCount(), 1)
                self.assertNotIn("conversation secret", repr(dialog.table.item(0, 2).text()))
                dialog.scope_filter.setCurrentIndex(dialog.scope_filter.findData("project"))
                self.app.processEvents()
                self.assertEqual(dialog.table.item(0, 0).text(), "Projeto")
                self.assertEqual(dialog.table.item(0, 1).text(), "lab")
                self.assertEqual(dialog.table.item(0, 2).text(), "Project preference: short reports.")
            finally:
                dialog.close()

    def test_edit_and_delete_act_on_selected_explicit_memory(self):
        with tempfile.TemporaryDirectory() as folder:
            memory = SharedMemory(path=Path(folder) / "conversation.sqlite3")
            memory.remember_fact("A memória original.")
            dialog = MemoryDialog(memory=memory)
            try:
                dialog.table.selectRow(0)
                with patch.object(QInputDialog, "getMultiLineText", return_value=("Memória corrigida.", True)):
                    dialog.edit_selected()
                self.assertEqual(memory.list_facts()[0]["content"], "Memória corrigida.")
                dialog.table.selectRow(0)
                with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
                    dialog.delete_selected()
                self.assertEqual(memory.list_facts(), [])
            finally:
                dialog.close()


if __name__ == "__main__":
    unittest.main()

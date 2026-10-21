import os
from pathlib import Path
import sys
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PyQt6.QtWidgets import QApplication, QMessageBox
from unittest.mock import patch
from mark.desktop_missions import MissionDialog
from mark.missions import MissionStore


class MissionDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def test_create_scope_add_step_and_execute_local_task(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "project"
            root.mkdir()
            store = MissionStore(Path(folder) / "missions.sqlite3")
            dialog = MissionDialog(store=store)
            try:
                dialog.objective.setText("Ler README do projeto")
                dialog.workspace.setText(str(root))
                dialog.operation_checks["workspace.write"].setChecked(True)
                dialog.create_mission()
                self.assertIsNotNone(dialog.active_mission)
                dialog.tool.setCurrentIndex(dialog.tool.findData("workspace.write"))
                dialog.arguments.setPlainText('{"relative":"readme.txt","content":"MARK"}')
                dialog.add_step()
                mission = store.list_missions()[0]
                self.assertEqual(mission["steps"][0]["tool"], "workspace.write")
                dialog.execute_selected()
                dialog.runner._future.result(timeout=5)
                self.assertEqual((root / "readme.txt").read_text(), "MARK")
                self.assertEqual(store.list_missions()[0]["state"], "COMPLETED")
            finally:
                dialog.close()

    def test_step_outside_scope_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "project"
            root.mkdir()
            dialog = MissionDialog(store=MissionStore(Path(folder) / "missions.sqlite3"))
            try:
                dialog.objective.setText("Ler código")
                dialog.workspace.setText(str(root))
                dialog.create_mission()
                dialog.tool.setCurrentIndex(dialog.tool.findData("workspace.write"))
                dialog.arguments.setPlainText('{"relative":"x","content":"y"}')
                with patch.object(QMessageBox, "warning"):
                    dialog.add_step()
                self.assertEqual(dialog.store.list_missions()[0]["steps"], [])
            finally:
                dialog.close()

    def test_local_dialog_assigns_workspace_to_conversation_draft(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "project"
            root.mkdir()
            store = MissionStore(Path(folder) / "missions.sqlite3")
            mission_id = store.create_conversation_draft("Revisar o projeto pelo General")
            dialog = MissionDialog(store=store)
            try:
                dialog.refresh()
                dialog.missions.selectRow(0)
                dialog.workspace.setText(str(root))
                dialog.reauthorize_selected()
                mission = store.list_missions()[0]
                self.assertEqual(mission["id"], mission_id)
                self.assertEqual(mission["workspace"], str(root.resolve()))
                self.assertEqual(mission["state"], "QUEUED")
                self.assertIn(mission_id, dialog.grants)
            finally:
                dialog.close()


if __name__ == "__main__":
    unittest.main()

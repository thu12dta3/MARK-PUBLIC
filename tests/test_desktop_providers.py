import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PyQt6.QtWidgets import QApplication, QLineEdit
from mark.desktop_providers import ProviderDialog


class ProviderDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_providers_start_disabled_and_keys_are_masked(self):
        default = {"enabled": False, "free_only_confirmed": False, "model": ""}
        with patch("mark.desktop_providers.provider_settings", return_value=default), \
             patch("mark.desktop_providers.key_source", return_value=None), \
             patch("mark.desktop_providers.list_models") as network:
            dialog = ProviderDialog()
            try:
                self.assertEqual(set(dialog._controls), {"gemini", "groq", "openrouter"})
                for control in dialog._controls.values():
                    self.assertFalse(control["active"].isChecked())
                    self.assertFalse(control["allow"].isChecked())
                    self.assertEqual(control["key"].echoMode(), QLineEdit.EchoMode.Password)
                network.assert_not_called()
            finally:
                dialog.close()


if __name__ == "__main__":
    unittest.main()

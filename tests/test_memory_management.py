import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mark.core.context import CoreContext
from mark.memory import SharedMemory


class ExplicitMemoryManagementTests(unittest.TestCase):
    def make_memory(self, folder):
        return SharedMemory(path=Path(folder) / "conversation.sqlite3")

    def test_search_edit_delete_respects_scope_and_never_lists_turns(self):
        with tempfile.TemporaryDirectory() as folder:
            memory = self.make_memory(folder)
            memory.add("turn privado", "resposta privada")
            memory.remember_fact("Preferência: idioma português.")
            project_ctx = CoreContext.create(project_id="project-a")
            memory.remember_fact("Decisão: usar testes unitários.", "project", project_ctx)
            rows = memory.list_facts("português")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["scope"], "global")
            self.assertNotIn("turn privado", repr(memory.list_facts()))
            self.assertTrue(memory.update_fact(rows[0]["id"], "Preferência: idioma brasileiro."))
            self.assertEqual(memory.list_facts("brasileiro")[0]["id"], rows[0]["id"])
            self.assertFalse(memory.list_facts("português"))
            self.assertTrue(memory.delete_fact(rows[0]["id"]))
            self.assertFalse(memory.delete_fact(rows[0]["id"]))
            self.assertEqual(memory.list_facts(scope="project", scope_id="project-a")[0]["scope_id"], "project-a")

    def test_backup_restore_is_integrity_checked_scoped_and_additive(self):
        with tempfile.TemporaryDirectory() as folder:
            original = self.make_memory(folder)
            original.add("histórico preservado", "resposta preservada")
            original.remember_fact("Perfil: prefiro respostas curtas.")
            original.remember_fact("Projeto: testes herméticos.", "project",
                                   CoreContext.create(project_id="mark-lab"))
            backup = original.facts_backup()

            destination = Path(folder) / "restored" / "conversation.sqlite3"
            destination.parent.mkdir(mode=0o700)
            restored = SharedMemory(path=destination)
            restored.add("histórico novo", "resposta nova")
            result = restored.restore_facts(backup)
            self.assertEqual(result, {"inserted": 2, "duplicates": 0})
            self.assertEqual(restored.restore_facts(backup), {"inserted": 0, "duplicates": 2})
            history = restored.history()
            self.assertEqual([row["user"] for row in history], ["histórico novo"])
            self.assertEqual(len(restored.list_facts()), 2)
            self.assertEqual(restored.list_facts(scope="project", scope_id="mark-lab")[0]["content"],
                             "Projeto: testes herméticos.")

    def test_tampered_backup_is_rejected_without_partial_import(self):
        with tempfile.TemporaryDirectory() as folder:
            source = self.make_memory(folder)
            source.remember_fact("Uma memória válida.")
            payload = json.loads(source.facts_backup())
            payload["entries"][0]["content"] = "conteúdo adulterado"
            target = self.make_memory(Path(folder) / "other")
            with self.assertRaisesRegex(ValueError, "integridade"):
                target.restore_facts(json.dumps(payload).encode())
            self.assertEqual(target.list_facts(), [])

    def test_restore_rejects_untrusted_scope_and_invalid_content_atomically(self):
        with tempfile.TemporaryDirectory() as folder:
            source = self.make_memory(folder)
            source.remember_fact("Memória confiável.")
            payload = json.loads(source.facts_backup())
            payload["entries"].append({"scope": "task", "scope_id": "../outside",
                                       "content": "escape", "provenance": "fixture"})
            unsigned = {key: value for key, value in payload.items() if key != "sha256"}
            from hashlib import sha256
            payload["sha256"] = sha256(json.dumps(unsigned, ensure_ascii=False, sort_keys=True,
                                                  separators=(",", ":")).encode()).hexdigest()
            target = self.make_memory(Path(folder) / "target")
            with self.assertRaisesRegex(ValueError, "Identificador"):
                target.restore_facts(json.dumps(payload).encode())
            self.assertEqual(target.list_facts(), [])

    def test_export_is_private_and_never_overwrites_existing_path(self):
        with tempfile.TemporaryDirectory() as folder:
            memory = self.make_memory(folder)
            memory.remember_fact("Exportável.")
            destination = Path(folder) / "backup.json"
            self.assertEqual(memory.export_facts(destination), 1)
            self.assertEqual(destination.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                memory.export_facts(destination)


if __name__ == "__main__":
    unittest.main()

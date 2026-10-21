import json
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path

from mark.backup_manager import (
    BackupIntegrityError,
    BackupMCPBridge,
    BackupManager,
)
from mark.cognition.knowledge import SQLiteKnowledgeStore
from mark.cognition.tools import ActionRequest, default_registry
from mark.memory import SharedMemory


class BackupManagerTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.state_dir = self.root / "state"
        self.state_dir.mkdir(mode=0o700)
        self.cognition_dir = self.state_dir / "cognition"
        self.cognition_dir.mkdir(mode=0o700)
        self.knowledge_db = self.cognition_dir / "knowledge.sqlite3"
        self.knowledge_files = self.cognition_dir / "files"
        self.knowledge_files.mkdir(mode=0o700)

        # Populate conversation.sqlite3
        self.memory = SharedMemory(path=self.state_dir / "conversation.sqlite3")
        self.memory.add("Olá MARK", "Olá! Como posso ajudar?", source="desktop-chat")
        self.memory.remember_fact("O servidor local usa porta 8123", scope="global")

        # Populate knowledge.sqlite3 (with FTS5)
        sample_doc = self.knowledge_files / "manual_linux.txt"
        sample_doc.write_text(
            "# Capítulo 1\nO kernel Linux gerencia processos, memória virtual e chamadas de sistema.",
            encoding="utf-8",
        )
        with SQLiteKnowledgeStore(self.knowledge_db) as store:
            store.ingest(
                sample_doc,
                "COMPUTING",
                title="Manual Técnico Linux",
                license="USER_OWNED",
            )

        # Secret token file inside state_dir must never be included in backups
        secret_token = self.state_dir / "home_assistant.token"
        secret_token.write_text("secret_token_should_not_be_in_backup\n", encoding="utf-8")
        secret_token.chmod(0o600)

        self.manager = BackupManager(
            state_dir=self.state_dir,
            knowledge_db=self.knowledge_db,
            knowledge_files_dir=self.knowledge_files,
        )

    def tearDown(self):
        self._tmp.cleanup()

    def test_create_verify_and_restore_roundtrip_offline(self):
        backup_zip = self.root / "backups" / "mark_backup.zip"
        created = self.manager.create_backup(backup_zip)
        self.assertTrue(created["verified"])
        self.assertGreaterEqual(created["entry_count"], 3)
        self.assertEqual(backup_zip.stat().st_mode & 0o777, 0o600)

        # Verify no .token files are present inside the archive
        with zipfile.ZipFile(backup_zip, "r") as zf:
            for name in zf.namelist():
                self.assertFalse(name.endswith(".token"), f"Token vazou no backup: {name}")

        # Restore into a brand new target state directory
        restore_state = self.root / "restored_state"
        restore_knowledge_db = restore_state / "cognition" / "knowledge.sqlite3"
        restore_files_dir = restore_state / "cognition" / "files"
        restore_mgr = BackupManager(
            state_dir=restore_state,
            knowledge_db=restore_knowledge_db,
            knowledge_files_dir=restore_files_dir,
        )
        result = restore_mgr.restore_backup(backup_zip)
        self.assertTrue(result["restored"])
        self.assertTrue((restore_state / "conversation.sqlite3").is_file())
        self.assertTrue(restore_knowledge_db.is_file())
        self.assertTrue((restore_files_dir / "manual_linux.txt").is_file())

        # Verify restored conversation memory and FTS5 search work offline
        restored_mem = SharedMemory(path=restore_state / "conversation.sqlite3")
        history = restored_mem.history()
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["assistant"], "Olá! Como posso ajudar?")

        with SQLiteKnowledgeStore(restore_knowledge_db, read_only=True) as store:
            hits = store.search("kernel Linux memoria")
            self.assertEqual(len(hits), 1)
            self.assertEqual(hits[0].citation.title, "Manual Técnico Linux")

    def test_tampered_sqlite_or_manifest_aborts_restore_without_overwriting(self):
        backup_zip = self.root / "backups" / "tampered.zip"
        self.manager.create_backup(backup_zip)

        # Tamper with databases/conversation.sqlite3 inside the zip
        tampered_zip = self.root / "backups" / "corrupted_payload.zip"
        with zipfile.ZipFile(backup_zip, "r") as zin, zipfile.ZipFile(tampered_zip, "w") as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)
                if item.filename == "databases/conversation.sqlite3":
                    data = b"CORRUPTED_SQLITE_BYTES_000000"
                zout.writestr(item, data)

        with self.assertRaises(BackupIntegrityError):
            self.manager.verify_backup(tampered_zip)

        # Ensure live conversation.sqlite3 is untouched when restore is rejected
        with self.assertRaises(BackupIntegrityError):
            self.manager.restore_backup(tampered_zip)
        self.assertEqual(len(self.memory.history()), 1)

    def test_zip_slip_path_traversal_is_blocked(self):
        malicious_zip = self.root / "malicious.zip"
        with zipfile.ZipFile(malicious_zip, "w") as zf:
            zf.writestr("manifest.json", json.dumps({"magic": "MARK_OFFLINE_BACKUP_V1"}))
            zf.writestr("../../evil.txt", "pwned")

        with self.assertRaises(BackupIntegrityError):
            self.manager.verify_backup(malicious_zip)

    def test_mcp_bridge_and_cognition_registry(self):
        backup_zip = self.root / "backups" / "bridge_test.zip"
        bridge = BackupMCPBridge(manager=self.manager)
        create_res = bridge.execute_tool(
            "backup_create_archive",
            {"destination_path": str(backup_zip), "include_knowledge_files": True},
        )
        self.assertEqual(create_res["status"], "ok")

        reg = default_registry(self.knowledge_db)
        verify_res = reg.invoke(
            ActionRequest("backup.verify_archive", {"backup_path": str(backup_zip)}),
            frozenset({"backup.verify"}),
        )
        self.assertEqual(verify_res["status"], "ok")
        self.assertTrue(verify_res["result"]["valid"])


if __name__ == "__main__":
    unittest.main()

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mark.missions import MissionStore
from mark.mission_intent import handle_general_mission_request, is_engineer_delegation, is_mission_status_question


class GeneralMissionIntentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = MissionStore(Path(self.temp.name) / "missions.sqlite3")

    def tearDown(self):
        self.temp.cleanup()

    def test_recognizes_explicit_delegation_and_status_only(self):
        self.assertTrue(is_engineer_delegation("MARK, peça ao Engineer para revisar o projeto Python."))
        self.assertTrue(is_mission_status_question("Como está a revisão que pedi ao Engineer?"))
        self.assertFalse(is_engineer_delegation("Explique o que um engenheiro de software faz."))
        self.assertFalse(is_mission_status_question("Qual o status da bateria?"))

    def test_voice_delegation_creates_inert_draft_and_deduplicates_retry(self):
        command = "MARK, peça ao Engineer para revisar o projeto Python desta pasta."
        response = handle_general_mission_request(command, self.store, "pc")
        repeated = handle_general_mission_request(command, self.store, "pc")
        mission = self.store.list_missions()
        self.assertIn("Nenhum arquivo foi lido ou alterado", response)
        self.assertEqual(repeated.split("rascunho da missão ", 1)[1][:8], response.split("rascunho da missão ", 1)[1][:8])
        self.assertEqual(len(mission), 1)
        self.assertEqual(mission[0]["state"], "WAITING_APPROVAL")
        self.assertIsNone(mission[0]["workspace"])
        with self.store._db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM mission_grants").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM mission_steps").fetchone()[0], 0)

    def test_remote_origin_cannot_create_mission_but_can_read_status(self):
        denied = handle_general_mission_request(
            "Mande o Engineer revisar meu projeto.", self.store, "cliente")
        self.assertIn("conversa autenticada", denied)
        self.assertEqual(self.store.list_missions(), [])

    def test_status_comes_from_persisted_state_and_never_exposes_workspace(self):
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            mission_id = self.store.create("Revisar projeto Python", workspace=root)
            message = handle_general_mission_request(
                "Como está a revisão que pedi?", self.store, "pc")
        self.assertIn(mission_id[:8], message)
        self.assertIn("aguardando autorização local", message)
        self.assertIn("nenhuma etapa registrada", message)
        self.assertNotIn(str(root), message)

    def test_workspace_must_be_chosen_locally_before_authorization(self):
        mission_id = self.store.create_conversation_draft("Revisar o projeto local")
        with tempfile.TemporaryDirectory() as workspace:
            canonical = self.store.set_workspace(mission_id, Path(workspace))
            grant = self.store.approve(mission_id, {"workspace.list", "workspace.read"})
        self.assertEqual(grant.workspace, canonical)
        self.assertEqual(self.store.list_missions()[0]["state"], "QUEUED")


if __name__ == "__main__":
    unittest.main()

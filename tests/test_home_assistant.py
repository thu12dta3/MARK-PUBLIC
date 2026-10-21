import io
import json
import os
import socket
import tempfile
import unittest
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from mark.cognition.tools import ActionRequest, default_registry
from mark.home_assistant import (
    HomeAssistantAuthError,
    HomeAssistantClient,
    HomeAssistantError,
    HomeAssistantMCPBridge,
    HomeAssistantSecurityError,
    load_ha_token,
    save_ha_token,
    scrub_ha_secrets,
    validate_ha_base_url,
)


class HomeAssistantTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.token_file = self.root / "home_assistant.token"
        save_ha_token("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.local_test_token_123456", self.token_file)

    def tearDown(self):
        self._tmp.cleanup()

    def _sample_states(self):
        return [
            {
                "entity_id": "sensor.temperatura_sala",
                "state": "23.4",
                "attributes": {
                    "friendly_name": "Temperatura da Sala",
                    "unit_of_measurement": "°C",
                    "device_class": "temperature",
                    "area": "sala",
                },
                "last_updated": "2026-10-18T14:00:00Z",
            },
            {
                "entity_id": "light.luz_quarto",
                "state": "on",
                "attributes": {
                    "friendly_name": "Luz Principal do Quarto",
                },
                "last_updated": "2026-10-18T14:10:00Z",
            },
            {
                "entity_id": "sensor.umidade_varanda",
                "state": "unavailable",
                "attributes": {
                    "friendly_name": "Sensor de Umidade da Varanda",
                    "unit_of_measurement": "%",
                },
                "last_updated": "2026-10-18T11:00:00Z",
            },
            {
                "entity_id": "sensor.consumo_cozinha",
                "state": "410",
                "attributes": {
                    "friendly_name": "Consumo Cozinha",
                    "unit_of_measurement": "W",
                },
                "last_updated": "2026-10-18T08:00:00Z",
            },
            {
                "entity_id": "script.abrir_portao",
                "state": "off",
                "attributes": {"friendly_name": "Script Secreto"},
                "last_updated": "2026-10-18T14:10:00Z",
            },
        ]

    def test_token_saved_with_0600_and_rejects_world_readable(self):
        mode = self.token_file.stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)
        token = load_ha_token(self.token_file)
        self.assertIn("local_test_token_123456", token)

        self.token_file.chmod(0o644)
        with self.assertRaises(HomeAssistantAuthError):
            load_ha_token(self.token_file)

    def test_url_validation_blocks_credentials_and_non_http(self):
        self.assertEqual(validate_ha_base_url("http://127.0.0.1:8123/"), "http://127.0.0.1:8123")
        with self.assertRaises(HomeAssistantSecurityError):
            validate_ha_base_url("http://admin:secret@127.0.0.1:8123")
        with self.assertRaises(HomeAssistantSecurityError):
            validate_ha_base_url("ftp://127.0.0.1:8123")

    def test_read_only_enforcement_blocks_service_calls_and_restricted_domains(self):
        client = HomeAssistantClient("http://127.0.0.1:8123", token_path=self.token_file)
        with self.assertRaises(HomeAssistantSecurityError):
            client.call_service("light", "turn_on", {"entity_id": "light.luz_quarto"})
        with self.assertRaises(HomeAssistantSecurityError):
            client._request_get("/api/states", method="POST")
        with self.assertRaises(HomeAssistantSecurityError):
            client._request_get("/api/services/light/turn_on")
        with self.assertRaises(HomeAssistantSecurityError):
            client.query_entities(domain="script")
        with self.assertRaises(HomeAssistantSecurityError):
            client.get_entity_state("automation.alarme_noturno")

    def test_query_entities_filters_by_room_and_domain_and_flags_unavailable_and_stale(self):
        client = HomeAssistantClient("http://127.0.0.1:8123", token_path=self.token_file, stale_seconds=3600)
        now = datetime(2026, 10, 18, 14, 15, 0, tzinfo=timezone.utc)

        def fake_urlopen(req, timeout=5):
            self.assertEqual(req.get_method(), "GET")
            return io.BytesIO(json.dumps(self._sample_states()).encode("utf-8"))

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            sala = client.query_entities(room="sala", now=now)
            self.assertEqual(sala["count"], 1)
            self.assertEqual(sala["entities"][0]["entity_id"], "sensor.temperatura_sala")
            self.assertIn("23.4 °C", sala["summary"])

            all_res = client.query_entities(now=now)
            # script.abrir_portao must be automatically filtered out
            ids = [e["entity_id"] for e in all_res["entities"]]
            self.assertNotIn("script.abrir_portao", ids)
            self.assertEqual(all_res["unavailable_count"], 1)
            self.assertEqual(all_res["stale_count"], 2)
            self.assertIn("INDISPONÍVEL", all_res["summary"])
            self.assertIn("DESATUALIZADO", all_res["summary"])

            only_avail = client.query_entities(include_unavailable=False, now=now)
            avail_ids = [e["entity_id"] for e in only_avail["entities"]]
            self.assertNotIn("sensor.umidade_varanda", avail_ids)

    def test_invalid_credentials_and_timeout_handling(self):
        client = HomeAssistantClient("http://127.0.0.1:8123", token_path=self.token_file, timeout=2.0)

        def fake_401(req, timeout=2):
            raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, None)

        with patch("urllib.request.urlopen", side_effect=fake_401):
            with self.assertRaises(HomeAssistantAuthError):
                client.query_entities(room="sala")

        def fake_timeout(req, timeout=2):
            raise urllib.error.URLError(socket.timeout("timed out"))

        with patch("urllib.request.urlopen", side_effect=fake_timeout):
            with self.assertRaises(TimeoutError):
                client.query_entities(room="sala")

    def test_token_never_leaks_in_error_messages(self):
        dirty = "Erro ao enviar Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.secret_value para o host"
        cleaned = scrub_ha_secrets(dirty)
        self.assertNotIn("secret_value", cleaned)
        self.assertIn("[REDACTED_HA_TOKEN]", cleaned)

    def test_mcp_bridge_and_cognition_registry_integration(self):
        client = HomeAssistantClient("http://127.0.0.1:8123", token_path=self.token_file)
        bridge = HomeAssistantMCPBridge(client=client)
        tools = {t["name"] for t in bridge.get_mcp_tools()}
        self.assertIn("ha_query_entities", tools)
        self.assertIn("ha_get_entity_state", tools)

        def fake_urlopen(req, timeout=5):
            return io.BytesIO(json.dumps(self._sample_states()[0]).encode("utf-8"))

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            res = bridge.execute_tool("ha_get_entity_state", {"entity_id": "sensor.temperatura_sala"})
            self.assertEqual(res["status"], "ok")
            self.assertEqual(res["result"]["entity"]["entity_id"], "sensor.temperatura_sala")

        reg = default_registry(self.root / "knowledge.sqlite3")
        contract_names = {c["name"] for c in reg.contracts()}
        self.assertIn("home_assistant.ha_query_entities", contract_names)
        self.assertIn("home_assistant.ha_get_entity_state", contract_names)


if __name__ == "__main__":
    unittest.main()

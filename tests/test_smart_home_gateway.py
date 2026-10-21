from __future__ import annotations

import base64
import json
import os
import ssl
import struct
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QFrame

from mark import desktop
from mark.cognition.tools import default_registry
from mark.desktop_theme import STYLESHEET, THEME_PALETTE, message_html
from mark.home_assistant import HomeAssistantClient, save_ha_token
from mark.smart_home_gateway import (
    EncryptedCredentialVault,
    MQTTSecureClient,
    SignedWebhookVerifier,
    SmartHomeAuthError,
    SmartHomeGateway,
    SmartHomeGatewayError,
    SmartHomeMCPBridge,
    SmartHomeSecurityError,
    _encode_mqtt_remaining_length,
    _encode_mqtt_utf8,
    scrub_gateway_secrets,
)


def _build_mqtt_publish_packet(topic: str, payload: bytes) -> bytes:
    topic_bytes = _encode_mqtt_utf8(topic)
    body = topic_bytes + payload
    return bytes([0x30]) + _encode_mqtt_remaining_length(len(body)) + body


class EncryptedVaultTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.vault_file = self.root / "vault.enc"
        self.key_file = self.root / "vault.key"

    def tearDown(self):
        self.tmp.cleanup()

    def test_vault_encrypts_and_decrypts_with_0600_permissions(self):
        vault = EncryptedCredentialVault(vault_path=self.vault_file, key_path=self.key_file)
        vault.store_credential("home_assistant", {"base_url": "http://127.0.0.1:8123", "token": "super-secret-ha-token-1234567890"})
        vault.store_credential("webhook_hmac", {"secret": "hmac-signing-key-abcdef1234567890"})

        self.assertTrue(self.vault_file.exists())
        self.assertTrue(self.key_file.exists())
        self.assertEqual(self.vault_file.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.key_file.stat().st_mode & 0o777, 0o600)

        raw_disk_bytes = self.vault_file.read_bytes()
        self.assertNotIn(b"super-secret-ha-token", raw_disk_bytes)
        self.assertNotIn(b"hmac-signing-key", raw_disk_bytes)

        reloaded = EncryptedCredentialVault(vault_path=self.vault_file, key_path=self.key_file)
        self.assertEqual(
            reloaded.get_credential("home_assistant")["token"],
            "super-secret-ha-token-1234567890",
        )
        self.assertEqual(
            reloaded.get_credential("webhook_hmac")["secret"],
            "hmac-signing-key-abcdef1234567890",
        )

    def test_vault_rejects_world_readable_permissions(self):
        vault = EncryptedCredentialVault(vault_path=self.vault_file, key_path=self.key_file)
        vault.store_credential("home_assistant", {"token": "secret-token-value"})
        os.chmod(self.vault_file, 0o644)
        with self.assertRaises(SmartHomeAuthError):
            vault.get_credential("home_assistant")

    def test_vault_detects_tampered_ciphertext(self):
        vault = EncryptedCredentialVault(vault_path=self.vault_file, key_path=self.key_file)
        vault.store_credential("home_assistant", {"token": "secret-token-value"})
        envelope = json.loads(self.vault_file.read_text(encoding="utf-8"))
        ct = bytearray(base64.urlsafe_b64decode(envelope["ciphertext"].encode("ascii")))
        ct[0] ^= 0xFF
        envelope["ciphertext"] = base64.urlsafe_b64encode(bytes(ct)).decode("ascii")
        self.vault_file.write_text(json.dumps(envelope), encoding="utf-8")
        os.chmod(self.vault_file, 0o600)
        with self.assertRaises(SmartHomeAuthError):
            vault.get_credential("home_assistant")

    def test_p0_vault_supports_aes256_gcm_and_chacha20_poly1305_and_key_rotation(self):
        # 1. Verifica envelope MARK_VAULT_V2 com AES-256-GCM e PBKDF2-HMAC-SHA256
        vault_aes = EncryptedCredentialVault(
            vault_path=self.vault_file,
            key_path=self.key_file,
            cipher_suite="AES-256-GCM",
            kdf_iterations=310_000,
        )
        vault_aes.store_credential("zigbee", {"token": "zigbee-secret-token-001"})
        env_aes = json.loads(self.vault_file.read_text(encoding="utf-8"))
        self.assertEqual(env_aes["magic"], "MARK_VAULT_V2")
        self.assertEqual(env_aes["cipher"], "AES-256-GCM")
        self.assertEqual(env_aes["kdf"], "PBKDF2-HMAC-SHA256")
        self.assertGreaterEqual(env_aes["iterations"], 100_000)

        # 2. Adulteração de metadados autenticados (AAD) como 'iterations' ou 'cipher' deve invalidar a tag AEAD
        tampered_aad = dict(env_aes)
        tampered_aad["iterations"] = env_aes["iterations"] + 1
        self.vault_file.write_text(json.dumps(tampered_aad), encoding="utf-8")
        os.chmod(self.vault_file, 0o600)
        with self.assertRaises(SmartHomeAuthError):
            vault_aes.get_credential("zigbee")

        # Restaura envelope íntegro e testa rotação de chave (AES-256-GCM -> ChaCha20-Poly1305)
        self.vault_file.write_text(json.dumps(env_aes), encoding="utf-8")
        os.chmod(self.vault_file, 0o600)
        old_key = self.key_file.read_bytes()
        rot_info = vault_aes.rotate_key(new_cipher_suite="ChaCha20-Poly1305")
        self.assertTrue(rot_info["rotated"])
        self.assertEqual(rot_info["cipher"], "ChaCha20-Poly1305")
        self.assertNotEqual(self.key_file.read_bytes(), old_key)
        self.assertEqual(self.key_file.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.vault_file.stat().st_mode & 0o777, 0o600)

        reloaded_chacha = EncryptedCredentialVault(vault_path=self.vault_file, key_path=self.key_file)
        self.assertEqual(reloaded_chacha.get_credential("zigbee")["token"], "zigbee-secret-token-001")


class SignedWebhookVerifierTests(unittest.TestCase):
    def test_valid_hmac_sha256_signature_and_replay_protection(self):
        verifier = SignedWebhookVerifier("webhook-shared-secret-key-2026", max_skew_seconds=120)
        payload = '{"device_id":"sensor.living_room_temp","state":"22.8","unit":"C","room":"sala"}'
        ts = int(time.time())
        headers = verifier.sign_payload(payload, timestamp=ts, nonce="nonce-001")

        parsed = verifier.verify_and_parse(payload, headers, now=ts + 2)
        self.assertEqual(parsed["device_id"], "sensor.living_room_temp")
        self.assertEqual(parsed["state"], "22.8")

        # Replaying the exact same nonce must fail
        with self.assertRaises(SmartHomeSecurityError):
            verifier.verify_and_parse(payload, headers, now=ts + 3)

    def test_rejects_expired_timestamp_and_tampered_body(self):
        verifier = SignedWebhookVerifier("webhook-shared-secret-key-2026", max_skew_seconds=60)
        payload = '{"device_id":"sensor.kitchen_humidity","state":"51"}'
        ts = 1_700_000_000
        headers = verifier.sign_payload(payload, timestamp=ts, nonce="nonce-002")

        with self.assertRaises(SmartHomeSecurityError):
            verifier.verify_and_parse(payload, headers, now=ts + 300)

        headers_fresh = verifier.sign_payload(payload, timestamp=ts, nonce="nonce-003")
        with self.assertRaises(SmartHomeSecurityError):
            verifier.verify_and_parse(
                '{"device_id":"sensor.kitchen_humidity","state":"99"}',
                headers_fresh,
                now=ts + 5,
            )


class MQTTSecureClientTests(unittest.TestCase):
    def test_enforces_tls_and_read_only_mode(self):
        with self.assertRaises(SmartHomeSecurityError):
            MQTTSecureClient("mqtt.local", port=1883, use_tls=False)

        client = MQTTSecureClient("mqtt.local", port=8883, use_tls=True)
        ctx = client.create_tls_context()
        self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)
        self.assertGreaterEqual(ctx.minimum_version, ssl.TLSVersion.TLSv1_2)

        with self.assertRaises(SmartHomeSecurityError):
            client.publish("zigbee2mqtt/living_room/set", '{"state":"ON"}')

        with self.assertRaises(SmartHomeSecurityError):
            client.validate_topic_filter("homeassistant/switch/boiler/set")

        with self.assertRaises(SmartHomeSecurityError):
            client.validate_topic_filter("cmnd/tasmota_kitchen/POWER")

        self.assertEqual(
            client.validate_topic_filter("zigbee2mqtt/living_room/sensor"),
            "zigbee2mqtt/living_room/sensor",
        )

    def test_reads_retained_telemetry_packet_over_tls_socket(self):
        connack = bytes([0x20, 0x02, 0x00, 0x00])
        suback = bytes([0x90, 0x03, 0x00, 0x01, 0x00])
        publish_pkt = _build_mqtt_publish_packet(
            "zigbee2mqtt/office/climate",
            b'{"temperature": 23.4, "humidity": 48}',
        )

        class FakeTLSSocket:
            def __init__(self):
                self._chunks = [connack, suback, publish_pkt]
                self.sent = bytearray()

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def sendall(self, data: bytes):
                self.sent.extend(data)

            def recv(self, _n: int) -> bytes:
                return self._chunks.pop(0) if self._chunks else b""

        class FakeRawSocket:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        class FakeSSLContext:
            def wrap_socket(self, _raw, server_hostname=None):
                return FakeTLSSocket()

        client = MQTTSecureClient(
            "iot.internal",
            port=8883,
            use_tls=True,
            username="mark_ro",
            password="mqtt_tls_password_99",
        )
        with patch("socket.create_connection", return_value=FakeRawSocket()), patch.object(
            client, "create_tls_context", return_value=FakeSSLContext()
        ):
            res = client.read_retained_topic("zigbee2mqtt/office/climate")

        self.assertEqual(res["platform"], "mqtt_tls")
        self.assertTrue(res["tls_verified"])
        self.assertTrue(res["read_only"])
        self.assertEqual(res["payload"]["temperature"], 23.4)
        self.assertEqual(res["payload"]["humidity"], 48)


class SmartHomeGatewayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.vault = EncryptedCredentialVault(
            vault_path=self.root / "vault.enc",
            key_path=self.root / "vault.key",
        )
        self.token_path = self.root / "ha.token"
        save_ha_token("ha-token-encrypted-xyz987654321", self.token_path)

        self.ha_states = [
            {
                "entity_id": "sensor.office_temperature",
                "state": "21.5",
                "last_updated": "2026-10-18T12:00:00+00:00",
                "attributes": {
                    "friendly_name": "Office Temperature",
                    "area_name": "office",
                    "unit_of_measurement": "C",
                },
            },
            {
                "entity_id": "light.office_desk",
                "state": "unavailable",
                "last_updated": "2026-10-18T12:00:00+00:00",
                "attributes": {
                    "friendly_name": "Office Desk Light",
                    "area_name": "office",
                },
            },
        ]
        self.ha_client = HomeAssistantClient(
            "http://127.0.0.1:8123",
            token_path=self.token_path,
        )
        self.ha_client._request_get = lambda endpoint, **kwargs: self.ha_states
        self.gateway = SmartHomeGateway(vault=self.vault, ha_client=self.ha_client, read_only=True)
        self.gateway.configure_webhook_secret("webhook-hmac-secret-xyz987654321")

    def tearDown(self):
        self.tmp.cleanup()

    def test_unified_gateway_queries_ha_and_webhooks(self):
        verifier = self.gateway.webhook_verifier
        assert verifier is not None
        body = json.dumps(
            {
                "action": "telemetry",
                "device_id": "sensor.office_co2",
                "name": "Office CO2",
                "domain": "sensor",
                "state": "420",
                "unit": "ppm",
                "room": "office",
            }
        )
        headers = verifier.sign_payload(body, nonce="wh-nonce-10")
        ingested = self.gateway.ingest_signed_webhook_event(body, headers)
        self.assertEqual(ingested.device_id, "sensor.office_co2")
        self.assertTrue(ingested.encrypted_transport)

        unified = self.gateway.query_unified_status(room="office")
        self.assertTrue(unified["read_only"])
        self.assertEqual(unified["count"], 3)
        self.assertIn("Office Temperature", unified["summary"])
        self.assertIn("Office CO2", unified["summary"])

    def test_gateway_blocks_actuation_and_scrubs_secrets(self):
        with self.assertRaises(SmartHomeSecurityError):
            SmartHomeGateway(vault=self.vault, read_only=False)

        with self.assertRaises(SmartHomeSecurityError):
            self.gateway.execute_command("turn_on", entity_id="light.office_desk")

        leaked = "Falha de conexao Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.secret na porta 8123"
        scrubbed = scrub_gateway_secrets(leaked)
        self.assertNotIn("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9", scrubbed)
        self.assertIn("[REDACTED_CREDENTIAL]", scrubbed)

    def test_mcp_bridge_and_cognition_registry_integration(self):
        bridge = SmartHomeMCPBridge(gateway=self.gateway)
        tools = {t["name"] for t in bridge.get_mcp_tools()}
        self.assertEqual(
            tools,
            {
                "smart_home_query_status",
                "smart_home_read_mqtt_tls",
            },
        )
        res = bridge.execute_tool("smart_home_query_status", {"room": "office"})
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["result"]["count"], 2)

        reg = default_registry(knowledge_path=self.root / "knowledge.sqlite3")
        contract_names = {c["name"] if isinstance(c, dict) else c.name for c in reg.contracts()}
        self.assertIn("smart_home.smart_home_query_status", contract_names)
        self.assertIn("smart_home.smart_home_read_mqtt_tls", contract_names)


class UIVisualReworkIntegrityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def test_qss_stylesheet_uses_modern_charcoal_and_cobalt_cyan_palette(self):
        for token_name, hex_value in THEME_PALETTE.items():
            self.assertRegex(hex_value, r"^#[0-9a-fA-F]{6}$", f"Invalid hex for {token_name}")
        self.assertIn(THEME_PALETTE["bg_canvas"], STYLESHEET)
        self.assertIn(THEME_PALETTE["bg_sidebar"], STYLESHEET)
        self.assertIn(THEME_PALETTE["accent_cobalt"], STYLESHEET)
        self.assertIn(THEME_PALETTE["accent_cyan"], STYLESHEET)
        self.assertIn(THEME_PALETTE["accent_cyan_bright"], STYLESHEET)

        self.assertIn("QPushButton:hover", STYLESHEET)
        self.assertIn("QPushButton:pressed", STYLESHEET)
        self.assertIn("QPushButton:disabled", STYLESHEET)
        self.assertIn("#navButtonActive", STYLESHEET)
        self.assertIn("#userBubble", STYLESHEET)
        self.assertIn("#assistantBubble", STYLESHEET)
        self.assertIn("#composerBox", STYLESHEET)
        self.assertEqual(STYLESHEET.count("{"), STYLESHEET.count("}"))

    def test_chat_bubbles_and_markdown_rendering_match_new_visual_identity(self):
        rendered = message_html(
            "## Painel\n\nUse `smart_home_query_status` em [MARK](https://example.com).\n\n```python\nprint('ok')\n```"
        )
        self.assertIn("#4fd1c5", rendered)
        self.assertIn("#63b3ed", rendered)
        self.assertIn("#0d131f", rendered)

        with patch.object(desktop.MarkWindow, "_load_history"), patch.object(desktop.MarkWindow, "refresh_status"):
            window = desktop.MarkWindow()
        window.status_timer.stop()
        window.history_timer.stop()
        try:
            window._append_bubble("Solicitar leitura de sensores MQTT", "user")
            window._append_bubble("Sensores lidos em modo somente leitura.", "assistant")
            user_b = window.findChild(QFrame, "userBubble")
            asst_b = window.findChild(QFrame, "assistantBubble")
            self.assertIsNotNone(user_b)
            self.assertIsNotNone(asst_b)
            self.assertIn("#2D333B", user_b.styleSheet())
            self.assertIn("border: none", user_b.styleSheet())
            self.assertIn("border-radius: 8px", user_b.styleSheet())
            self.assertIn("#1A1D21", asst_b.styleSheet())
            self.assertIn("border-radius: 0px", asst_b.styleSheet())
            self.assertNotIn("transparent", user_b.styleSheet())
            self.assertNotIn("transparent", asst_b.styleSheet())
        finally:
            window.close()
            window.deleteLater()
            self.app.processEvents()


if __name__ == "__main__":
    unittest.main()

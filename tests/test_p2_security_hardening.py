"""Suíte de Testes da Fase P2 — Criptografia de Backups, Blindagem de Importação,
Endurecimento do Home Assistant / MQTT / Webhooks, Higienização de Logs,
Permissões Locais POSIX e Recuperação Pós-Roubo de Dispositivo.

Cobre todos os 7 passos operacionais exigidos na Fase P2 do projeto MARK.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import hmac
import io
import json
import logging
import os
import secrets
import shutil
import ssl
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mark.backup_manager import (
    BACKUP_MAGIC_V2,
    DEFAULT_PBKDF2_ITERATIONS,
    MAX_ARCHIVE_MEMBERS,
    MAX_COMPRESSION_RATIO,
    MAX_MEMBER_BYTES,
    MAX_TOTAL_UNCOMPRESSED_BYTES,
    SUPPORTED_BACKUP_CIPHERS,
    BackupError,
    BackupIntegrityError,
    BackupManager,
    _aead_decrypt,
    _aead_encrypt,
    _derive_backup_key,
    _validate_archive_relpath,
)
from mark.cognition.storage import (
    StorageSecurityError,
    atomic_create_temp_file,
    ensure_private_dir,
    private_file,
    read_json,
    verify_private_path,
    write_private_json,
)
from mark.device_auth import (
    DEFAULT_SESSION_LIFETIME,
    DeviceRegistry,
    SessionRegistry,
    b64,
    unb64,
)
from mark.home_assistant import HomeAssistantClient, HomeAssistantSecurityError
from mark.logging_sanitizer import (
    LogSanitizerFilter,
    sanitize_diagnostic_report,
    sanitize_log_text,
    sanitize_stack_trace,
)
from mark.smart_home_gateway import (
    MQTTSecureClient,
    SignedWebhookVerifier,
    SmartHomeSecurityError,
)


class TestP2SecurityHardening(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_path = Path(self.temp_dir.name)
        self.state_dir = self.base_path / "state"
        self.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.backups_dir = self.base_path / "backups"
        self.backups_dir.mkdir(mode=0o700, parents=True, exist_ok=True)

        # Inicializa base de dados mock para backup
        self.conversation_db = self.state_dir / "conversation.sqlite3"
        import sqlite3
        conn = sqlite3.connect(str(self.conversation_db))
        conn.execute("CREATE TABLE test (id INT, val TEXT)")
        conn.execute("INSERT INTO test VALUES (1, 'initial')")
        conn.commit()
        conn.close()
        self.conversation_db.chmod(0o600)

        self.backup_mgr = BackupManager(
            state_dir=self.state_dir,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    # =========================================================================
    # PASSO 1: Criptografia Autenticada e Proteção de Backups
    # =========================================================================

    def test_p2_step1_aead_roundtrip_aes_gcm(self):
        """Passo 1.1: Valida cifragem e decifragem autenticada AEAD com AES-256-GCM."""
        plaintext = b"MARK sensitive cognitive payload - AES-256-GCM test"
        salt = secrets.token_bytes(32)
        nonce = secrets.token_bytes(12)
        aad = b"archive_test.sqlite3:conversation_db:abc:100"
        derived_key = _derive_backup_key(b"master-secret-password-1234", salt, iterations=DEFAULT_PBKDF2_ITERATIONS)
        self.assertEqual(len(derived_key), 32)

        ciphertext = _aead_encrypt("AES-256-GCM", derived_key, nonce, plaintext, aad)
        self.assertNotEqual(ciphertext, plaintext)

        decrypted = _aead_decrypt("AES-256-GCM", derived_key, nonce, ciphertext, aad)
        self.assertEqual(decrypted, plaintext)

    def test_p2_step1_aead_roundtrip_chacha20(self):
        """Passo 1.2: Valida cifragem e decifragem autenticada AEAD com ChaCha20-Poly1305."""
        plaintext = b"MARK sensitive state data - ChaCha20-Poly1305 test"
        salt = secrets.token_bytes(32)
        nonce = secrets.token_bytes(12)
        aad = b"knowledge.sqlite3:knowledge_db:def:200"
        derived_key = _derive_backup_key(b"master-secret-password-5678", salt, iterations=DEFAULT_PBKDF2_ITERATIONS)

        ciphertext = _aead_encrypt("ChaCha20-Poly1305", derived_key, nonce, plaintext, aad)
        self.assertNotEqual(ciphertext, plaintext)

        decrypted = _aead_decrypt("ChaCha20-Poly1305", derived_key, nonce, ciphertext, aad)
        self.assertEqual(decrypted, plaintext)

    def test_p2_step1_aead_tamper_detection(self):
        """Passo 1.3: Adulteração na tag MAC ou no texto cifrado resulta em falha de autenticação AEAD."""
        plaintext = b"Untampered SQLite database bytes"
        salt = secrets.token_bytes(32)
        nonce = secrets.token_bytes(12)
        aad = b"db_entry:category:sha:size"
        derived_key = _derive_backup_key(b"safe-passphrase", salt)
        ciphertext = _aead_encrypt("AES-256-GCM", derived_key, nonce, plaintext, aad)

        # Adultera um byte do ciphertext
        corrupted_ct = bytearray(ciphertext)
        corrupted_ct[0] ^= 0xFF

        with self.assertRaises(BackupIntegrityError):
            _aead_decrypt("AES-256-GCM", derived_key, nonce, bytes(corrupted_ct), aad)

        # Adultera um byte do AAD (Additional Authenticated Data)
        bad_aad = b"db_entry:category:sha:tampered_size"
        with self.assertRaises(BackupIntegrityError):
            _aead_decrypt("AES-256-GCM", derived_key, nonce, ciphertext, bad_aad)

    def test_p2_step1_wrong_key_rejection(self):
        """Passo 1.4: Chave incorreta falha na autenticação AEAD disparando BackupIntegrityError."""
        plaintext = b"Secret data"
        salt = secrets.token_bytes(32)
        nonce = secrets.token_bytes(12)
        aad = b"aad"
        key1 = _derive_backup_key(b"correct-password", salt)
        key2 = _derive_backup_key(b"wrong-password", salt)

        ciphertext = _aead_encrypt("AES-256-GCM", key1, nonce, plaintext, aad)

        with self.assertRaises(BackupIntegrityError):
            _aead_decrypt("AES-256-GCM", key2, nonce, ciphertext, aad)

    def test_p2_step1_master_key_file_isolation_and_posix_mode(self):
        """Passo 1.5: Arquivo de chave mestra fica isolado fora do zip com permissão POSIX 0600."""
        archive_dest = self.backups_dir / "backup_test.zip"
        res = self.backup_mgr.create_backup(archive_dest)
        self.assertTrue(archive_dest.exists())
        self.assertTrue(res["verified"])

        # Verifica se backup_master.key foi criado no state_dir
        master_key_file = self.state_dir / "backup_master.key"
        self.assertTrue(master_key_file.exists())
        self.assertEqual(oct(master_key_file.stat().st_mode & 0o777), oct(0o600))

        # Assegura que backup_master.key NÃO está dentro do zip gerado
        with zipfile.ZipFile(archive_dest, "r") as zf:
            namelist = zf.namelist()
            self.assertNotIn("backup_master.key", namelist)
            self.assertIn("manifest.json", namelist)

    # =========================================================================
    # PASSO 2: Endurecimento de Importação e Restauração
    # =========================================================================

    def test_p2_step2_zip_slip_rejection(self):
        """Passo 2.1: Prevenção estrita contra Zip-Slip (traversal de diretórios no zip)."""
        malicious_zip = self.backups_dir / "zip_slip.zip"
        with zipfile.ZipFile(malicious_zip, "w") as zf:
            zf.writestr("../../etc/cron.d/evil_job", b"malicious payload")
            zf.writestr("manifest.json", json.dumps({"version": 2}))

        with self.assertRaises(BackupIntegrityError):
            self.backup_mgr.restore_backup(malicious_zip)

    def test_p2_step2_zip_bomb_large_member_rejection(self):
        """Passo 2.2: Rejeição de membros que excedem o tamanho individual máximo (anti-Zip Bomb)."""
        bomb_zip = self.backups_dir / "zip_bomb_large_member.zip"
        with zipfile.ZipFile(bomb_zip, "w") as zf:
            zi = zipfile.ZipInfo("huge_file.bin")
            zi.file_size = MAX_MEMBER_BYTES + 1024
            zf.writestr(zi, b"fake small bytes")
            zf.writestr("manifest.json", json.dumps({"version": 2}))

        with self.assertRaises(BackupIntegrityError):
            self.backup_mgr.restore_backup(bomb_zip)

    def test_p2_step2_zip_bomb_ratio_rejection(self):
        """Passo 2.3: Rejeição de arquivos com taxa de compressão excessiva (Zip Bomb)."""
        ratio_zip = self.backups_dir / "zip_bomb_ratio.zip"
        with zipfile.ZipFile(ratio_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zi = zipfile.ZipInfo("ratio_bomb.bin")
            zi.file_size = 50 * 1024 * 1024  # 50MB
            zi.compress_size = 1000  # 1KB
            zf.writestr(zi, b"compressed")
            zf.writestr("manifest.json", json.dumps({"version": 2}))

        with self.assertRaises(BackupIntegrityError):
            self.backup_mgr.restore_backup(ratio_zip)

    def test_p2_step2_symlink_in_archive_rejection(self):
        """Passo 2.4: Rejeição estrita de links simbólicos dentro do pacote comprimido."""
        symlink_zip = self.backups_dir / "symlink_entry.zip"
        with zipfile.ZipFile(symlink_zip, "w") as zf:
            zi = zipfile.ZipInfo("symlink_pointer")
            # 0o120000 indica link simbólico no padrão POSIX
            zi.external_attr = 0o120777 << 16
            zf.writestr(zi, b"/etc/shadow")
            zf.writestr("manifest.json", json.dumps({"version": 2}))

        with self.assertRaises(BackupIntegrityError):
            self.backup_mgr.restore_backup(symlink_zip)

    def test_p2_step2_atomic_rollback_on_restore_failure(self):
        """Passo 2.5: Falha na validação pós-descompressão reverte atomicamente os arquivos com permissão 0600."""
        # 1. Garante que o arquivo original existe
        self.assertTrue(self.conversation_db.exists())
        original_bytes = self.conversation_db.read_bytes()

        # 2. Cria arquivo zip que simula falha (por exemplo, arquivo não declarado no manifesto)
        bad_zip = self.backups_dir / "corrupted_archive.zip"
        with zipfile.ZipFile(bad_zip, "w") as zf:
            manifest_dict = {
                "magic": BACKUP_MAGIC_V2,
                "format_version": 2,
                "schema_version": 1,
                "created_at": "2026-10-18T12:00:00Z",
                "cipher": "AES-256-GCM",
                "kdf": "PBKDF2-HMAC-SHA256",
                "iterations": DEFAULT_PBKDF2_ITERATIONS,
                "salt": secrets.token_hex(16),
                "base_nonce": secrets.token_hex(12),
                "entries": [],
            }
            # Entry não declarada gerará BackupIntegrityError
            zf.writestr("manifest.json", json.dumps(manifest_dict))
            zf.writestr("undeclared.sqlite3", b"rogue data")

        # 3. Tenta restaurar — deve falhar
        with self.assertRaises(BackupError):
            self.backup_mgr.restore_backup(bad_zip)

        # 4. Verifica que a base original não foi adulterada e mantém permissão 0600
        self.assertTrue(self.conversation_db.exists())
        self.assertEqual(self.conversation_db.read_bytes(), original_bytes)
        self.assertEqual(oct(self.conversation_db.stat().st_mode & 0o777), oct(0o600))

    # =========================================================================
    # PASSO 3: Blindagem do Conector Home Assistant
    # =========================================================================

    def test_p2_step3_home_assistant_ssrf_protection(self):
        """Passo 3.1: Rejeição de URLs suspeitas para mitigação de SSRF (cloud metadata, loopback, etc)."""
        blocked_urls = [
            "https://169.254.169.254/latest/meta-data/",
            "https://metadata.google.internal/computeMetadata/v1/",
            "https://255.255.255.255:8123",
            "https://224.0.0.1:8123",
            "https://0.0.0.0:8123",
        ]

        from mark.home_assistant import validate_ha_base_url

        for url in blocked_urls:
            with self.assertRaises(HomeAssistantSecurityError):
                validate_ha_base_url(url)

    def test_p2_step3_home_assistant_tls_enforcement(self):
        """Passo 3.2: Exigência obrigatória de TLS (HTTPS) para hosts remotos ou require_tls=True."""
        from mark.home_assistant import validate_ha_base_url

        # Host remoto com HTTP inseguro deve falhar
        with self.assertRaises(HomeAssistantSecurityError):
            validate_ha_base_url("http://my-ha.remote-domain.com:8123")

        # require_tls explícito com HTTP deve falhar
        with self.assertRaises(HomeAssistantSecurityError):
            validate_ha_base_url("http://127.0.0.1:8123", require_tls=True)

    def test_p2_step3_home_assistant_token_scrubbing(self):
        """Passo 3.3: Higienização de tokens em exceções de conexão ou formatação."""
        from mark.home_assistant import scrub_ha_secrets

        token = "secret-super-long-token-9988776655"
        raw_msg = f"Request failed for https://homeassistant.local:8123/api?token={token}&key=val with Bearer {token}"
        scrubbed = scrub_ha_secrets(raw_msg)
        self.assertNotIn(token, scrubbed)
        self.assertIn("[REDACTED_HA_TOKEN]", scrubbed)

    def test_p2_step3_home_assistant_read_only_restriction(self):
        """Passo 3.4: Bloqueio estrito de endpoints mutáveis (/api/services/*, /api/events/*)."""
        client = HomeAssistantClient(base_url="https://homeassistant.local:8123")

        with self.assertRaises(HomeAssistantSecurityError):
            client.call_service("light", "turn_on", {"entity_id": "light.sala"})

        with self.assertRaises(HomeAssistantSecurityError):
            client._request_get("/api/states", method="POST")

        with self.assertRaises(HomeAssistantSecurityError):
            client._request_get("/api/services/light/turn_on")

        with self.assertRaises(HomeAssistantSecurityError):
            client.query_entities(domain="script")

        with self.assertRaises(HomeAssistantSecurityError):
            client.get_entity_state("automation.alarme_noturno")

    # =========================================================================
    # PASSO 4: MQTT over TLS e Webhooks Seguros
    # =========================================================================

    def test_p2_step4_mqtt_tls_untrusted_cert_rejection(self):
        """Passo 4.1: Rejeição de conexões MQTT com autoridade certificadora não confiável."""
        client = MQTTSecureClient(host="127.0.0.1", port=8883, use_tls=True)

        with patch("socket.create_connection") as mock_conn, \
             patch("ssl.SSLContext.wrap_socket") as mock_wrap:
            mock_wrap.side_effect = ssl.SSLCertVerificationError("certificate verify failed")

            with self.assertRaises(SmartHomeSecurityError) as ctx:
                client.read_retained_topic("home/sensor/temp")
            self.assertIn("Certificado SSL/TLS do broker MQTT rejeitado", str(ctx.exception))

    def test_p2_step4_webhook_hmac_and_replay_protection(self):
        """Passo 4.2: Webhook HMAC-SHA256 com verificação atômica de nonce via SQLite e anti-replay."""
        secret = "super-secret-shared-key-12345"
        sqlite_path = self.state_dir / "webhook_nonces.sqlite3"
        verifier = SignedWebhookVerifier(
            secret=secret,
            db_path=sqlite_path,
            max_skew_seconds=60,
            rate_limit_rpm=30,
        )

        payload = '{"event":"motion_detected","zone":"front_door"}'
        headers = verifier.sign_payload(payload)

        # 1. Primeira verificação deve passar com sucesso
        parsed = verifier.verify_and_parse(payload, headers)
        self.assertEqual(parsed["event"], "motion_detected")

        # 2. Replay com o mesmo nonce deve ser categoricamente rejeitado
        with self.assertRaises(SmartHomeSecurityError) as ctx:
            verifier.verify_and_parse(payload, headers)
        self.assertIn("nonce duplicado detectado", str(ctx.exception))

    def test_p2_step4_webhook_clock_skew_rejection(self):
        """Passo 4.3: Webhook com timestamp além da janela aceitável de clock skew (<= 60s) é rejeitado."""
        secret = "webhook-key-for-skew-test"
        sqlite_path = self.state_dir / "webhook_skew.sqlite3"
        verifier = SignedWebhookVerifier(secret=secret, db_path=sqlite_path, max_skew_seconds=60)

        payload = '{"action":"door_unlocked"}'
        old_timestamp = int(time.time()) - 120  # 2 minutos no passado
        headers = verifier.sign_payload(payload, timestamp=old_timestamp, nonce="nonce_too_old")

        with self.assertRaises(SmartHomeSecurityError) as ctx:
            verifier.verify_and_parse(payload, headers)
        self.assertIn("timestamp fora da janela anti-replay permitida", str(ctx.exception))

    def test_p2_step4_webhook_rate_limiting(self):
        """Passo 4.4: Aplicação estrita de rate limiting em Webhooks."""
        secret = "rate-limit-secret-1234"
        sqlite_path = self.state_dir / "webhook_rate.sqlite3"
        verifier = SignedWebhookVerifier(
            secret=secret,
            db_path=sqlite_path,
            rate_limit_rpm=3,
        )

        payload = '{"ping":true}'
        now = time.time()

        # 3 requisições permitidas
        for i in range(3):
            headers = verifier.sign_payload(payload, timestamp=int(now), nonce=f"nonce_rate_{i}_{secrets.token_hex(4)}")
            res = verifier.verify_and_parse(payload, headers, now=now)
            self.assertTrue(res["ping"])

        # 4ª requisição deve estourar o limite de taxa
        headers = verifier.sign_payload(payload, timestamp=int(now), nonce="nonce_rate_overflow")
        with self.assertRaises(SmartHomeSecurityError) as ctx:
            verifier.verify_and_parse(payload, headers, now=now)
        self.assertIn("limite de frequência excedido", str(ctx.exception))

    # =========================================================================
    # PASSO 5: Higienização e Purga de Segredos em Logs
    # =========================================================================

    def test_p2_step5_sanitize_log_text_secrets(self):
        """Passo 5.1: Purga tokens Bearer, JWTs, chaves privadas PEM e credenciais em URLs."""
        raw_log = (
            "Connecting with Bearer ya29.a0AfH6SMDh_fake_token_123456789 to service. "
            "Auth URL: https://admin:SuperSecretPass123@internal.cluster.local/api?token=secret123&api_key=privkey99. "
            "Session JWT: eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozG4. "
            "Key: -----BEGIN EC PRIVATE KEY-----\nMHcCAQEEI...\n-----END EC PRIVATE KEY-----"
        )

        sanitized = sanitize_log_text(raw_log)

        self.assertNotIn("ya29.a0AfH6SMDh_fake_token_123456789", sanitized)
        self.assertNotIn("SuperSecretPass123", sanitized)
        self.assertNotIn("secret123", sanitized)
        self.assertNotIn("privkey99", sanitized)
        self.assertNotIn("-----BEGIN EC PRIVATE KEY-----", sanitized)

        self.assertIn("Bearer [REDACTED_TOKEN]", sanitized)
        self.assertIn("[REDACTED_PASSWORD]", sanitized)
        self.assertIn("[REDACTED_JWT]", sanitized)
        self.assertIn("[REDACTED_PRIVATE_KEY]", sanitized)

    def test_p2_step5_sanitize_stack_trace(self):
        """Passo 5.2: Higieniza mensagens e variáveis em exceções e stack traces."""
        try:
            raise ValueError("Falha na autenticação com chave privada: -----BEGIN PRIVATE KEY-----\nSecretBytes\n-----END PRIVATE KEY-----")
        except ValueError as exc:
            trace_sanitized = sanitize_stack_trace(exc)

        self.assertNotIn("SecretBytes", trace_sanitized)
        self.assertIn("[REDACTED_PRIVATE_KEY]", trace_sanitized)

    def test_p2_step5_sanitize_diagnostic_report(self):
        """Passo 5.3: Sanitiza recursivamente dicionários e estruturas de diagnóstico."""
        report = {
            "service": "home_assistant",
            "status": "online",
            "credentials": {
                "api_key": "raw_sensitive_key_abcdef",
                "access_token": "token_1234567",
                "nested": {
                    "master_key": "master_secret_bytes",
                    "safe_info": "ok_value",
                },
            },
            "recent_errors": [
                "Error connecting to https://user:secretpass@host.local/api",
            ],
        }

        clean = sanitize_diagnostic_report(report)

        self.assertEqual(clean["credentials"]["api_key"], "[REDACTED]")
        self.assertEqual(clean["credentials"]["access_token"], "[REDACTED]")
        self.assertEqual(clean["credentials"]["nested"]["master_key"], "[REDACTED]")
        self.assertEqual(clean["credentials"]["nested"]["safe_info"], "ok_value")
        self.assertIn("[REDACTED_PASSWORD]", clean["recent_errors"][0])

    def test_p2_step5_logging_filter_integration(self):
        """Passo 5.4: Integração do LogSanitizerFilter com o logger padrão do Python."""
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.addFilter(LogSanitizerFilter())
        handler.setFormatter(logging.Formatter("%(levelname)s:%(message)s"))

        logger = logging.getLogger("mark.test_security_sanitizer")
        logger.setLevel(logging.DEBUG)
        logger.addHandler(handler)

        logger.info("Request with Bearer sec_tok_9988776655 completed.")
        output = stream.getvalue()

        self.assertNotIn("sec_tok_9988776655", output)
        self.assertIn("Bearer [REDACTED_TOKEN]", output)

    # =========================================================================
    # PASSO 6: Permissões POSIX e Fortalecimento de Armazenamento Local
    # =========================================================================

    def test_p2_step6_posix_directory_and_file_permissions(self):
        """Passo 6.1: Assegura permissões restritas 0700 para diretórios e 0600 para arquivos."""
        secure_dir = self.base_path / "secure_dir"
        ensure_private_dir(secure_dir)

        self.assertTrue(secure_dir.is_dir())
        self.assertEqual(oct(secure_dir.stat().st_mode & 0o777), oct(0o700))

        # Grava arquivo privado
        json_file = secure_dir / "confidential.json"
        write_private_json(json_file, {"user": "admin", "role": "owner"})

        self.assertTrue(json_file.is_file())
        self.assertEqual(oct(json_file.stat().st_mode & 0o777), oct(0o600))

        # Lê arquivo privado e confirma integridade
        data = read_json(json_file, require_private=True)
        self.assertEqual(data["user"], "admin")

    def test_p2_step6_symlink_rejection(self):
        """Passo 6.2: Rejeição estrita de symlinks em diretórios de armazenamento seguro."""
        target_file = self.base_path / "actual_target.txt"
        target_file.write_text("legitimate data")
        target_file.chmod(0o600)

        symlink_path = self.base_path / "malicious_symlink.txt"
        symlink_path.symlink_to(target_file)

        with self.assertRaises(StorageSecurityError) as ctx:
            verify_private_path(symlink_path, is_dir=False)
        self.assertIn("Symlinks são proibidos", str(ctx.exception))

    def test_p2_step6_atomic_temp_file_creation_with_o_excl(self):
        """Passo 6.3: Criação atômica de arquivos temporários com flags O_CREAT | O_EXCL e modo 0600."""
        temp_file = atomic_create_temp_file(dir_path=self.base_path, prefix="test_atomic_")

        self.assertTrue(temp_file.exists())
        self.assertEqual(oct(temp_file.stat().st_mode & 0o777), oct(0o600))
        verify_private_path(temp_file, is_dir=False)

    # =========================================================================
    # PASSO 7: Procedimento de Recuperação Pós-Perda ou Roubo de Dispositivo
    # =========================================================================

    def test_p2_step7_device_revocation_and_anti_reactivation(self):
        """Passo 7.1: Revogação imediata de dispositivo furtado, expurgo de sessões e bloqueio permanente de reativação."""
        db_path = self.state_dir / "devices_recovery.db"
        registry = DeviceRegistry(db_path)
        sessions = SessionRegistry(registry, lifetime=DEFAULT_SESSION_LIFETIME)

        # 1. Gera par de chaves EC P-256 para o celular original
        stolen_priv = ec.generate_private_key(ec.SECP256R1())
        stolen_der = stolen_priv.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        stolen_pub_b64 = b64(stolen_der)

        code1 = registry.open_enrollment(validity_seconds=180)
        proof1 = b64(stolen_priv.sign(b"MARK-PAIR-V1\n" + code1.encode(), ec.ECDSA(hashes.SHA256())))

        stolen_device_id = registry.enroll(code1, "Celular Antigo", stolen_pub_b64, proof1)
        registry.update_device(stolen_device_id, "authorized")

        # Emite sessão válida
        token, expires = sessions.issue(stolen_device_id)
        self.assertTrue(sessions.valid(stolen_device_id, token))

        # 2. Notificação de furto/roubo: Executa revogação permanente em tempo real
        revoked = registry.revoke_device(stolen_device_id, permanent=True)
        self.assertTrue(revoked)
        self.assertEqual(registry.status(stolen_device_id), "revoked")

        # 3. Sessão deve estar instantaneamente expirada/inválida
        self.assertFalse(sessions.valid(stolen_device_id, token))

        # 4. Tentativa de re-enrollment com a chave roubada DEVE FALHAR categoricamente
        code2 = registry.open_enrollment(validity_seconds=180)
        proof2 = b64(stolen_priv.sign(b"MARK-PAIR-V1\n" + code2.encode(), ec.ECDSA(hashes.SHA256())))

        with self.assertRaises(ValueError) as ctx:
            registry.enroll(code2, "Celular Reativado Falsamente", stolen_pub_b64, proof2)
        self.assertIn("chave e dispositivo revogados permanentemente", str(ctx.exception))

        # 5. Tentativa de atualizar status diretamente para 'authorized' DEVE FALHAR
        changed = registry.update_device(stolen_device_id, "authorized")
        self.assertFalse(changed)
        self.assertEqual(registry.status(stolen_device_id), "revoked")

        registry.close()

    def test_p2_step7_full_post_theft_recovery_flow(self):
        """Passo 7.2: Fluxo completo de recuperação pós-furto com novo dispositivo e restauração de dados."""
        db_path = self.state_dir / "devices_theft_flow.db"
        registry = DeviceRegistry(db_path)

        # Dispositivo 1: Roubado
        stolen_priv = ec.generate_private_key(ec.SECP256R1())
        stolen_der = stolen_priv.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        code1 = registry.open_enrollment(180)
        proof1 = b64(stolen_priv.sign(b"MARK-PAIR-V1\n" + code1.encode(), ec.ECDSA(hashes.SHA256())))
        stolen_device_id = registry.enroll(code1, "Dispositivo Roubado", b64(stolen_der), proof1)
        registry.update_device(stolen_device_id, "authorized")

        # Dispositivo 2: Novo dispositivo legítimo
        new_priv = ec.generate_private_key(ec.SECP256R1())
        new_der = new_priv.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        code2 = registry.open_enrollment(180)
        proof2 = b64(new_priv.sign(b"MARK-PAIR-V1\n" + code2.encode(), ec.ECDSA(hashes.SHA256())))

        # Executa recuperação atômica pós-roubo
        new_device_id = registry.recover_after_device_theft(
            lost_device_id=stolen_device_id,
            new_name="Dispositivo Novo Substituto",
            new_public_der_b64=b64(new_der),
            new_proof_b64=proof2,
            enrollment_code=code2,
            auto_authorize=True,
        )

        # Validações:
        # 1. Antigo está permanentemente revogado
        self.assertEqual(registry.status(stolen_device_id), "revoked")
        # 2. Novo está devidamente autorizado
        self.assertEqual(registry.status(new_device_id), "authorized")
        self.assertNotEqual(stolen_device_id, new_device_id)

        # 3. Cria backup autenticado com AEAD
        backup_dest = self.backups_dir / "recovery_backup.zip"
        res = self.backup_mgr.create_backup(backup_dest, passphrase="secure-theft-recovery-pass")
        self.assertTrue(backup_dest.exists())
        self.assertTrue(res["verified"])

        # 4. Modifica a base para simular perda de dados
        import sqlite3
        conn = sqlite3.connect(str(self.conversation_db))
        conn.execute("UPDATE test SET val='tampered' WHERE id=1")
        conn.commit()
        conn.close()

        # 5. Restauração de memória persistente a partir do backup autenticado
        restored_res = self.backup_mgr.restore_backup(
            backup_dest,
            passphrase="secure-theft-recovery-pass",
        )
        self.assertTrue(restored_res["restored"])

        # Confirma que o dado original 'initial' foi restaurado
        conn = sqlite3.connect(str(self.conversation_db))
        row = conn.execute("SELECT val FROM test WHERE id=1").fetchone()
        conn.close()
        self.assertEqual(row[0], "initial")

        registry.close()


if __name__ == "__main__":
    unittest.main()

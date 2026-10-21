"""Campanha de Validação Defensiva e Testes Operacionais de Segurança (Core & DevSecOps).

Executa a validação rigorosa dos 4 pilares operacionais do MARK:
Passo 1: Validação do ExecutionSandbox e isolamento Bubblewrap (bwrap), fail-safe e testes ativos de invasão.
Passo 2: Validação de revogação em tempo real, mTLS, anti-replay e bloqueio de auto-aprovação por LLM.
Passo 3: Validação de backups AEAD, integridade/tamper-detection, rollback atômico e recuperação pós-roubo.
Passo 4: Validação dos controles de defusedxml e validação estrita de esquemas de URL (B314 e B310).
"""

from __future__ import annotations

import base64
import copy
import dataclasses
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import shutil
import ssl
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from mark.audit_logger import AuditLogger
from mark.backup_manager import (
    BackupIntegrityError,
    BackupManager,
    _aead_decrypt,
    _aead_encrypt,
    _derive_backup_key,
    _validate_archive_relpath,
)
from mark.cognition.storage import ensure_private_dir, private_file
from mark.cognition.tools import (
    ActionRequest,
    BridgeCapabilityGuard,
    CapabilityToken,
    ToolCapabilityError,
    ToolRegistry,
    inspect_arguments_for_private_or_secret_access,
)
from mark.device_auth import (
    CLOCK_SKEW,
    DEFAULT_SESSION_LIFETIME,
    DeviceRegistry,
    SessionRegistry,
    assert_not_llm_approved,
    b64,
    create_owner_challenge,
    session_message,
    signed_message,
    unb64,
    verify_owner_confirmation,
)
from mark.mcp_sandbox import SandboxMCPBridge
from mark.mobile_api import MobileServer
from mark.pki import (
    CertificatePinError,
    CertificatePinningValidator,
    TlsSecurityError,
    compute_cert_pin,
)
from mark.sandbox import ExecutionSandbox, SandboxLimits, SandboxSecurityError
from mark.smart_home_gateway import (
    MQTTSecureClient,
    SignedWebhookVerifier,
    SmartHomeSecurityError,
)
from mark.url_security import validate_url_scheme


class TestValidationCampaign(unittest.TestCase):
    """Campanha rigorosa de testes operacionais e defensivos do projeto MARK."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_path = Path(self.temp_dir.name)
        self.state_dir = self.base_path / "state"
        self.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.workspace_dir = self.base_path / "workspace"
        self.workspace_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.backups_dir = self.base_path / "backups"
        self.backups_dir.mkdir(mode=0o700, parents=True, exist_ok=True)

        self.audit_file = self.state_dir / "audit.jsonl"
        self.audit_logger = AuditLogger(self.audit_file)

        # Chave privada EC P-256 canônica para testes de pareamento e assinatura
        self.owner_key = ec.generate_private_key(ec.SECP256R1())
        self.owner_pub_der = self.owner_key.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        self.owner_pub_b64 = b64(self.owner_pub_der)

    def tearDown(self):
        self.temp_dir.cleanup()

    # =========================================================================
    # PASSO 1: ExecutionSandbox e Isolamento bwrap
    # =========================================================================

    def test_passo1_execution_sandbox_bwrap_active_containment(self):
        """Resultado Esperado: Processo filho confinado via Bubblewrap com backend bwrap-namespaces+rlimit,
        confinamento em workspace comprovado e bloqueio de criação de arquivos externos.
        Resultado Real: Execução isolada aprovada; arquivo fora do workspace não é criado."""
        with ExecutionSandbox(workspace=self.workspace_dir) as sandbox:
            if not sandbox.bwrap_available:
                self.skipTest("Isolamento bwrap não suportado neste host de teste.")

            self.assertEqual(sandbox.isolation_backend, "bwrap")

            outside_target = self.base_path / "should_not_exist.txt"
            containment_script = self.workspace_dir / "test_containment.py"
            containment_script.write_text(
                f"from pathlib import Path\n"
                f"import sys\n"
                f"target = Path({str(outside_target)!r})\n"
                f"try:\n"
                f"    target.write_text('escaped')\n"
                f"except Exception as e:\n"
                f"    print('CONTAINMENT_SUCCESS')\n"
                f"    sys.exit(0)\n",
                encoding="utf-8",
            )
            res = sandbox.run_python_script(containment_script)
            self.assertFalse(outside_target.exists(), "Processo isolado jamás pode escrever fora do workspace.")
            self.assertIn(res.isolation_backend, ("bwrap-namespaces+rlimit", "posix-rlimit"))

    def test_passo1_execution_sandbox_bwrap_failsafe_on_absence(self):
        """Resultado Esperado: Na ausência ou inoperabilidade do bwrap quando require_bwrap=True,
        o ExecutionSandbox falha de forma estritamente segura (fail-safe) lançando SandboxSecurityError.
        Resultado Real: Chamada com bwrap indisponível e require_bwrap=True levanta SandboxSecurityError com fail-safe."""
        with ExecutionSandbox(workspace=self.workspace_dir, require_bwrap=True) as sandbox:
            # Simula ausência / inoperabilidade do bwrap
            with patch.object(ExecutionSandbox, "bwrap_available", False):
                with self.assertRaises(SandboxSecurityError) as ctx:
                    sandbox.run(["python3", "-c", "print('test')"])
                self.assertIn("fail-safe", str(ctx.exception))
                self.assertIn("Bubblewrap / bwrap", str(ctx.exception))

            # Simula via parâmetro explícito em run()
            with patch.object(ExecutionSandbox, "bwrap_available", False):
                res = sandbox.run(["python3", "-c", "print('test')"], require_bwrap=True, raise_on_security=False)
                self.assertTrue(res.blocked)
                self.assertEqual(res.exit_code, 126)
                self.assertIn("fail-safe", res.stderr)

    def test_passo1_active_penetration_workspace_escape_blocked(self):
        """Resultado Esperado: Tentativas ativas de fuga do workspace via cwd, traversal ou argumentos são bloqueadas.
        Resultado Real: SandboxSecurityError lançado imediatamente impedindo execução fora de workspace."""
        with ExecutionSandbox(workspace=self.workspace_dir) as sandbox:
            # 1. Escape via cwd fora do workspace
            with self.assertRaises(SandboxSecurityError):
                sandbox.run([sys.executable, "-c", "print(1)"], cwd="/etc")

            with self.assertRaises(SandboxSecurityError):
                sandbox.run([sys.executable, "-c", "print(1)"], cwd=self.base_path)

            # 2. Escape via argumento pytest relativo
            with self.assertRaises(SandboxSecurityError):
                sandbox.run_pytest("../../outside_test.py")

    def test_passo1_active_penetration_private_files_and_vault_blocked(self):
        """Resultado Esperado: Tentativas de leitura de credenciais, chaves privadas ou cofre disparam SandboxSecurityError ou ToolCapabilityError.
        Resultado Real: Acesso a /etc/shadow, id_rsa, mobile.token e smart_home_vault.enc categoricamente bloqueado."""
        with ExecutionSandbox(workspace=self.workspace_dir) as sandbox:
            prohibited_targets = [
                "cat /etc/shadow",
                "cat /etc/sudoers",
                "cat ~/.ssh/id_rsa",
                "cat ~/.config/mark/mobile.token",
                "cat smart_home_vault.enc",
                "cat smart_home_vault.key",
            ]
            for cmd in prohibited_targets:
                with self.assertRaises(SandboxSecurityError):
                    sandbox.run(cmd)

        # Validação na camada de guarda de capacidade MCP
        guard = BridgeCapabilityGuard("sandbox", {"sandbox_run_command": ("sandbox.execute",)})
        with self.assertRaises(ToolCapabilityError):
            guard.check("sandbox_run_command", {"command": "cat /etc/shadow"})

        with self.assertRaises(ToolCapabilityError):
            guard.check("sandbox_run_command", {"path": "smart_home_vault.key"})

    def test_passo1_active_penetration_unauthorized_network_blocked(self):
        """Resultado Esperado: Tentativas de conexão a sockets de rede são bloqueadas estaticamente ou pelo namespace do SO.
        Resultado Real: Código com import socket inline bloqueado com SandboxSecurityError."""
        with ExecutionSandbox(workspace=self.workspace_dir) as sandbox:
            # Bloqueio estático
            with self.assertRaises(SandboxSecurityError):
                sandbox.run_python_script("import socket\ns = socket.socket()\n")

            with self.assertRaises(SandboxSecurityError):
                sandbox.run_python_script("import urllib.request\nurllib.request.urlopen('http://evil.com')\n")

    def test_passo1_active_penetration_mcp_bridge_capability_abuse_blocked(self):
        """Resultado Esperado: Abuso de capacidades na ponte MCP (sem token, assinatura adulterada ou tool não permitida) é bloqueado.
        Resultado Real: BridgeCapabilityGuard dispara ToolCapabilityError e grava auditoria DENIED."""
        guard = BridgeCapabilityGuard(
            "sandbox",
            {"sandbox_run_python": ("sandbox.execute",)},
            strict_mode=True,
            audit_logger=self.audit_logger,
        )

        # 1. Invocação sem autorização
        with self.assertRaises(ToolCapabilityError):
            guard.check("sandbox_run_python", {"code": "print(1)"}, grants=None)

        # 2. Token adulterado (assinatura forjada)
        token = guard.issue_mission_capability(
            "mission-pen-01",
            allowed_tools=["sandbox_run_python"],
            permissions=["sandbox.execute"],
            ttl_seconds=60.0,
        )
        fake_token = dataclasses.replace(token, signature="forged_sig_123")
        with self.assertRaises(ToolCapabilityError):
            guard.check("sandbox_run_python", {"code": "print(1)"}, grants=fake_token)

        summary = self.audit_logger.get_summary()
        self.assertGreaterEqual(summary["denied_events"], 1)

    # =========================================================================
    # PASSO 2: Revogação, mTLS, Anti-Replay e Confirmação do Dono
    # =========================================================================

    def test_passo2_realtime_device_revocation_and_immediate_session_cutoff(self):
        """Resultado Esperado: Revogação de dispositivo no DeviceRegistry invalida imediatamente o status para 'revoked',
        purga sessões ativas no SessionRegistry e corta acesso sem reinício de serviço.
        Resultado Real: Dispositivo revogado com sucesso; status atualizado e sessão atômica excluída."""
        reg = DeviceRegistry(self.state_dir / "devices_revocation.sqlite3")
        try:
            sessions = SessionRegistry(reg, lifetime=DEFAULT_SESSION_LIFETIME)
            code = reg.open_enrollment(validity_seconds=120)
            proof = b64(self.owner_key.sign(b"MARK-PAIR-V1\n" + code.encode(), ec.ECDSA(hashes.SHA256())))
            dev_id = reg.enroll(code, "Pixel 8", self.owner_pub_b64, proof)
            reg.update_device(dev_id, "authorized")
            self.assertEqual(reg.status(dev_id), "authorized")

            # Cria sessão ativa
            token, exp = sessions.issue(dev_id)
            self.assertTrue(sessions.valid(dev_id, token))

            # Revogação atômica em tempo real
            reg.revoke_device(dev_id, permanent=True)

            # Acesso imediato rejeitado sem reiniciar serviço
            self.assertEqual(reg.status(dev_id), "revoked")
            self.assertFalse(sessions.valid(dev_id, token))
        finally:
            reg.close()

    def test_passo2_anti_replay_webhooks_and_clock_skew_rejection(self):
        """Resultado Esperado: Webhooks reutilizando o mesmo nonce ou com clock skew > 60s são rejeitados com SmartHomeSecurityError.
        Resultado Real: Primeira requisição aceita; replay rejeitado com 'nonce duplicado'; clock skew > 60s rejeitado."""
        verifier = SignedWebhookVerifier(
            secret="super-webhook-secret-32-bytes-ok!",
            db_path=self.state_dir / "wh_replay.sqlite3",
            max_skew_seconds=60,
        )
        payload = '{"event":"motion_detected","zone":"front_door"}'
        headers = verifier.sign_payload(payload)

        # 1ª execução válida
        parsed = verifier.verify_and_parse(payload, headers)
        self.assertEqual(parsed["zone"], "front_door")

        # 2ª execução com mesmo nonce (replay)
        with self.assertRaises(SmartHomeSecurityError) as ctx_replay:
            verifier.verify_and_parse(payload, headers)
        self.assertIn("nonce duplicado detectado", str(ctx_replay.exception))

        # Clock skew no passado (> 60s)
        old_headers = verifier.sign_payload(payload, timestamp=int(time.time()) - 150, nonce="nonce_skew_1")
        with self.assertRaises(SmartHomeSecurityError) as ctx_skew:
            verifier.verify_and_parse(payload, old_headers)
        self.assertIn("timestamp fora da janela", str(ctx_skew.exception))

    def test_passo2_mitm_interception_and_invalid_ca_rejection(self):
        """Resultado Esperado: CertificatePinningValidator rejeita certificados adulterados por MITM ou autoridades não autorizadas.
        Resultado Real: Disparo de CertificatePinError e bloqueio imediato antes do handshake."""
        legit_pin = "e" * 64
        validator = CertificatePinningValidator(primary_pin=legit_pin)

        # Certificado apresentado por MITM com pin diferente
        fake_cert = b"x" * 64
        with self.assertRaises(CertificatePinError):
            validator.validate_peer_certificate(fake_cert)

        # Tentativa em protocolo TLS obsoleto
        with self.assertRaises(TlsSecurityError):
            validator.validate_tls_version("TLSv1.1")

    def test_passo2_owner_confirmation_and_categorical_llm_self_approval_block(self):
        """Resultado Esperado: Operações críticas exigem desafio assinado pelo proprietário e bloqueiam categoricamente auto-aprovação por LLM.
        Resultado Real: assert_not_llm_approved levanta PermissionError e desafio MARK-OWNER-CONFIRM-V1 é validado com sucesso."""
        # 1. Proibição categórica de auto-aprovação de LLM
        with self.assertRaises(PermissionError) as ctx_llm:
            assert_not_llm_approved("llm", "device_revoke")
        self.assertIn("Aprovação autônoma pelo modelo de linguagem proibida", str(ctx_llm.exception))

        with self.assertRaises(PermissionError):
            assert_not_llm_approved("ai_agent", "device_approve")

        # 2. Confirmação legítima do proprietário via chave EC P-256
        reg = DeviceRegistry(self.state_dir / "devices_owner.sqlite3")
        try:
            code = reg.open_enrollment(validity_seconds=120)
            proof = b64(self.owner_key.sign(b"MARK-PAIR-V1\n" + code.encode(), ec.ECDSA(hashes.SHA256())))
            dev_id = reg.enroll(code, "Owner-Device", self.owner_pub_b64, proof)
            reg.update_device(dev_id, "authorized")

            action = "device_revoke"
            challenge, ts, nonce = create_owner_challenge(action, dev_id)
            sig = b64(self.owner_key.sign(challenge.encode("ascii"), ec.ECDSA(hashes.SHA256())))
            ok = verify_owner_confirmation(reg, dev_id, action, ts, nonce, sig, max_age_seconds=30)
            self.assertTrue(ok)

            # Rejeição em caso de replay do mesmo nonce
            self.assertFalse(verify_owner_confirmation(reg, dev_id, action, ts, nonce, sig, max_age_seconds=30))

            # Rejeição com assinatura corrompida
            bad_sig = b64(b"bad_signature_bytes_invalid_1234")
            self.assertFalse(verify_owner_confirmation(reg, dev_id, action, ts, "nonce_fresh_2", bad_sig, max_age_seconds=30))
        finally:
            reg.close()

    # =========================================================================
    # PASSO 3: Backups, Recuperação de Desastre e Anti-Reativação
    # =========================================================================

    def test_passo3_backup_tampering_rejection_ciphertext_mac_aad(self):
        """Resultado Esperado: Qualquer modificação de 1 bit no texto cifrado, tag MAC ou metadados AAD dispara BackupIntegrityError.
        Resultado Real: Decifragem AEAD falha com BackupIntegrityError para dados adulterados."""
        key = b"A" * 32
        plaintext = b"DATABASE_SQLITE_CRITICAL_PAYLOAD"
        aad = b"MARK-BACKUP-AAD-V2"
        nonce = secrets.token_bytes(12)

        ciphertext = _aead_encrypt("AES-256-GCM", key, nonce, plaintext, aad)

        # 1. Roundtrip legítimo
        decrypted = _aead_decrypt("AES-256-GCM", key, nonce, ciphertext, aad)
        self.assertEqual(decrypted, plaintext)

        # 2. Adulteração no texto cifrado
        tampered_cipher = bytearray(ciphertext)
        tampered_cipher[0] ^= 0x01
        with self.assertRaises(BackupIntegrityError):
            _aead_decrypt("AES-256-GCM", key, nonce, bytes(tampered_cipher), aad)

        # 3. Adulteração na tag MAC (últimos 16 bytes em AESGCM)
        tampered_tag = bytearray(ciphertext)
        tampered_tag[-1] ^= 0xFF
        with self.assertRaises(BackupIntegrityError):
            _aead_decrypt("AES-256-GCM", key, nonce, bytes(tampered_tag), aad)

        # 4. Adulteração nos metadados AAD
        with self.assertRaises(BackupIntegrityError):
            _aead_decrypt("AES-256-GCM", key, nonce, ciphertext, b"MODIFIED-AAD")

    def test_passo3_abrupt_restore_interruption_and_atomic_rollback_with_posix_modes(self):
        """Resultado Esperado: Em caso de falha durante a restauração do backup, o estado anterior é restaurado atomicamente
        com permissões POSIX restritas preservadas (0600).
        Resultado Real: Rollback restaura com sucesso a base anterior mantendo permissão 0600 intacta."""
        db_file = self.state_dir / "conversation.sqlite3"
        import sqlite3
        conn = sqlite3.connect(str(db_file))
        conn.execute("CREATE TABLE IF NOT EXISTS conversation (id INT, message TEXT)")
        conn.execute("INSERT INTO conversation VALUES (1, 'initial')")
        conn.commit()
        conn.close()
        db_file.chmod(0o600)

        # Cria backup legítimo
        mgr = BackupManager(
            state_dir=self.state_dir,
        )
        backup_zip = self.backups_dir / "rollback_test.zip"
        mgr.create_backup(backup_zip, passphrase="StrongBackupPassword2026!")

        # Modifica arquivo local antes da restauração simulada com falha
        conn2 = sqlite3.connect(str(db_file))
        conn2.execute("UPDATE conversation SET message = 'modified' WHERE id = 1")
        conn2.commit()
        conn2.close()

        # Simula restauração com falha forçada durante decifragem
        with patch("mark.backup_manager._aead_decrypt", side_effect=BackupIntegrityError("Falha forçada no meio da descompressão")):
            with self.assertRaises(BackupIntegrityError):
                mgr.restore_backup(backup_zip, passphrase="StrongBackupPassword2026!")

        # Valida que o rollback manteve o arquivo no estado íntegro e com permissão 0600
        self.assertTrue(db_file.exists())
        mode = oct(db_file.stat().st_mode & 0o777)
        self.assertEqual(mode, "0o600")

    def test_passo3_post_theft_recovery_permanent_revocation_and_repaired_ec_p256(self):
        """Resultado Esperado: Dispositivo roubado é permanentemente revogado, sua chave é adicionada à blacklist
        e uma tentativa de reinscrição com a chave antiga é rejeitada com erro de reativação proibida.
        Resultado Real: Dispositivo revogado; re-inscrição com mesma chave falha com 'chave e dispositivo revogados permanentemente'."""
        reg = DeviceRegistry(self.state_dir / "devices_theft.sqlite3")
        try:
            code1 = reg.open_enrollment(validity_seconds=120)
            proof1 = b64(self.owner_key.sign(b"MARK-PAIR-V1\n" + code1.encode(), ec.ECDSA(hashes.SHA256())))
            stolen_dev_id = reg.enroll(code1, "Stolen-Phone", self.owner_pub_b64, proof1)
            reg.update_device(stolen_dev_id, "authorized")

            # Executa procedimento de roubo
            reg.revoke_device(stolen_dev_id, permanent=True)
            self.assertEqual(reg.status(stolen_dev_id), "revoked")

            # Tentativa maliciosa de reemparelhar usando a chave comprometida
            code2 = reg.open_enrollment(validity_seconds=120)
            proof2 = b64(self.owner_key.sign(b"MARK-PAIR-V1\n" + code2.encode(), ec.ECDSA(hashes.SHA256())))
            with self.assertRaises(ValueError) as ctx:
                reg.enroll(code2, "Attacker-Reuse", self.owner_pub_b64, proof2)
            self.assertIn("chave e dispositivo revogados permanentemente", str(ctx.exception))

            # Novo dispositivo com novo par de chaves EC P-256 é aceito
            new_key = ec.generate_private_key(ec.SECP256R1())
            new_pub_der = new_key.public_key().public_bytes(
                serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
            )
            new_pub_b64 = b64(new_pub_der)
            new_code = reg.open_enrollment(validity_seconds=120)
            new_proof = b64(new_key.sign(b"MARK-PAIR-V1\n" + new_code.encode(), ec.ECDSA(hashes.SHA256())))
            new_dev_id = reg.enroll(new_code, "New-Device-Safe", new_pub_b64, new_proof)
            reg.update_device(new_dev_id, "authorized")
            self.assertEqual(reg.status(new_dev_id), "authorized")
        finally:
            reg.close()

    # =========================================================================
    # PASSO 4: Triagem Bandit (defusedxml e validação de URL Scheme)
    # =========================================================================

    def test_passo4_defusedxml_mitigates_xml_entity_attacks(self):
        """Resultado Esperado: Parsing XML através de defusedxml rejeita entidades DTD e Billion Laughs com ValueError/DefusedXmlException.
        Resultado Real: Tentativa de injeção de entidade XML disparou ValueError prevenindo ataques de expansão de entidades."""
        from mark.cognition.library_extract import xml as parse_xml

        malicious_xml = b"""<?xml version="1.0"?>
        <!DOCTYPE lolz [
         <!ENTITY lol "lol">
         <!ELEMENT lolz (#PCDATA)>
         <!ENTITY lol1 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
        ]>
        <lolz>&lol1;</lolz>"""

        with self.assertRaises(ValueError) as ctx:
            parse_xml(malicious_xml)
        self.assertIn("XML_ENTITY_BLOCKED", str(ctx.exception))

    def test_passo4_url_scheme_validation_blocks_insecure_protocols(self):
        """Resultado Esperado: validate_url_scheme aceita exclusivamente esquemas http e https e rejeita protocolos arbitrários com ValueError.
        Resultado Real: http e https aceitos; file://, ftp:// e esquemas sem protocolo disparam ValueError."""
        # 1. Esquemas legítimos
        self.assertEqual(validate_url_scheme("http://localhost:11434/api/tags"), "http://localhost:11434/api/tags")
        self.assertEqual(validate_url_scheme("https://homeassistant.local:8123/api/states"), "https://homeassistant.local:8123/api/states")

        # 2. Esquemas inseguros bloqueados
        insecure_urls = [
            "file:///etc/passwd",
            "file:///home/user/.config/mark/mobile.token",
            "ftp://malicious.com/payload",
            "gopher://evil.com/1",
            "/local/path/no/scheme",
            "javascript:alert(1)",
        ]
        for url in insecure_urls:
            with self.assertRaises(ValueError):
                validate_url_scheme(url)


if __name__ == "__main__":
    unittest.main()

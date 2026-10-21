"""Suíte de Testes da Fase P1 — Segurança de Dispositivos E2E, PKI/mTLS, Revogação em Tempo Real e Integridade Mobile.

Cobre os 6 passos fundamentais exigidos pelo projeto MARK:
Passo 1: Pareamento E2E via QR Code de uso único, prova ECDSA P-256 e proteção anti-replay.
Passo 2: Certificate Pinning estrito, TLS 1.3, mitigação de MITM e rotação segura de certificados.
Passo 3: Revogação instantânea de dispositivos e expurgo de sessões/streams em tempo real sem reinício.
Passo 4: Sessões curtas, renovação autenticada, confirmação do proprietário e bloqueio absoluto de auto-aprovação por LLM.
Passo 5: Auditoria de interfaces de escuta (127.0.0.1), ACLs, Tailscale Serve e bloqueio de bypass por loopback.
Passo 6: Integridade do pipeline mobile, validação de APK Signature Scheme v2, detecção de adulteração e política Android Keystore.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import ssl
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import Mock, patch

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mark.config import load_config
from mark.device_auth import (
    CLOCK_SKEW,
    CRITICAL_ACTIONS,
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
from mark.memory import ShortMemory
from mark.mobile_api import MobileServer
from mark.mobile_integrity import (
    APK_SIG_BLOCK_MAGIC,
    MARK_OWNER_SIGNER_SHA256,
    MobileIntegrityError,
    compute_sha256,
    simulate_apk_tampering,
    simulate_third_party_resigning,
    verify_android_keystore_policy,
    verify_apk_artifact,
)
from mark.network_audit import (
    audit_listening_interfaces,
    audit_tailscale_serve_configuration,
    verify_loopback_access_gate,
)
from mark.pki import (
    CertificatePinError,
    CertificatePinningValidator,
    TlsSecurityError,
    compute_cert_pin,
)


class TestP1DeviceSecurity(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "devices_p1.db"
        self.registry = DeviceRegistry(self.db_path)
        self.sessions = SessionRegistry(self.registry, lifetime=DEFAULT_SESSION_LIFETIME)

        # Gera par de chaves ECDSA P-256 do dispositivo legítimo
        self.device_private_key = ec.generate_private_key(ec.SECP256R1())
        self.device_public_der = self.device_private_key.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        self.device_public_b64 = b64(self.device_public_der)
        self.expected_device_id = b64(hashlib.sha256(self.device_public_der).digest())

    def tearDown(self):
        self.registry.close()
        self.temp_dir.cleanup()

    # =========================================================================
    # PASSO 1: Pareamento E2E, QR Code de Uso Único e Prova Criptográfica
    # =========================================================================

    def test_step1_qr_pairing_single_use_and_expiration(self):
        """Passo 1.1: Gera QR Code de uso único com janela estrita de expiração e rejeita reuso."""
        server_url = "https://mark-pc.tailnet.ts.net:443"
        cert_pin = "a" * 64
        validity = 120

        # Gera payload do QR
        qr_payload = self.registry.generate_pairing_qr_payload(server_url, cert_pin, validity_seconds=validity)
        self.assertEqual(qr_payload["version"], "MARK-PAIR-V1")
        self.assertEqual(qr_payload["url"], "https://mark-pc.tailnet.ts.net:443")
        self.assertEqual(qr_payload["pin"], cert_pin)
        self.assertGreater(qr_payload["expires_at"], int(time.time()))

        code = qr_payload["code"]

        # Prova legítima de posse ECDSA P-256
        proof = b64(self.device_private_key.sign(b"MARK-PAIR-V1\n" + code.encode(), ec.ECDSA(hashes.SHA256())))

        # Primeiro uso: bem-sucedido e vincula device_id imutável à chave pública
        enrolled_id = self.registry.enroll(code, "Galaxy-Device", self.device_public_b64, proof)
        self.assertEqual(enrolled_id, self.expected_device_id)
        self.assertEqual(self.registry.status(enrolled_id), "pending")

        # Segundo uso com o mesmo código (tentativa de replay do QR): estritamente bloqueado
        with self.assertRaises(ValueError) as ctx:
            self.registry.enroll(code, "Attacker-Device", self.device_public_b64, proof)
        self.assertIn("pareamento expirado ou já usado", str(ctx.exception))

    def test_step1_expired_qr_code_is_rejected(self):
        """Passo 1.2: Rejeita tentativas de pareamento com QR Code expirado."""
        # Cria código com validade no passado
        with self.registry.lock, self.registry.db:
            code = "expired-code-123"
            self.registry.db.execute(
                "INSERT INTO enrollment VALUES(?, ?, 0)",
                (hashlib.sha256(code.encode()).hexdigest(), int(time.time()) - 10),
            )

        proof = b64(self.device_private_key.sign(b"MARK-PAIR-V1\n" + code.encode(), ec.ECDSA(hashes.SHA256())))
        with self.assertRaises(ValueError) as ctx:
            self.registry.enroll(code, "Expired-Device", self.device_public_b64, proof)
        self.assertIn("pareamento expirado ou já usado", str(ctx.exception))

    def test_step1_cryptographic_proof_of_possession_and_curve_enforcement(self):
        """Passo 1.3: Exige prova de posse válida e curva estrita EC P-256; rejeita assinaturas falsas ou outras curvas."""
        code = self.registry.open_enrollment(validity_seconds=60)

        # 1. Prova forjada / assinatura inválida é rejeitada
        with self.assertRaises(ValueError) as ctx:
            self.registry.enroll(code, "Fake-Proof", self.device_public_b64, b64(b"invalid-signature-bytes"))
        self.assertIn("prova de posse inválida", str(ctx.exception))

        # 2. Curva não-P-256 (ex: SECP384R1) é terminantemente rejeitada
        other_key = ec.generate_private_key(ec.SECP384R1())
        other_der = other_key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        other_proof = b64(other_key.sign(b"MARK-PAIR-V1\n" + code.encode(), ec.ECDSA(hashes.SHA256())))
        with self.assertRaises(ValueError) as ctx:
            self.registry.enroll(code, "Wrong-Curve", b64(other_der), other_proof)
        self.assertIn("use chave EC P-256", str(ctx.exception))

    def test_step1_anti_replay_with_nonces_and_timestamps(self):
        """Passo 1.4: Proteção anti-replay com nonces de uso único e janela temporal (CLOCK_SKEW)."""
        code = self.registry.open_enrollment()
        proof = b64(self.device_private_key.sign(b"MARK-PAIR-V1\n" + code.encode(), ec.ECDSA(hashes.SHA256())))
        device_id = self.registry.enroll(code, "Test-Device", self.device_public_b64, proof)
        self.registry.update_device(device_id, "authorized")

        now = str(int(time.time()))
        nonce = "unique-nonce-001"
        body = b'{"command":"ping"}'
        sig = b64(self.device_private_key.sign(
            signed_message("POST", "/v1/test", body, now, nonce),
            ec.ECDSA(hashes.SHA256()),
        ))

        # Primeira verificação: aceita
        self.assertTrue(self.registry.verify(device_id, "POST", "/v1/test", body, now, nonce, sig))

        # Replay com mesmo nonce: rejeitado imediatamente
        self.assertFalse(self.registry.verify(device_id, "POST", "/v1/test", body, now, nonce, sig))

        # Timestamp no passado além de CLOCK_SKEW (60s): rejeitado
        stale_time = str(int(time.time()) - CLOCK_SKEW - 10)
        stale_sig = b64(self.device_private_key.sign(
            signed_message("POST", "/v1/test", body, stale_time, "fresh-nonce"),
            ec.ECDSA(hashes.SHA256()),
        ))
        self.assertFalse(self.registry.verify(device_id, "POST", "/v1/test", body, stale_time, "fresh-nonce", stale_sig))

    # =========================================================================
    # PASSO 2: Certificate Pinning, mTLS e Proteção contra MITM
    # =========================================================================

    def test_step2_strict_certificate_pinning_and_tls13_enforcement(self):
        """Passo 2.1: Valida aplicação estrita de certificate pinning e TLS 1.3."""
        # Cria certificado de teste simulado
        genuine_cert_der = b"GENUINE_MARK_TLS13_SERVER_CERT_LEAF_" + b"0" * 32
        genuine_pin = compute_cert_pin(genuine_cert_der)

        validator = CertificatePinningValidator(primary_pin=genuine_pin)
        self.assertEqual(validator.primary_pin, genuine_pin)

        # Conexão legítima com TLS 1.3 e certificado esperado: sucesso
        validated_pin = validator.validate_connection("TLSv1.3", genuine_cert_der)
        self.assertEqual(validated_pin, genuine_pin)

        # Tentativa com protocolo inseguro (TLS 1.2): bloqueada
        with self.assertRaises(TlsSecurityError) as ctx:
            validator.validate_connection("TLSv1.2", genuine_cert_der)
        self.assertIn("TLS 1.3", str(ctx.exception))

        # Tentativa de tráfego claro: bloqueada
        with self.assertRaises(TlsSecurityError):
            validator.validate_connection("HTTP/1.1", genuine_cert_der)

    def test_step2_mitm_interception_and_rogue_ca_rejection(self):
        """Passo 2.2: Rejeição imediata de certificados de proxy MITM e CAs não reconhecidas."""
        genuine_cert_der = b"GENUINE_MARK_TLS13_SERVER_CERT_LEAF_" + b"1" * 32
        genuine_pin = compute_cert_pin(genuine_cert_der)

        validator = CertificatePinningValidator(primary_pin=genuine_pin)

        # Simula certificado gerado por proxy MITM (mitmproxy, burp, CA invasora corporativa)
        mitm_proxy_cert_der = b"MITM_INTERCEPTOR_PROXY_CERT_LEAF____" + b"2" * 32

        # Validação direta lança CertificatePinError
        with self.assertRaises(CertificatePinError) as ctx:
            validator.validate_peer_certificate(mitm_proxy_cert_der)
        self.assertIn("Identidade do servidor MARK mudou", str(ctx.exception))
        self.assertIn("Conexão bloqueada", str(ctx.exception))

        # Simulação automatizada de MITM confirma bloqueio antes de qualquer dado trafegar
        permitted, reason = validator.simulate_mitm_interception(mitm_proxy_cert_der)
        self.assertFalse(permitted)
        self.assertIn("Identidade do servidor MARK mudou", reason)

        # Certificado ausente na negociação TLS: bloqueado
        with self.assertRaises(CertificatePinError):
            validator.validate_peer_certificate(None)

    def test_step2_safe_certificate_rotation_with_graceful_fallback(self):
        """Passo 2.3: Suporte a rotação segura de certificados com falha graciosa e bloqueio de tráfego."""
        old_cert_der = b"MARK_OLD_CERT_LEAF__________________" + b"3" * 32
        new_cert_der = b"MARK_NEW_ROTATED_CERT_LEAF__________" + b"4" * 32
        attacker_cert_der = b"MARK_ATTACKER_FAKE_CERT_____________" + b"5" * 32

        old_pin = compute_cert_pin(old_cert_der)
        new_pin = compute_cert_pin(new_cert_der)

        validator = CertificatePinningValidator(primary_pin=old_pin)

        # Inicia rotação preservando o anterior como backup temporário
        validator.rotate_pin(new_pin=new_pin, keep_previous_as_backup=True)
        self.assertEqual(validator.primary_pin, new_pin)
        self.assertIn(old_pin, validator.active_pins)

        # Ambos os certificados (novo principal e antigo de backup) são aceitos durante a transição
        self.assertEqual(validator.validate_peer_certificate(new_cert_der), new_pin)
        self.assertEqual(validator.validate_peer_certificate(old_cert_der), old_pin)

        # Certificado estranho não autorizado: bloqueado imediatamente
        with self.assertRaises(CertificatePinError):
            validator.validate_peer_certificate(attacker_cert_der)

        # Conclusão da rotação: desativação do backup antigo
        validator.rotate_pin(new_pin=new_pin, keep_previous_as_backup=False)
        self.assertEqual(validator.validate_peer_certificate(new_cert_der), new_pin)
        with self.assertRaises(CertificatePinError):
            validator.validate_peer_certificate(old_cert_der)

    # =========================================================================
    # PASSO 3: Revogação Efetiva de Dispositivos em Tempo Real
    # =========================================================================

    def test_step3_instant_device_revocation_and_realtime_session_purge(self):
        """Passo 3.1: Revogação imediata expurga sessões ativas no SessionRegistry e corta acesso em tempo real."""
        code = self.registry.open_enrollment()
        proof = b64(self.device_private_key.sign(b"MARK-PAIR-V1\n" + code.encode(), ec.ECDSA(hashes.SHA256())))
        device_id = self.registry.enroll(code, "Revoke-Test", self.device_public_b64, proof)
        self.registry.update_device(device_id, "authorized")

        # Emite sessão válida
        token, expiry = self.sessions.issue(device_id)
        self.assertTrue(self.sessions.valid(device_id, token))

        revocation_notified = []
        self.registry.register_revocation_listener(lambda dev: revocation_notified.append(dev))

        # Executa revogação em tempo real
        revoked = self.registry.revoke_device(device_id)
        self.assertTrue(revoked)
        self.assertEqual(self.registry.status(device_id), "revoked")
        self.assertEqual(revocation_notified, [device_id])

        # Sessão ativa é IMEDIATAMENTE invalidada em memória e no banco
        self.assertFalse(self.sessions.valid(device_id, token))

        # Tentativa de emitir nova sessão: negada
        with self.assertRaises(ValueError) as ctx:
            self.sessions.issue(device_id)
        self.assertIn("aparelho não autorizado", str(ctx.exception))

    def test_step3_http_api_instant_revocation_without_restart(self):
        """Passo 3.2: Dispositivo revogado perde acesso HTTP e SSE imediatamente sem reinício do servidor."""
        code = self.registry.open_enrollment()
        proof = b64(self.device_private_key.sign(b"MARK-PAIR-V1\n" + code.encode(), ec.ECDSA(hashes.SHA256())))
        device_id = self.registry.enroll(code, "HTTP-Revoke", self.device_public_b64, proof)
        self.registry.update_device(device_id, "authorized")

        server = MobileServer(load_config(), "legacy-tok", 0, memory=ShortMemory(), devices=self.registry)
        awake_session = Mock(awake=True)
        server.model_sessions = {"chat": awake_session, "call": awake_session}
        server.model_session = awake_session
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        try:
            base = f"http://127.0.0.1:{server.server_port}"
            now = str(int(time.time()))
            boot_nonce = "boot-rev-1"
            boot_sig = b64(self.device_private_key.sign(
                signed_message("POST", "/v1/session", b"", now, boot_nonce),
                ec.ECDSA(hashes.SHA256()),
            ))
            boot_req = urllib.request.Request(
                base + "/v1/session",
                data=b"",
                headers={
                    "X-Mark-Device": device_id,
                    "X-Mark-Time": now,
                    "X-Mark-Nonce": boot_nonce,
                    "X-Mark-Signature": boot_sig,
                },
            )
            with urllib.request.urlopen(boot_req, timeout=3) as res:
                token = json.load(res)["session_token"]

            # Requisição autorizada antes da revogação
            req_nonce = "req-pre-rev"
            req_sig = b64(self.device_private_key.sign(
                session_message("GET", "/health", b"", now, req_nonce, "rid-1", token),
                ec.ECDSA(hashes.SHA256()),
            ))
            healthy_req = urllib.request.Request(
                base + "/health",
                headers={
                    "X-Mark-Device": device_id,
                    "X-Mark-Time": now,
                    "X-Mark-Nonce": req_nonce,
                    "X-Mark-Signature": req_sig,
                    "X-Mark-Session": token,
                    "X-Mark-Request-Id": "rid-1",
                },
            )
            with urllib.request.urlopen(healthy_req, timeout=3) as res:
                self.assertEqual(json.load(res)["status"], "ok")

            # REVOGAÇÃO IMEDIATA DO DISPOSITIVO (sem reiniciar o server)
            self.registry.revoke_device(device_id)

            # Próxima requisição é IMEDIATAMENTE rejeitada com HTTP 401
            req_nonce_post = "req-post-rev"
            req_sig_post = b64(self.device_private_key.sign(
                session_message("GET", "/health", b"", now, req_nonce_post, "rid-2", token),
                ec.ECDSA(hashes.SHA256()),
            ))
            blocked_req = urllib.request.Request(
                base + "/health",
                headers={
                    "X-Mark-Device": device_id,
                    "X-Mark-Time": now,
                    "X-Mark-Nonce": req_nonce_post,
                    "X-Mark-Signature": req_sig_post,
                    "X-Mark-Session": token,
                    "X-Mark-Request-Id": "rid-2",
                },
            )
            with self.assertRaises(urllib.error.HTTPError) as denied:
                urllib.request.urlopen(blocked_req, timeout=3)
            self.assertEqual(denied.exception.code, 401)
            denied.exception.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    # =========================================================================
    # PASSO 4: Sessões Curtas, Renovação Autenticada e Confirmação do Dono
    # =========================================================================

    def test_step4_short_session_lifetime_and_authenticated_renewal(self):
        """Passo 4.1: Sessão curta com renovação autenticada que invalida o token anterior."""
        code = self.registry.open_enrollment()
        proof = b64(self.device_private_key.sign(b"MARK-PAIR-V1\n" + code.encode(), ec.ECDSA(hashes.SHA256())))
        device_id = self.registry.enroll(code, "Short-Session-Dev", self.device_public_b64, proof)
        self.registry.update_device(device_id, "authorized")

        # Sessão curta padrão de 120 segundos
        self.assertEqual(self.sessions.lifetime, 120)
        token1, expiry1 = self.sessions.issue(device_id)
        self.assertLessEqual(expiry1 - int(time.time()), 120)
        self.assertTrue(self.sessions.valid(device_id, token1))

        # Renovação autenticada
        token2, expiry2 = self.sessions.renew(device_id, token1)
        self.assertNotEqual(token1, token2)
        self.assertTrue(self.sessions.valid(device_id, token2))

        # Token anterior (token1) foi invalidado na renovação
        self.assertFalse(self.sessions.valid(device_id, token1))

    def test_step4_owner_confirmation_and_llm_approval_prohibition(self):
        """Passo 4.2: Confirmação do proprietário para ações críticas e bloqueio estrito de auto-aprovação por LLM."""
        code = self.registry.open_enrollment()
        proof = b64(self.device_private_key.sign(b"MARK-PAIR-V1\n" + code.encode(), ec.ECDSA(hashes.SHA256())))
        device_id = self.registry.enroll(code, "Owner-Confirm-Dev", self.device_public_b64, proof)
        self.registry.update_device(device_id, "authorized")

        action = "clear_memory"
        self.assertIn(action, CRITICAL_ACTIONS)

        # 1. Proibição categórica de auto-aprovação pelo modelo de linguagem
        for llm_source in ("llm", "assistant", "model", "synthetic", "ai_agent"):
            with self.assertRaises(PermissionError) as ctx:
                assert_not_llm_approved(llm_source, action)
            self.assertIn("Aprovação autônoma pelo modelo de linguagem proibida", str(ctx.exception))

        # 2. Confirmação do dono legítima com assinatura biométrica ECDSA P-256
        challenge, ts, nonce = create_owner_challenge(action, device_id)
        sig = b64(self.device_private_key.sign(challenge.encode("ascii"), ec.ECDSA(hashes.SHA256())))

        valid = verify_owner_confirmation(
            self.registry, device_id, action, ts, nonce, sig, max_age_seconds=30
        )
        self.assertTrue(valid)

        # 3. Tentativa de replay do nonce de confirmação: rejeitada
        replay_valid = verify_owner_confirmation(
            self.registry, device_id, action, ts, nonce, sig, max_age_seconds=30
        )
        self.assertFalse(replay_valid)

        # 4. Assinatura com chave errada: rejeitada
        attacker_key = ec.generate_private_key(ec.SECP256R1())
        fake_sig = b64(attacker_key.sign(challenge.encode("ascii"), ec.ECDSA(hashes.SHA256())))
        self.assertFalse(
            verify_owner_confirmation(
                self.registry, device_id, action, ts, "fresh-nonce-fake", fake_sig, max_age_seconds=30
            )
        )

    # =========================================================================
    # PASSO 5: Auditoria de Interfaces, ACLs e Loopback
    # =========================================================================

    def test_step5_listening_interfaces_and_tailscale_serve_audit(self):
        """Passo 5.1: Auditoria de interfaces de escuta bloqueia 0.0.0.0 e valida Tailscale Serve."""
        # 1. Auditoria com endpoints legítimos em loopback
        clean_audit = audit_listening_interfaces([("127.0.0.1", 8765), ("127.0.0.1", 4173)])
        self.assertTrue(clean_audit["secure"])
        self.assertEqual(clean_audit["findings"], [])

        # 2. Auditoria detecta interface pública insegura e porta 80 proibida
        insecure_audit = audit_listening_interfaces([("0.0.0.0", 8765), ("127.0.0.1", 80)])
        self.assertFalse(insecure_audit["secure"])
        self.assertTrue(any("Interface pública insegura" in f for f in insecure_audit["findings"]))
        self.assertTrue(any("Porta HTTP 80 legada proibida" in f for f in insecure_audit["findings"]))

        # 3. Auditoria do Tailscale Serve
        ts_audit = audit_tailscale_serve_configuration()
        self.assertTrue(ts_audit["secure"])
        self.assertTrue(ts_audit["tls_enforced"])
        self.assertTrue(ts_audit["plaintext_blocked"])

    def test_step5_loopback_isolation_and_bypass_protection(self):
        """Passo 5.2: Bloqueia qualquer rota alternativa ou bypass de loopback sem credencial válida."""
        valid_local_token = "valid-secret-token-12345"

        # 1. Acesso loopback legítimo com token correto
        allowed, msg, code = verify_loopback_access_gate(
            "127.0.0.1", {"Authorization": f"Bearer {valid_local_token}"}, valid_local_token
        )
        self.assertTrue(allowed)
        self.assertEqual(code, 200)

        # 2. Acesso loopback sem credencial: 401
        allowed, msg, code = verify_loopback_access_gate("127.0.0.1", {}, valid_local_token)
        self.assertFalse(allowed)
        self.assertEqual(code, 401)

        # 3. Acesso loopback com token forjado: 401
        allowed, msg, code = verify_loopback_access_gate(
            "127.0.0.1", {"Authorization": "Bearer forged-token"}, valid_local_token
        )
        self.assertFalse(allowed)
        self.assertEqual(code, 401)

        # 4. Origem externa tentando usar token local de loopback: 401
        allowed, msg, code = verify_loopback_access_gate(
            "192.168.1.50", {"Authorization": f"Bearer {valid_local_token}"}, valid_local_token
        )
        self.assertFalse(allowed)
        self.assertEqual(code, 401)

        # 5. Token Bearer estático com cabeçalhos Tailscale/Proxy: 401
        allowed, msg, code = verify_loopback_access_gate(
            "127.0.0.1",
            {"Authorization": f"Bearer {valid_local_token}", "Tailscale-User-Login": "user@mark"},
            valid_local_token,
        )
        self.assertFalse(allowed)
        self.assertEqual(code, 401)

        # 6. Tráfego HTTP plano mascarado por proxy: 403
        allowed, msg, code = verify_loopback_access_gate(
            "127.0.0.1",
            {"Authorization": f"Bearer {valid_local_token}", "X-Forwarded-Proto": "http"},
            valid_local_token,
        )
        self.assertFalse(allowed)
        self.assertEqual(code, 403)

    # =========================================================================
    # PASSO 6: Integridade do Pipeline de Build e Distribuição Mobile
    # =========================================================================

    def test_step6_apk_signature_scheme_v2_verification(self):
        """Passo 6.1: Validação de assinatura APK Signature Scheme v2 com certificado legítimo do proprietário."""
        # Cria artefato APK sintético estruturado com bloco de assinatura legítimo
        base_payload = b"PK\x03\x04" + b"A" * 2048
        genuine_apk = (
            base_payload
            + MARK_OWNER_SIGNER_SHA256.encode("ascii")
            + APK_SIG_BLOCK_MAGIC
            + f"\nMARK_SIGNER_SHA256:{MARK_OWNER_SIGNER_SHA256}".encode("ascii")
        )
        expected_hash = compute_sha256(genuine_apk)

        result = verify_apk_artifact(
            genuine_apk,
            expected_sha256=expected_hash,
            authorized_signers=[MARK_OWNER_SIGNER_SHA256],
        )
        self.assertTrue(result["valid"])
        self.assertTrue(result["matches_owner_key"])
        self.assertEqual(result["signers"], [MARK_OWNER_SIGNER_SHA256])

    def test_step6_detection_of_tampered_and_resigned_apks(self):
        """Passo 6.2: Detecção estrita de binários APK adulterados ou reassinados por terceiros."""
        base_payload = b"PK\x03\x04" + b"B" * 2048
        genuine_apk = (
            base_payload
            + MARK_OWNER_SIGNER_SHA256.encode("ascii")
            + APK_SIG_BLOCK_MAGIC
            + f"\nMARK_SIGNER_SHA256:{MARK_OWNER_SIGNER_SHA256}".encode("ascii")
        )

        # 1. Simulação de adulteração binária (1 byte alterado): detectado e bloqueado
        tamper_success, tamper_reason = simulate_apk_tampering(genuine_apk, flip_offset=100)
        self.assertFalse(tamper_success)
        self.assertIn("Adulteração detectada com sucesso", tamper_reason)

        # 2. Simulação de reassinatura por atacante com outra chave: detectado e bloqueado
        attacker_signer = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
        resigned_success, resign_reason = simulate_third_party_resigning(genuine_apk, attacker_signer)
        self.assertFalse(resigned_success)
        self.assertIn("Reassinatura bloqueada com sucesso", resign_reason)

    def test_step6_android_keystore_security_policy(self):
        """Passo 6.3: Auditoria da política e garantias de segurança do Android Keystore."""
        policy = verify_android_keystore_policy()
        self.assertEqual(policy["provider"], "AndroidKeyStore")
        self.assertEqual(policy["curve"], "secp256r1")
        self.assertTrue(policy["non_exportable_keys"])
        self.assertTrue(policy["hardware_backed_storage"])
        self.assertTrue(policy["biometric_gating"]["required_for_critical_actions"])
        self.assertEqual(policy["biometric_gating"]["authenticator_type"], "BIOMETRIC_STRONG")
        self.assertTrue(policy["resigning_protection"]["keystore_invalidated_on_resigning"])


if __name__ == "__main__":
    unittest.main()

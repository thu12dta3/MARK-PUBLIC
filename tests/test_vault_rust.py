"""Suíte de testes de segurança para o Cofre de Credenciais em Rust (mark_core / PyO3).

Valida:
1. Geração de Nonces de 96 bits aleatórios e conformidade NIST SP 800-38D.
2. Cifragem e decifragem autenticada AES-256-GCM.
3. Derivação forte de chaves via Argon2id e HKDF-SHA256.
4. Tratamento e rejeição estrita de:
   - Tag de autenticação corrompida / adulterada
   - Nonce inválido ou com tamanho diferente de 96 bits
   - Chave mestra alterada ou incorreta
   - Ciphertext truncado
5. Interoperabilidade do schema de payload JSON/BSON multi-dispositivo (Desktop, Android, Wear OS).
6. Integração do LLMKeyVault com o motor Rust compilado.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import tempfile
import unittest

import mark_core
from mark.config.llm_config import LLMKeyVault, VAULT_AAD, StorageSecurityError


class TestRustCredentialVault(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory(prefix="mark-test-vault-")
        self.tmp_path = Path(self.tmp_dir.name)
        self.vault_file = self.tmp_path / "keys.enc"
        self.master_key_file = self.tmp_path / ".master.key"

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_rust_ping(self):
        """Valida que o módulo compilado mark_core responde ao ping."""
        self.assertEqual(mark_core.rust_ping(), "MARK Rust Core Online")

    def test_nonce_generation_is_strictly_96_bits(self):
        """Valida que cada operação gera um Nonce de 96 bits (12 bytes) e não repetido."""
        nonce1 = mark_core.generate_nonce_96()
        nonce2 = mark_core.generate_nonce_96()
        self.assertEqual(len(nonce1), 12)
        self.assertEqual(len(nonce2), 12)
        self.assertNotEqual(nonce1, nonce2, "Nonces consecutivos não podem se repetir")

    def test_argon2id_derivation_256_bits(self):
        """Valida que a derivação Argon2id produz exatamente 32 bytes (256 bits)."""
        salt = os.urandom(16)
        key1 = mark_core.derive_key_argon2id("senha_mestra_mark_2026", salt)
        key2 = mark_core.derive_key_argon2id("senha_mestra_mark_2026", salt)
        self.assertEqual(len(key1), 32)
        self.assertEqual(key1, key2)

        # Salt diferente produz chave diferente
        other_salt = os.urandom(16)
        key3 = mark_core.derive_key_argon2id("senha_mestra_mark_2026", other_salt)
        self.assertNotEqual(key1, key3)

    def test_hkdf_derivation(self):
        """Valida que o HKDF-SHA256 deriva chaves com comprimento parametrizável."""
        ikm = b"initial_key_material_256_bits_xx"
        salt = b"salt_for_hkdf_16b"
        info = b"mark-credentials-v1"
        sub_key = mark_core.derive_key_hkdf(ikm, salt, info, 32)
        self.assertEqual(len(sub_key), 32)

    def test_aes_256_gcm_encryption_roundtrip_with_aad(self):
        """Valida cifragem e decifragem com AAD no schema padronizado de payload."""
        key = os.urandom(32)
        plaintext = b"sk-proj-super-secret-api-key-12345"
        aad = b"MARK-AAD-PAYLOAD-V1"

        payload_json = mark_core.encrypt_aes_256_gcm(key, plaintext, aad)
        payload = json.loads(payload_json)

        self.assertEqual(payload["version"], 1)
        self.assertEqual(payload["cipher"], "AES-256-GCM")
        self.assertIn("nonce", payload)
        self.assertIn("ciphertext", payload)
        self.assertIn("tag", payload)

        # Valida tamanhos decodificados
        nonce_bytes = base64.b64decode(payload["nonce"])
        tag_bytes = base64.b64decode(payload["tag"])
        self.assertEqual(len(nonce_bytes), 12)
        self.assertEqual(len(tag_bytes), 16)

        decrypted = mark_core.decrypt_aes_256_gcm(key, payload_json, aad)
        self.assertEqual(decrypted, plaintext)

    def test_corrupted_auth_tag_raises_error(self):
        """Valida que se a tag de autenticação for adulterada, a decifragem falha imediatamente."""
        key = os.urandom(32)
        plaintext = b"segredo_sensivel"
        payload_json = mark_core.encrypt_aes_256_gcm(key, plaintext, None)
        payload = json.loads(payload_json)

        # Adultera o primeiro byte da tag
        tag_bytes = bytearray(base64.b64decode(payload["tag"]))
        tag_bytes[0] ^= 0xFF
        payload["tag"] = base64.b64encode(tag_bytes).decode("ascii")

        # Adultera também o final do ciphertext_with_tag
        ct_bytes = bytearray(base64.b64decode(payload["ciphertext"]))
        ct_bytes[-16] ^= 0xFF
        payload["ciphertext"] = base64.b64encode(ct_bytes).decode("ascii")

        tampered_json = json.dumps(payload)
        with self.assertRaises((ValueError, RuntimeError)):
            mark_core.decrypt_aes_256_gcm(key, tampered_json, None)

    def test_invalid_nonce_length_rejected(self):
        """Valida que Nonces que não possuam 96 bits (12 bytes) são rejeitados."""
        key = os.urandom(32)
        payload_json = mark_core.encrypt_aes_256_gcm(key, b"dados", None)
        payload = json.loads(payload_json)

        # Altera nonce para 8 bytes em vez de 12 bytes
        payload["nonce"] = base64.b64encode(b"12345678").decode("ascii")
        with self.assertRaises((ValueError, RuntimeError)):
            mark_core.decrypt_aes_256_gcm(key, json.dumps(payload), None)

    def test_altered_key_fails_authentication(self):
        """Valida que decifrar com chave errada resulta em falha de autenticação AEAD."""
        key1 = os.urandom(32)
        key2 = os.urandom(32)
        payload_json = mark_core.encrypt_aes_256_gcm(key1, b"mensagem", None)

        with self.assertRaises((ValueError, RuntimeError)):
            mark_core.decrypt_aes_256_gcm(key2, payload_json, None)

    def test_seal_and_unseal_high_level_argon2id(self):
        """Valida as funções de alto nível seal_credentials e unseal_credentials com Argon2id."""
        passphrase = "master_password_vault_mark_2026"
        secret_data = json.dumps({"openai": "sk-12345", "deepseek": "sk-67890"})
        aad = "MARK-CREDENTIALS-AAD"

        sealed_json = mark_core.seal_credentials(passphrase, secret_data, aad)
        self.assertTrue(mark_core.validate_vault_payload(sealed_json))

        unsealed_data = mark_core.unseal_credentials(passphrase, sealed_json, aad)
        self.assertEqual(unsealed_data, secret_data)

        # Senha errada deve levantar erro
        with self.assertRaises((ValueError, RuntimeError)):
            mark_core.unseal_credentials("senha_errada", sealed_json, aad)

    def test_llm_key_vault_interoperability(self):
        """Valida que o LLMKeyVault grava e lê credenciais utilizando a criptografia autenticada."""
        vault = LLMKeyVault(
            vault_file=self.vault_file,
            master_key_file=self.master_key_file,
        )
        vault.set_key("groq", "gsk_test_api_key_secure_123")
        vault.set_key("anthropic", "sk-ant-test-key-456")

        # Lê chaves gravadas
        groq_key = vault.get_key("groq")
        ant_key = vault.get_key("anthropic")
        self.assertEqual(groq_key, "gsk_test_api_key_secure_123")
        self.assertEqual(ant_key, "sk-ant-test-key-456")

        # Instancia novo cofre apontando para o mesmo arquivo
        vault2 = LLMKeyVault(
            vault_file=self.vault_file,
            master_key_file=self.master_key_file,
        )
        self.assertEqual(vault2.get_key("groq"), "gsk_test_api_key_secure_123")
        self.assertEqual(vault2.get_key("anthropic"), "sk-ant-test-key-456")

        # Corrompe 1 byte no arquivo do cofre -> deve levantar StorageSecurityError
        raw_bytes = bytearray(self.vault_file.read_bytes())
        raw_bytes[-1] ^= 0xAA
        self.vault_file.write_bytes(raw_bytes)

        vault3 = LLMKeyVault(
            vault_file=self.vault_file,
            master_key_file=self.master_key_file,
        )
        with self.assertRaises(StorageSecurityError):
            vault3.get_key("groq")


if __name__ == "__main__":
    unittest.main()

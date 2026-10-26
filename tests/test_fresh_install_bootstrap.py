"""Testes de Bootstrapping Automático e Tratamento Defensivo de Tokens (Instalação Limpa).

Valida:
1. Bootstrapping automático do diretório ~/.config/mark/ (0700) e mobile.token (0600) em instalação limpa.
2. Tratamento gracioso de FileNotFoundError / PermissionError na leitura de tokens, retornando valor nulo/vazio.
3. Ausência de FileNotFoundError não capturado no pipeline de requisição / chat do desktop.
4. Estado de pareamento móvel nulo/inativo seguro quando o token não estiver presente.
5. Inicialização e geração transparente de chave móvel segura no mobile_api.
"""

from __future__ import annotations

import os
import stat
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mark import desktop_helpers
from mark import mobile_api


class TestFreshInstallBootstrap(unittest.TestCase):
    """Garante resiliência e integridade em cenários de instalação limpa (fresh install)."""

    def setUp(self):
        self.test_dir = Path("/tmp/mark_test_fresh_bootstrap_" + os.urandom(8).hex())
        self.config_dir = self.test_dir / ".config" / "mark"
        self.token_file = self.config_dir / "mobile.token"

    def tearDown(self):
        if self.token_file.exists():
            try:
                self.token_file.unlink()
            except OSError:
                pass
        if self.config_dir.exists():
            try:
                self.config_dir.rmdir()
            except OSError:
                pass
        p = self.test_dir / ".config"
        if p.exists():
            try:
                p.rmdir()
            except OSError:
                pass
        if self.test_dir.exists():
            try:
                self.test_dir.rmdir()
            except OSError:
                pass

    def test_ensure_config_dir_exists_creates_folder_and_file_with_strict_permissions(self):
        """Verifica se ensure_config_dir_exists cria pasta 0700 e mobile.token 0600."""
        self.assertTrue(
            hasattr(desktop_helpers, "ensure_config_dir_exists"),
            "desktop_helpers deve disponibilizar a função ensure_config_dir_exists",
        )
        self.assertFalse(self.config_dir.exists())
        self.assertFalse(self.token_file.exists())

        desktop_helpers.ensure_config_dir_exists(self.config_dir)

        self.assertTrue(self.config_dir.exists(), "O diretório ~/.config/mark deve ser criado")
        self.assertTrue(self.token_file.exists(), "O arquivo mobile.token deve ser criado")

        dir_stat = self.config_dir.stat()
        file_stat = self.token_file.stat()

        # Permissões POSIX
        dir_mode = stat.S_IMODE(dir_stat.st_mode)
        file_mode = stat.S_IMODE(file_stat.st_mode)
        self.assertEqual(dir_mode, 0o700, f"Diretório deve ter permissão 0700, obtido {oct(dir_mode)}")
        self.assertEqual(file_mode, 0o600, f"Arquivo mobile.token deve ter permissão 0600, obtido {oct(file_mode)}")

    def test_read_token_defensive_returns_empty_without_raising_filenotfound(self):
        """Em instalação limpa, a leitura do token não deve lançar FileNotFoundError."""
        self.assertFalse(self.token_file.exists())
        with patch.object(desktop_helpers, "TOKEN_FILE", self.token_file):
            token = desktop_helpers._token()
            self.assertEqual(token, "", "Token ausente deve retornar string vazia")
            self.assertTrue(self.token_file.exists(), "mobile.token deve ter sido criado no bootstrap")
            self.assertEqual(stat.S_IMODE(self.token_file.stat().st_mode), 0o600)

    def test_desktop_request_does_not_crash_with_filenotfound_on_missing_token(self):
        """_request não deve lançar FileNotFoundError se o token ainda não existe."""
        self.assertFalse(self.token_file.exists())
        with patch.object(desktop_helpers, "TOKEN_FILE", self.token_file):
            with patch("urllib.request.urlopen") as mock_urlopen:
                mock_resp = MagicMock()
                mock_resp.status = 200
                mock_resp.read.return_value = b'{"status": "ok"}'
                mock_resp.__enter__.return_value = mock_resp
                mock_urlopen.return_value = mock_resp

                try:
                    status, body = desktop_helpers._request("/health", timeout=2)
                    self.assertEqual(status, 200)
                except FileNotFoundError as exc:
                    self.fail(f"_request lançou FileNotFoundError não capturado: {exc}")

    def test_mobile_pairing_returns_safe_inactive_state_when_token_missing(self):
        """_mobile_pairing deve retornar estado inativo seguro quando não houver token."""
        self.assertFalse(self.token_file.exists())
        with patch.object(desktop_helpers, "TOKEN_FILE", self.token_file):
            pairing_state = desktop_helpers._mobile_pairing()
            self.assertIsInstance(pairing_state, dict)
            self.assertIn(pairing_state.get("status"), {"inactive", "disabled", "not_configured", "offline"})
            self.assertFalse(pairing_state.get("active", False))
            self.assertIsNone(pairing_state.get("token"))

    def test_mobile_api_token_handles_empty_token_file(self):
        """mobile_token() deve gerar novo token seguro quando o arquivo estiver vazio."""
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.token_file.touch(mode=0o600)
        self.assertEqual(self.token_file.stat().st_size, 0)

        token = mobile_api.mobile_token(self.token_file)
        self.assertIsInstance(token, str)
        self.assertGreaterEqual(len(token), 32)
        self.assertGreater(self.token_file.stat().st_size, 0)
        self.assertEqual(stat.S_IMODE(self.token_file.stat().st_mode), 0o600)


if __name__ == "__main__":
    unittest.main()

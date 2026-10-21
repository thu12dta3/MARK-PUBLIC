"""Suíte de testes de integração e endurecimento para o Sandboxing (bwrap) - MARK Core.

Valida:
1. Confinamento e isolamento de kernel via Bubblewrap (--unshare-all, --proc /proc, --dev /dev, --tmpfs /tmp).
2. Bloqueio estrito de escape de diretório (directory traversal) e symlinks maliciosos.
3. Inacessibilidade a diretórios sensíveis do host (~/.ssh, ~/.gnupg, ~/.bashrc, ~/.config, /etc/shadow).
4. Bloqueio de chamadas de rede não autorizadas pelo namespace isolado (bwrap --unshare-net).
5. Limpeza de variáveis de ambiente sensíveis (--clearenv).
"""

from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from mark.core.sandbox import (
    ExecutionSandbox,
    FORBIDDEN_PATH_TOKENS,
    SandboxLimits,
    SandboxResult,
    SandboxSecurityError,
    sanitize_environment,
)


class TestSandboxHardening(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory(prefix="mark-test-sbx-")
        self.workspace = Path(self.tmp_dir.name).resolve()

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_core_sandbox_exports(self):
        """Valida que o módulo mark.core.sandbox re-exporta corretamente todos os símbolos necessários."""
        import mark.core.sandbox as core_sbx
        import mark.core as mark_core_pkg

        self.assertTrue(hasattr(core_sbx, "ExecutionSandbox"))
        self.assertTrue(hasattr(core_sbx, "SandboxSecurityError"))
        self.assertTrue(hasattr(core_sbx, "SandboxResult"))
        self.assertTrue(hasattr(mark_core_pkg, "ExecutionSandbox"))

    def test_blocks_sensitive_user_workspaces(self):
        """Valida que diretórios sensíveis do usuário (~/.ssh, ~/.config, ~/.bashrc) são rejeitados como workspace."""
        sensitive_dirs = [
            Path.home() / ".ssh",
            Path.home() / ".gnupg",
            Path.home() / ".config",
            Path.home() / ".bashrc",
            Path.home() / ".bash_profile",
            Path("/etc"),
            Path("/root"),
            Path("/"),
            Path.home(),
        ]
        for sdir in sensitive_dirs:
            with self.assertRaises(SandboxSecurityError, msg=f"Deveria bloquear workspace: {sdir}"):
                ExecutionSandbox(workspace=sdir)

    def test_directory_traversal_via_dots_and_absolute_paths_blocked(self):
        """Valida que tentativas de directory traversal (../ e caminhos absolutos protegidos) são barradas."""
        with ExecutionSandbox(workspace=self.workspace) as sandbox:
            traversal_vectors = [
                "../../etc/passwd",
                "../.ssh/id_rsa",
                "subdir/../../../.bashrc",
                "/home/user/.bashrc",
                "/home/user/.config",
                "/home/user/.ssh/id_ed25519",
                "/etc/shadow",
                "/etc/sudoers",
                "~/.gnupg/secring.gpg",
            ]
            for vec in traversal_vectors:
                with self.assertRaises(SandboxSecurityError, msg=f"Deveria bloquear traversal: {vec}"):
                    sandbox.validate_command(["cat", vec])

    def test_symlink_escape_from_workspace_blocked(self):
        """Valida que symlinks criados dentro do workspace apontando para fora são detectados e bloqueados."""
        secret_file = self.workspace.parent / "host_secret.txt"
        secret_file.write_text("HOST_SECRET_TOKEN", encoding="utf-8")
        try:
            link_path = self.workspace / "sneaky_link.txt"
            link_path.symlink_to(secret_file)

            with ExecutionSandbox(workspace=self.workspace) as sandbox:
                with self.assertRaises(SandboxSecurityError):
                    sandbox.validate_command(["cat", "sneaky_link.txt"])
        finally:
            if secret_file.exists():
                secret_file.unlink()

    def test_bwrap_command_includes_mandatory_kernel_flags(self):
        """Valida que o comando construído para o bwrap aplica todas as flags obrigatórias de isolamento."""
        with ExecutionSandbox(workspace=self.workspace) as sandbox:
            cmd = sandbox._build_bwrap_command(
                argv=["python3", "-c", "print('TEST')"],
                work_dir=self.workspace,
                clean_env={"PATH": "/usr/bin:/bin"},
                temp_home="/tmp/fake_home",
                cpu_seconds=10,
            )
            cmd_str = " ".join(cmd)
            self.assertIn("--unshare-all", cmd_str)
            self.assertIn("--proc", cmd)
            self.assertIn("/proc", cmd)
            self.assertIn("--dev", cmd)
            self.assertIn("/dev", cmd)
            self.assertIn("--tmpfs", cmd)
            self.assertIn("/tmp", cmd)
            self.assertIn("/home", cmd)
            self.assertIn("--ro-bind", cmd)
            self.assertIn("/usr", cmd)
            self.assertIn("--cap-drop", cmd)
            self.assertIn("ALL", cmd)
            self.assertIn("--clearenv", cmd)

    def test_bwrap_isolates_sensitive_host_paths_inside_container(self):
        """Valida que dentro do container bwrap, ~/.ssh, ~/.bashrc e ~/.config são inacessíveis."""
        with ExecutionSandbox(workspace=self.workspace) as sandbox:
            if not sandbox.bwrap_available:
                self.skipTest("Bubblewrap (bwrap) não disponível no host para teste ativo de contenção.")

            # Script executado dentro do bwrap tentando ler arquivos sensíveis do host
            probe_script = self.workspace / "probe_host.py"
            probe_script.write_text(
                "import os, sys\n"
                "from pathlib import Path\n"
                f"home_user = Path('{Path.home().as_posix()}')\n"
                "ssh_name = '.' + 'ssh'\n"
                "bashrc_name = '.' + 'bashrc'\n"
                "config_name = '.' + 'config'\n"
                "shadow_path = '/' + 'etc' + '/' + 'shadow'\n"
                "ssh_exists = (home_user / ssh_name).exists()\n"
                "bashrc_exists = (home_user / bashrc_name).exists()\n"
                "config_exists = (home_user / config_name).exists()\n"
                "shadow_exists = Path(shadow_path).exists() and os.access(shadow_path, os.R_OK)\n"
                "if ssh_exists or bashrc_exists or config_exists or shadow_exists:\n"
                "    print(f'LEAK: ssh={ssh_exists} bashrc={bashrc_exists} config={config_exists} shadow={shadow_exists}')\n"
                "    sys.exit(99)\n"
                "print('HOST_ISOLATION_VERIFIED_CLEAN')\n"
                "sys.exit(0)\n",
                encoding="utf-8",
            )
            res = sandbox.run_python_script(probe_script)
            self.assertEqual(res.exit_code, 0, msg=f"stdout={res.stdout} stderr={res.stderr}")
            self.assertIn("HOST_ISOLATION_VERIFIED_CLEAN", res.stdout)

    def test_bwrap_blocks_unauthorized_network_sockets_with_unshare_net(self):
        """Valida que chamadas de rede são bloqueadas tanto na análise estática quanto pelo namespace do kernel."""
        with ExecutionSandbox(workspace=self.workspace) as sandbox:
            # 1. Validação estática bloqueia chamada direta de rede
            with self.assertRaises(SandboxSecurityError):
                sandbox.run_python_script("import socket\ns = socket.socket()\n")

            if not sandbox.bwrap_available:
                self.skipTest("Bubblewrap (bwrap) não disponível no host.")

            # 2. Confinamento a nível de kernel via bwrap --unshare-all bloqueia qualquer conexão residual
            net_script = self.workspace / "try_unauthorized_net.py"
            net_script.write_text(
                "import socket, sys\n"
                "try:\n"
                "    sock_fn = getattr(socket, 'soc' + 'ket')\n"
                "    s = sock_fn(socket.AF_INET, socket.SOCK_STREAM)\n"
                "    s.settimeout(0.5)\n"
                "    s.connect(('127.0.0.1', 11434))\n"
                "    print('CONNECTED_UNAUTHORIZED')\n"
                "    sys.exit(1)\n"
                "except (OSError, socket.error) as exc:\n"
                "    print(f'SOCKET_BLOCKED:{type(exc).__name__}:{exc}')\n"
                "    sys.exit(0)\n",
                encoding="utf-8",
            )
            res = sandbox.run_python_script(net_script)
            self.assertEqual(res.exit_code, 0, msg=f"stdout={res.stdout} stderr={res.stderr}")
            self.assertIn("SOCKET_BLOCKED:", res.stdout)


if __name__ == "__main__":
    unittest.main()

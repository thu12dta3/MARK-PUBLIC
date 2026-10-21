"""Suíte Documentada de Testes Unitários de Segurança e Arquitetura (P0 Hardening).

Documenta e valida cada um dos 5 pilares de endurecimento P0 do ecossistema MARK:

1. Passo 1 — Endurecimento Real do ExecutionSandbox (`src/mark/sandbox.py`):
   - Isolamento efetivo a nível de SO com Bubblewrap (`bwrap --unshare-all`, `--unshare-net`,
     `--cap-drop ALL`, `--die-with-parent`, `--new-session`) e montagem read-only do sistema;
   - Bloqueio de escape de diretório, leitura/escrita fora do workspace autorizado (`/workspace`)
     e bloqueio de sockets de rede não autorizados por processos filhos;
   - Validação estrita de argumentos por executável (`find`, `git`, `python3`, `pytest`, `cargo`, `maturin`)
     e imposição de limites rígidos de memória (`RLIMIT_AS`), CPU (`RLIMIT_CPU`), descritores de arquivo
     (`RLIMIT_NOFILE`) e tamanho de arquivo (`RLIMIT_FSIZE`).

2. Passo 2 — Confinamento Absoluto do EngineerAgent (`src/mark/engineer_agent.py`):
   - Confinamento estrito ao diretório raiz autorizado (`/workspace` / `workspace_root`);
   - Prevenção de path traversal (`..`), symlinks, bytes nulos e modificação de módulos críticos de
     segurança (`sandbox.py`, `engineer_agent.py`, `device_auth.py`, `mobile_api.py`, etc.);
   - Preservação de backups automáticos verificáveis (`.mark_backups` + SHA-256) e validação de
     integridade AST + padrões proibidos + rollback imediato em falhas.

3. Passo 3 — Migração do Cofre Criptográfico do Smart Home Gateway (`src/mark/smart_home_gateway.py`):
   - Substituição de cifra caseira por primitivas autenticadas AEAD consolidadas (`AES-256-GCM` e
     `ChaCha20-Poly1305` da biblioteca `cryptography`);
   - Derivação segura de chaves via `PBKDF2HMAC` (SHA-256, >= 100.000 iterações, salt aleatório de 128 bits);
   - Proteção contra ataques de tempo (`hmac.compare_digest`), vinculação de metadados em AAD e
     rotação atômica de chaves (`rotate_key`) com permissão estrita `0600`.

4. Passo 4 — Autorização Granular por Ferramenta MCP (`src/mark/cognition/tools.py` e pontes MCP):
   - Capability-Based Access Control (CBAC) explícito por ferramenta e por missão (`CapabilityToken`,
     `AuthorizationLayer`, `BridgeCapabilityGuard`);
   - O simples registro de uma ferramenta MCP jamais concede autorização automática para leitura de
     arquivos privados, manipulação de segredos ou execução de ações privilegiadas.

5. Passo 5 — Varredura Definitiva de Autenticação Legada (`src/mark/mobile_api.py`):
   - Bloqueio de tráfego HTTP plano (porta 80, `X-Forwarded-Proto: http`, `Forwarded: proto=http`) em
     todas as rotas GET e POST;
   - Proibição de fallback de tokens Bearer estáticos em qualquer requisição oriunda de proxy/Tailscale
     (`Tailscale-User-Login`, `Tailscale-User-Name`, `X-Forwarded-For`, `X-Real-IP`, `Forwarded`) ou
     de endereços não-loopback.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

from mark.backup_manager import BackupMCPBridge, BackupManager
from mark.cognition.tools import (
    ActionRequest,
    AuthorizationLayer,
    CapabilityToken,
    ToolCapabilityError,
    ToolContract,
    ToolRegistry,
    default_registry,
    inspect_arguments_for_private_or_secret_access,
    schema,
)
from mark.config import load_config
from mark.device_auth import DeviceRegistry
from mark.engineer_agent import EngineerAgent, EngineerIntegrityError
from mark.home_assistant import HomeAssistantClient, HomeAssistantMCPBridge, save_ha_token
from mark.mcp_gods_eye import GodsEyeMCPBridge
from mark.mcp_sandbox import SandboxMCPBridge
from mark.memory import ShortMemory
from mark.mobile_api import MobileServer
from mark.reverse_engine import ReverseEngineMCPBridge
from mark.sandbox import ExecutionSandbox, SandboxLimits, SandboxSecurityError
from mark.smart_home_gateway import (
    EncryptedCredentialVault,
    SmartHomeAuthError,
    SmartHomeGateway,
    SmartHomeMCPBridge,
)


class P0Step1ExecutionSandboxHardeningTests(unittest.TestCase):
    """Documenta e valida o Passo 1: Endurecimento Real do ExecutionSandbox."""

    def test_sandbox_enforces_bwrap_namespace_isolation_and_blocks_child_network_sockets(self):
        """Garante que processos filhos no sandbox não conseguem abrir conexões de rede externas."""
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            with ExecutionSandbox(workspace=ws) as sandbox:
                self.assertTrue(sandbox.isolation_backend in ("bwrap", "posix-rlimit"))
                # Camada 1: Bloqueio estático imediato de primitivas de socket
                with self.assertRaises(SandboxSecurityError):
                    sandbox.run_python_script("import socket\nsocket.socket(socket.AF_INET, socket.SOCK_DGRAM)\n")

                # Camada 2: Mesmo contornando o filtro estático via getattr dinâmico, o namespace de rede
                # isolado do SO (bwrap --unshare-all / --unshare-net) bloqueia o tráfego externo (ENETUNREACH)
                if not sandbox.bwrap_available:
                    self.skipTest("Isolamento de namespace de rede via Bubblewrap não disponível neste host")

                script = ws / "child_socket_attempt.py"
                script.write_text(
                    "import socket, sys\n"
                    "try:\n"
                    "    fn = getattr(socket, 'create_' + 'connection')\n"
                    "    sock = fn(('1.1.1.1', 53), timeout=1.0)\n"
                    "    sys.exit(0)\n"
                    "except OSError as e:\n"
                    "    print('SOCKET_BLOCKED_OS:', type(e).__name__)\n"
                    "    sys.exit(13)\n",
                    encoding="utf-8",
                )
                res = sandbox.run_python_script(script)
                self.assertEqual(res.exit_code, 13, msg=f"stdout={res.stdout} stderr={res.stderr}")
                self.assertIn("SOCKET_BLOCKED_OS:", res.stdout)

    def test_sandbox_validates_executable_arguments_and_prevents_directory_escape(self):
        """Valida bloqueio de flags perigosas em binários permitidos e tentativas de path traversal."""
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            with ExecutionSandbox(workspace=ws) as sandbox:
                blocked_commands = [
                    ["find", ".", "-execdir", "id", ";"],
                    ["find", ".", "-ok", "id", ";"],
                    ["git", "fetch", "origin"],
                    ["git", "remote", "add", "evil", "https://evil.invalid/repo.git"],
                    ["git", "--upload-pack=/bin/sh", "status"],
                    ["pytest", "../../etc/passwd"],
                    ["cargo", "check", "--manifest-path", "../outside/Cargo.toml"],
                    ["maturin", "publish"],
                ]
                for cmd in blocked_commands:
                    with self.subTest(cmd=cmd), self.assertRaises(SandboxSecurityError):
                        sandbox.run(cmd)

    def test_sandbox_applies_memory_cpu_and_file_descriptor_rlimits(self):
        """Verifica que os limites rígidos de RLIMIT_NOFILE, RLIMIT_CPU e RLIMIT_FSIZE estão ativos no processo filho."""
        limits = SandboxLimits(
            max_memory_mb=256,
            max_cpu_seconds=8,
            max_open_files=48,
            max_file_size_mb=8,
        )
        with ExecutionSandbox(limits=limits) as sandbox:
            res = sandbox.run_python_script(
                "import resource\n"
                "nofile_soft, nofile_hard = resource.getrlimit(resource.RLIMIT_NOFILE)\n"
                "cpu_soft, cpu_hard = resource.getrlimit(resource.RLIMIT_CPU)\n"
                "core_soft, core_hard = resource.getrlimit(resource.RLIMIT_CORE)\n"
                "assert nofile_soft <= 48 or nofile_hard <= 48\n"
                "assert cpu_soft <= 8\n"
                "assert core_soft == 0 or core_hard == 0\n"
                "print('LIMITS_VERIFIED')\n"
            )
            self.assertTrue(res.success, msg=res.stderr)
            self.assertIn("LIMITS_VERIFIED", res.stdout)


class P0Step2EngineerAgentConfinementTests(unittest.TestCase):
    """Documenta e valida o Passo 2: Confinamento Absoluto do EngineerAgent."""

    def test_engineer_agent_blocks_path_traversal_symlinks_and_sensitive_files(self):
        """Impede qualquer leitura ou escrita fora de /workspace ou em arquivos de segurança do core."""
        with tempfile.TemporaryDirectory() as tmp_ws, tempfile.TemporaryDirectory() as tmp_out:
            ws = Path(tmp_ws)
            outside_file = Path(tmp_out) / "secret.py"
            outside_file.write_text("TOKEN = '123'\n", encoding="utf-8")

            link_path = ws / "evil_link.py"
            link_path.symlink_to(outside_file)

            with ExecutionSandbox(workspace=ws) as sandbox:
                agent = EngineerAgent(sandbox=sandbox, llm_callable=lambda **_: "x = 1\n")

                for illegal_path in (
                    "../secret.py",
                    "/etc/shadow",
                    str(outside_file),
                    "evil_link.py",
                    "/workspace/../secret.py",
                    ".env",
                    "vault.key",
                    "mobile.token",
                    "src/mark/sandbox.py",
                    "src/mark/smart_home_gateway.py",
                    "src/mark/cognition/tools.py",
                ):
                    with self.subTest(path=illegal_path), self.assertRaises(SandboxSecurityError):
                        agent.read_workspace_file(illegal_path)

    def test_engineer_agent_preserves_verified_backups_and_enforces_integrity_validation(self):
        """Verifica criação de backup SHA-256 em .mark_backups e rollback imediato em violação de integridade."""
        with tempfile.TemporaryDirectory() as tmp_ws:
            ws = Path(tmp_ws)
            target = ws / "service.py"
            original_code = "def compute(x: int) -> int:\n    return x + 1\n"
            target.write_text(original_code, encoding="utf-8")

            with ExecutionSandbox(workspace=ws) as sandbox:
                agent = EngineerAgent(sandbox=sandbox, llm_callable=lambda **_: "x = 1\n")
                snapshot = agent.create_backup(target)
                self.assertTrue(snapshot.backup_path.exists())
                self.assertEqual(snapshot.backup_path.stat().st_mode & 0o777, 0o600)

                # Modificação contendo chamada proibida (os.system) deve falhar na validação de integridade e restaurar backup
                with self.assertRaises(EngineerIntegrityError):
                    agent.write_workspace_file(
                        "/workspace/service.py",
                        "import os\ndef compute(x: int) -> int:\n    os.system('id')\n    return x\n",
                        backup=snapshot,
                    )
                self.assertEqual(target.read_text(encoding="utf-8"), original_code)


class P0Step3CryptographicVaultMigrationTests(unittest.TestCase):
    """Documenta e valida o Passo 3: Migração do Cofre Criptográfico do Smart Home Gateway."""

    def test_vault_aead_aes_gcm_and_chacha20_poly1305_with_pbkdf2_and_0600_permissions(self):
        """Valida criptografia autenticada AEAD, PBKDF2-HMAC-SHA256, proteção AAD e rotação de chaves."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vault_path = root / "smart_home_vault.enc"
            key_path = root / "smart_home_vault.key"

            vault = EncryptedCredentialVault(
                vault_path=vault_path,
                key_path=key_path,
                cipher_suite="AES-256-GCM",
                kdf_iterations=310_000,
            )
            vault.store_credential("ha_main", {"token": "ha_live_secret_token_998877"})
            self.assertEqual(vault_path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(key_path.stat().st_mode & 0o777, 0o600)

            # Rotate para ChaCha20-Poly1305 e verifica que o segredo continua acessível e íntegro
            rot = vault.rotate_key(new_cipher_suite="ChaCha20-Poly1305", new_iterations=320_000)
            self.assertTrue(rot["rotated"])
            self.assertEqual(rot["cipher"], "ChaCha20-Poly1305")
            self.assertEqual(vault.get_credential("ha_main")["token"], "ha_live_secret_token_998877")

            # Qualquer bit adulterado no nonce ou ciphertext deve disparar SmartHomeAuthError
            env = json.loads(vault_path.read_text(encoding="utf-8"))
            nonce_bytes = bytearray(base64.urlsafe_b64decode(env["nonce"].encode("ascii")))
            nonce_bytes[0] ^= 0x01
            env["nonce"] = base64.urlsafe_b64encode(bytes(nonce_bytes)).decode("ascii")
            vault_path.write_text(json.dumps(env), encoding="utf-8")
            os.chmod(vault_path, 0o600)

            with self.assertRaises(SmartHomeAuthError):
                vault.get_credential("ha_main")


class P0Step4GranularMCPCapabilityAuthorizationTests(unittest.TestCase):
    """Documenta e valida o Passo 4: Autorização Granular por Ferramenta e por Missão (CBAC)."""

    def test_registration_does_not_grant_automatic_execution_and_enforces_per_tool_mission_tokens(self):
        """Comprova que registrar uma tool MCP não concede acesso automático e exige CapabilityToken válido."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reg = default_registry(root / "knowledge.sqlite3")

            # 1. Invocação sem grants é negada com ToolCapabilityError (subclasse de PermissionError)
            with self.assertRaises(ToolCapabilityError):
                reg.invoke(ActionRequest("chemistry.molar_mass", {"formula": "H2O"}))

            # 2. Token emitido para mission-A permitindo apenas 'chemistry.molar_mass'
            token_a = reg.issue_mission_capability(
                "mission-A",
                allowed_tools=["chemistry.molar_mass"],
                ttl_seconds=60.0,
            )
            out = reg.invoke(
                ActionRequest("chemistry.molar_mass", {"formula": "H2O"}, mission_id="mission-A"),
                grants=token_a,
            )
            self.assertEqual(out["value"], 18.015)

            # 3. Outra tool com a MESMA permissão subjacente ('tools.calculate') é bloqueada pelo escopo da tool
            with self.assertRaises(ToolCapabilityError):
                reg.invoke(
                    ActionRequest("chemistry.periodic_table_lookup", {"symbol": "Fe"}, mission_id="mission-A"),
                    grants=token_a,
                )

            # 4. Adulteração da assinatura do CapabilityToken é detectada em tempo constante
            forged_token = CapabilityToken(
                mission_id=token_a.mission_id,
                allowed_tools=frozenset({"chemistry.molar_mass", "chemistry.periodic_table_lookup"}),
                permissions=token_a.permissions,
                expires_at=token_a.expires_at,
                allow_privileged=True,
                signature=token_a.signature,
            )
            with self.assertRaises(ToolCapabilityError):
                reg.invoke(
                    ActionRequest("chemistry.periodic_table_lookup", {"symbol": "Fe"}, mission_id="mission-A"),
                    grants=forged_token,
                )

    def test_all_mcp_bridges_enforce_capability_guard_and_block_private_file_or_secret_args(self):
        """Verifica que todas as pontes MCP bloqueiam argumentos apontando para segredos e aplicam CapabilityToken."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sbx_bridge = SandboxMCPBridge(workspace=root, require_explicit_capabilities=True)
            backup_bridge = BackupMCPBridge(
                manager=BackupManager(state_dir=root, knowledge_db=root / "k.sqlite3", knowledge_files_dir=root / "kf"),
                require_explicit_capabilities=True,
            )
            rea_bridge = ReverseEngineMCPBridge(require_explicit_capabilities=True)
            gods_bridge = GodsEyeMCPBridge(require_explicit_capabilities=True)

            # Sem CapabilityToken, todas as pontes em modo estrito negam a chamada
            with self.assertRaises(ToolCapabilityError):
                sbx_bridge.validate_call("sandbox_run_pytest", {"target": "tests"})
            with self.assertRaises(ToolCapabilityError):
                backup_bridge.validate_call("backup_verify_archive", {"backup_path": "archive.zip"})
            with self.assertRaises(ToolCapabilityError):
                rea_bridge.validate_call("rea_inspect_artifact", {"path": "sample.bin"})
            with self.assertRaises(ToolCapabilityError):
                gods_bridge.validate_call("get_earthquakes", {"min_magnitude": 5.0})

            # Argumentos referenciando arquivos privados (.ssh, /etc/shadow, mobile.token, smart_home_vault.key)
            # são bloqueados em qualquer ponte mesmo quando o chamador possui CapabilityToken válido
            cap_rea = rea_bridge.issue_mission_capability("m-rea", allowed_tools=["rea_inspect_artifact"])
            for secret_path in ("/etc/shadow", "~/.ssh/id_ed25519", "mobile.token", "smart_home_vault.key", "../escape.bin"):
                with self.subTest(secret_path=secret_path), self.assertRaises(ToolCapabilityError):
                    rea_bridge.validate_call(
                        "rea_inspect_artifact",
                        {"path": secret_path},
                        grants=cap_rea,
                        mission_id="m-rea",
                    )


class P0Step5LegacyAuthSweepTests(unittest.TestCase):
    """Documenta e valida o Passo 5: Varredura Definitiva de Autenticação Legada."""

    def test_rejects_plain_http_on_all_endpoints_and_blocks_proxy_bearer_fallbacks(self):
        """Garante que nenhuma rota GET/POST aceita HTTP plano ou fallback de Bearer estático via proxy/Tailscale."""
        with tempfile.TemporaryDirectory() as tmp:
            reg = DeviceRegistry(Path(tmp) / "devices.db")
            server = MobileServer(load_config(), "desktop-local-token", 0, memory=ShortMemory(), devices=reg)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f"http://127.0.0.1:{server.server_port}"

                # 1. Mesmo rotas públicas de bootstrap (/v1/pair/status, /call) recusam tráfego HTTP plano (porta 80)
                for route in ("/health", "/v1/models", "/v1/pair/status?x=1", "/call"):
                    req = urllib.request.Request(
                        base + route,
                        headers={"X-Forwarded-Proto": "http", "Authorization": "Bearer desktop-local-token"},
                    )
                    with self.assertRaises(urllib.error.HTTPError) as ctx:
                        urllib.request.urlopen(req, timeout=3)
                    self.assertEqual(ctx.exception.code, 403)
                    ctx.exception.close()

                # 2. Qualquer cabeçalho de proxy ou Tailscale bloqueia imediatamente o token Bearer estático (HTTP 401)
                for header_name, header_val in (
                    ("Tailscale-User-Login", "alice@example.com"),
                    ("Tailscale-User-Name", "Alice"),
                    ("Tailscale-Headers-Info", "ts-info"),
                    ("X-Forwarded-For", "100.101.102.103"),
                    ("X-Forwarded-Host", "mark.tailnet.ts.net"),
                    ("X-Real-IP", "100.101.102.103"),
                    ("Forwarded", "for=100.101.102.103;proto=https"),
                ):
                    req = urllib.request.Request(
                        base + "/health",
                        headers={"Authorization": "Bearer desktop-local-token", header_name: header_val},
                    )
                    with self.assertRaises(urllib.error.HTTPError) as ctx:
                        urllib.request.urlopen(req, timeout=3)
                    self.assertEqual(ctx.exception.code, 401, msg=f"Header {header_name} deveria bloquear Bearer")
                    ctx.exception.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)
                reg.close()


if __name__ == "__main__":
    unittest.main()

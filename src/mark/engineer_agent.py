"""Agente Autônomo de Engenharia de Software (Fase 6 - The Execution Core / Hardening P0).

Implementa o loop fechado de auto-correção:
Observe (Análise) -> Plan/Execute (Modificação) -> Test (Validação no ExecutionSandbox) -> Fix (Auto-Correção até 3 tentativas),
com confinamento absoluto ao diretório raiz autorizado (`/workspace` ou raiz do workspace do projeto MARK),
verificação estrita contra path traversal/symlinks/arquivos protegidos, backups automáticos verificados
e validação criptográfica e sintática de integridade em modificações.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field, asdict
import difflib
import hashlib
import json
import logging
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import sys
import time
from typing import Any, Callable, Sequence

from .cognition.brain_routing import route_request
from .cognition.providers import ModelRequest, get_provider
from .config import load_config
from .sandbox import ExecutionSandbox, SandboxResult, SandboxSecurityError

log = logging.getLogger("mark.engineer_agent")

MAX_RETRIES_DEFAULT = 3
MAX_ENGINEER_FILE_BYTES = 1024 * 1024  # 1 MiB
DEFAULT_MARK_PROJECT_ROOT = Path(__file__).resolve().parents[2]
CANONICAL_WORKSPACE_MOUNT = Path("/workspace")

_CODE_BLOCK_RE = re.compile(r"```(?:[A-Za-z0-9_+-]+)?\s*\n(.*?)```", re.DOTALL)

PROTECTED_WORKSPACE_PARTS = frozenset({
    ".git",
    ".ssh",
    ".gnupg",
    ".aws",
    ".config",
    ".local",
    ".venv",
    "venv",
    "__pycache__",
})

PROTECTED_WORKSPACE_FILENAMES = frozenset({
    ".env",
    ".netrc",
    ".npmrc",
    "id_rsa",
    "id_ed25519",
    "mobile.token",
    "home_assistant.token",
    "smart_home_vault.enc",
    "smart_home_vault.key",
    "mobile-devices.db",
})

PROTECTED_WORKSPACE_SUFFIXES = frozenset({
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    ".token",
    ".enc",
    ".sqlite3",
    ".db",
})

PROTECTED_CORE_SECURITY_PATHS = frozenset({
    "src/mark/sandbox.py",
    "src/mark/engineer_agent.py",
    "src/mark/engineer_workspace.py",
    "src/mark/device_auth.py",
    "src/mark/mobile_api.py",
    "src/mark/smart_home_gateway.py",
    "src/mark/cognition/tools.py",
    "src/mark/evolution/policy.py",
})

FORBIDDEN_MODIFICATION_PATTERNS = (
    re.compile(r"\bos\s*\.\s*(?:system|popen|exec\w*|spawn\w*|setuid|setgid|chown|chroot)\b"),
    re.compile(r"\bsubprocess\s*\.\s*(?:Popen|run|call|check_output|check_call)\b[^\n]*shell\s*=\s*True"),
    re.compile(r"(?:/etc/shadow|/etc/passwd|\.ssh/id_|mobile\.token|smart_home_vault\.key)"),
)


class EngineerIntegrityError(SandboxSecurityError):
    """Exceção levantada quando uma modificação viola a integridade estrutural, hash ou política de segurança."""


@dataclass(frozen=True)
class BackupSnapshot:
    """Snapshot verificável de backup automático antes de modificações do EngineerAgent."""

    relative_path: str
    existed: bool
    original_content: str
    original_sha256: str
    backup_file_path: str
    created_at: float

    @property
    def backup_path(self) -> Path:
        return Path(self.backup_file_path) if self.backup_file_path else Path()


@dataclass(frozen=True)
class AttemptRecord:
    attempt: int
    route_decision: str
    modified: bool
    diff: str
    lint_passed: bool
    test_passed: bool
    exit_code: int
    error_trace: str
    before_sha256: str = ""
    after_sha256: str = ""
    integrity_verified: bool = True


@dataclass
class EngineeringReport:
    task: str
    target_file: str
    success: bool
    attempts: int
    max_retries: int
    route_decision: str
    final_code: str
    history: list[AttemptRecord] = field(default_factory=list)
    message: str = ""
    workspace_root: str = ""
    backup_restored: bool = False
    integrity_sha256: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "target_file": self.target_file,
            "success": self.success,
            "attempts": self.attempts,
            "max_retries": self.max_retries,
            "route_decision": self.route_decision,
            "final_code": self.final_code,
            "history": [asdict(item) for item in self.history],
            "message": self.message,
            "workspace_root": self.workspace_root,
            "backup_restored": self.backup_restored,
            "integrity_sha256": self.integrity_sha256,
        }


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def resolve_authorized_workspace_root(
    workspace: str | Path | None = None,
    sandbox: ExecutionSandbox | None = None,
) -> Path:
    """Resolve e valida o diretório raiz autorizado para o EngineerAgent (/workspace ou raiz do projeto MARK)."""
    if workspace is not None:
        raw = Path(workspace).expanduser()
    elif sandbox is not None:
        raw = Path(sandbox.workspace)
    elif CANONICAL_WORKSPACE_MOUNT.is_dir() and not CANONICAL_WORKSPACE_MOUNT.is_symlink():
        raw = CANONICAL_WORKSPACE_MOUNT
    else:
        raw = DEFAULT_MARK_PROJECT_ROOT

    if raw.is_symlink():
        raise SandboxSecurityError("O diretório raiz do EngineerAgent não pode ser um link simbólico.")
    resolved = raw.resolve()
    if not resolved.exists() or not resolved.is_dir():
        raise ValueError(f"Diretório raiz do EngineerAgent inexistente ou inválido: {raw}")
    if resolved in (Path("/"), Path("/etc"), Path("/usr"), Path("/var"), Path("/root"), Path.home()):
        raise SandboxSecurityError(f"Diretório raiz amplo ou sensível demais para o EngineerAgent: {resolved}")
    return resolved


def extract_code_from_llm_response(response_text: str) -> str:
    """Extrai o código-fonte limpo de uma resposta do LLM (bloco markdown, JSON ou texto puro)."""
    if not isinstance(response_text, str) or not response_text.strip():
        raise ValueError("O modelo retornou uma resposta vazia.")

    stripped = response_text.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        try:
            parsed = json.loads(stripped)
            if isinstance(parsed, dict) and isinstance(parsed.get("code"), str):
                return parsed["code"]
        except json.JSONDecodeError:
            pass

    matches = _CODE_BLOCK_RE.findall(response_text)
    if matches:
        return matches[0]

    return stripped


class EngineerAgent:
    """Executor autônomo de tarefas de engenharia com confinamento absoluto ao workspace (/workspace),
    prevenção de path traversal, backups automáticos e validação de integridade.
    """

    def __init__(
        self,
        sandbox: ExecutionSandbox | None = None,
        *,
        workspace_root: str | Path | None = None,
        llm_callable: Callable[..., str] | None = None,
        router_callable: Callable[[str], str] | None = None,
        max_retries: int = MAX_RETRIES_DEFAULT,
        config: dict[str, Any] | None = None,
        allow_core_security_edits: bool = False,
    ):
        if max_retries < 1 or max_retries > 10:
            raise ValueError("max_retries deve estar entre 1 e 10.")
        resolved_root = resolve_authorized_workspace_root(workspace=workspace_root, sandbox=sandbox)
        if sandbox is not None:
            if sandbox.workspace.resolve() != resolved_root:
                raise SandboxSecurityError(
                    f"Conflito de confinamento: workspace do sandbox ({sandbox.workspace}) difere do workspace_root ({resolved_root})."
                )
            self.sandbox = sandbox
        else:
            self.sandbox = ExecutionSandbox(workspace=resolved_root)

        self.workspace_root: Path = self.sandbox.workspace.resolve()
        self.max_retries = min(int(max_retries), MAX_RETRIES_DEFAULT)
        self.cfg = config
        self.allow_core_security_edits = bool(allow_core_security_edits)
        self._llm_callable = llm_callable
        self._router_callable = router_callable or route_request
        self._backups: dict[str, BackupSnapshot] = {}

    def _normalize_confined_relpath(self, relative_or_path: str | Path) -> str:
        """Valida estritamente o caminho contra path traversal (..), bytes nulos, barras invertidas e escapes."""
        if isinstance(relative_or_path, Path):
            raw_str = str(relative_or_path)
        elif isinstance(relative_or_path, str):
            raw_str = relative_or_path.strip()
        else:
            raise SandboxSecurityError("Caminho de arquivo inválido.")

        if not raw_str or len(raw_str) > 512 or "\x00" in raw_str or "\\" in raw_str:
            raise SandboxSecurityError("Caminho de arquivo vazio, longo demais ou contendo caracteres ilegais.")

        # Suporte transparente ao prefixo virtual /workspace/... mapeando para self.workspace_root
        if raw_str == "/workspace" or raw_str.startswith("/workspace/"):
            rel_candidate = raw_str.removeprefix("/workspace").lstrip("/")
            if not rel_candidate:
                raise SandboxSecurityError("O caminho deve apontar para um arquivo dentro de /workspace.")
            pure = PurePosixPath(rel_candidate)
            if any(part in {"", ".", ".."} for part in pure.parts):
                raise SandboxSecurityError(f"Path traversal detectado em caminho /workspace: {raw_str}")
            return pure.as_posix()

        raw_path = Path(raw_str)
        if raw_path.is_absolute():
            # Se for caminho absoluto real, NÃO pode conter '..' na string original e deve estar dentro de workspace_root
            if ".." in PurePosixPath(raw_str).parts:
                raise SandboxSecurityError(f"Path traversal ('..') proibido em caminho absoluto: {raw_str}")
            if raw_path.is_symlink():
                raise SandboxSecurityError("Links simbólicos não podem ser acessados pelo EngineerAgent.")
            resolved_abs = raw_path.resolve()
            try:
                rel_from_root = resolved_abs.relative_to(self.workspace_root)
            except ValueError as exc:
                raise SandboxSecurityError(
                    f"Acesso negado: o caminho '{raw_str}' escapa do diretório raiz autorizado ({self.workspace_root})."
                ) from exc
            pure = PurePosixPath(rel_from_root.as_posix())
        else:
            pure = PurePosixPath(raw_str)
            if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
                raise SandboxSecurityError(
                    f"Tentativa de path traversal bloqueada no EngineerAgent: '{raw_str}'"
                )

        if not pure.parts:
            raise SandboxSecurityError("Caminho de arquivo inválido.")

        for part in pure.parts:
            lowered = part.casefold()
            if lowered in PROTECTED_WORKSPACE_PARTS or lowered in PROTECTED_WORKSPACE_FILENAMES:
                raise SandboxSecurityError(f"Acesso bloqueado a diretório ou arquivo protegido no workspace: {part}")
            if Path(part).suffix.casefold() in PROTECTED_WORKSPACE_SUFFIXES:
                raise SandboxSecurityError(f"Acesso bloqueado a arquivo de chave/credencial/banco protegido: {part}")

        normalized = pure.as_posix()
        if not self.allow_core_security_edits and normalized in PROTECTED_CORE_SECURITY_PATHS:
            raise SandboxSecurityError(
                f"Modificação autônoma de módulo crítico de segurança bloqueada: {normalized}"
            )
        return normalized

    def _resolve_workspace_file(
        self,
        relative_or_path: str | Path,
        *,
        must_exist: bool = False,
        writing: bool = False,
    ) -> Path:
        """Resolve um arquivo garantindo que cada componente do caminho permanece dentro de `self.workspace_root` sem symlinks."""
        normalized = self._normalize_confined_relpath(relative_or_path)
        pure = PurePosixPath(normalized)

        cursor = self.workspace_root
        check_parts = pure.parts[:-1] if writing else pure.parts
        for part in check_parts:
            cursor = cursor / part
            if cursor.is_symlink():
                raise SandboxSecurityError(f"Symlink intermediário detectado e bloqueado no workspace: {cursor}")

        candidate = self.workspace_root.joinpath(*pure.parts)
        if candidate.is_symlink():
            raise SandboxSecurityError("Links simbólicos não podem ser lidos ou editados pelo EngineerAgent.")

        resolved_parent = candidate.parent.resolve(strict=False)
        try:
            resolved_parent.relative_to(self.workspace_root)
        except ValueError as exc:
            raise SandboxSecurityError("O diretório pai do arquivo alvo escapa do workspace isolado.") from exc

        resolved = candidate.resolve(strict=False)
        try:
            resolved.relative_to(self.workspace_root)
        except ValueError as exc:
            raise SandboxSecurityError("O arquivo alvo escapa do workspace isolado do sandbox.") from exc

        if must_exist and not resolved.is_file():
            raise FileNotFoundError(f"Arquivo alvo não encontrado no workspace: {relative_or_path}")
        return resolved

    def create_backup(self, target_file: str | Path) -> BackupSnapshot:
        """Cria e persiste um backup automático com hash SHA-256 e permissão 0600 antes de alterar o arquivo."""
        path = self._resolve_workspace_file(target_file, must_exist=False, writing=True)
        rel_path = str(path.relative_to(self.workspace_root))
        existed = path.is_file()
        content = ""
        if existed:
            raw_bytes = path.read_bytes()
            if len(raw_bytes) > MAX_ENGINEER_FILE_BYTES:
                raise SandboxSecurityError("Arquivo excede o tamanho máximo seguro de 1 MiB para o EngineerAgent.")
            content = raw_bytes.decode("utf-8")

        digest = _sha256_text(content)
        backup_dir = self.workspace_root / ".mark_backups"
        if backup_dir.is_symlink():
            raise SandboxSecurityError("Diretório de backup do EngineerAgent não pode ser um symlink.")
        backup_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        backup_dir.chmod(0o700)

        safe_slug = re.sub(r"[^A-Za-z0-9_.-]", "_", rel_path)
        backup_file = backup_dir / f"{safe_slug}.{digest[:12]}.bak"
        if existed:
            backup_file.write_text(content, encoding="utf-8")
            backup_file.chmod(0o600)

        snapshot = BackupSnapshot(
            relative_path=rel_path,
            existed=existed,
            original_content=content,
            original_sha256=digest,
            backup_file_path=str(backup_file) if existed else "",
            created_at=time.time(),
        )
        self._backups[rel_path] = snapshot
        return snapshot

    def restore_backup(self, target_file: str | Path) -> bool:
        """Restaura o backup automático e valida que o hash SHA-256 restaurado é idêntico ao original."""
        path = self._resolve_workspace_file(target_file, must_exist=False, writing=True)
        rel_path = str(path.relative_to(self.workspace_root))
        snapshot = self._backups.get(rel_path)
        if snapshot is None:
            return False

        if not snapshot.existed:
            if path.exists():
                path.unlink()
            return True

        restored_text = snapshot.original_content
        if snapshot.backup_file_path:
            bak_path = Path(snapshot.backup_file_path)
            if bak_path.is_file() and not bak_path.is_symlink():
                disk_text = bak_path.read_text(encoding="utf-8")
                if _sha256_text(disk_text) != snapshot.original_sha256:
                    raise EngineerIntegrityError("Backup em disco foi corrompido ou adulterado (divergência SHA-256).")
                restored_text = disk_text

        if _sha256_text(restored_text) != snapshot.original_sha256:
            raise EngineerIntegrityError("Falha na verificação de integridade SHA-256 do backup original.")

        self._atomic_write(path, restored_text)
        verified_disk = path.read_text(encoding="utf-8")
        if _sha256_text(verified_disk) != snapshot.original_sha256:
            raise EngineerIntegrityError("Integridade pós-restauração falhou para o arquivo alvo.")
        return True

    def validate_modification_integrity(self, target_path: Path, new_code: str) -> str:
        """Valida a integridade de segurança, tamanho, codificação e AST antes e após a escrita."""
        if not isinstance(new_code, str):
            raise EngineerIntegrityError("O código modificado deve ser uma string UTF-8 válida.")
        if "\x00" in new_code:
            raise EngineerIntegrityError("Bytes nulos não são permitidos em modificações de código.")
        encoded = new_code.encode("utf-8")
        if len(encoded) > MAX_ENGINEER_FILE_BYTES:
            raise EngineerIntegrityError("O código modificado excede o limite máximo seguro de 1 MiB.")

        for pattern in FORBIDDEN_MODIFICATION_PATTERNS:
            if pattern.search(new_code):
                raise EngineerIntegrityError(
                    "Modificação rejeitada pela validação de integridade: padrão de execução insegura ou acesso a segredo detectado."
                )

        if target_path.suffix == ".py":
            try:
                tree = ast.parse(new_code, filename=target_path.name)
            except SyntaxError:
                # Erros de sintaxe normais do LLM são reportados na fase validate() para realimentação do loop,
                # mas verificamos a árvore AST quando sintaticamente válida contra imports/chamadas proibidas.
                tree = None
            if tree is not None:
                for node in ast.walk(tree):
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                        if (
                            isinstance(node.func.value, ast.Name)
                            and node.func.value.id == "os"
                            and node.func.attr in {"system", "popen", "execl", "execle", "execlp", "execv", "execve", "execvp", "setuid", "chroot"}
                        ):
                            raise EngineerIntegrityError(
                                f"Chamada proibida detectada na AST durante validação de integridade: os.{node.func.attr}"
                            )

        return _sha256_text(new_code)

    def _atomic_write(self, path: Path, content: str) -> None:
        """Realiza escrita atômica via arquivo temporário no mesmo diretório com fsync e verificação de hash."""
        path.parent.mkdir(parents=True, exist_ok=True)
        mode = 0o644
        if path.exists():
            info = path.lstat()
            if not os.path.samestat(info, path.stat()):
                raise SandboxSecurityError("Symlink detectado no destino durante escrita.")
            mode = info.st_mode & 0o777

        temp_file = path.parent / f".engineer_tmp_{os.getpid()}_{secrets.token_hex(8)}"
        expected_sha = _sha256_text(content)
        try:
            fd = os.open(
                temp_file,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temp_file, mode)
            os.replace(temp_file, path)
        except Exception:
            try:
                temp_file.unlink()
            except FileNotFoundError:
                pass
            raise

        actual_sha = _sha256_text(path.read_text(encoding="utf-8"))
        if actual_sha != expected_sha:
            raise EngineerIntegrityError("Falha de integridade pós-escrita: checksum SHA-256 em disco diverge do esperado.")

    def _resolve_workspace_path(
        self,
        relative_or_path: str | Path,
        *,
        must_exist: bool = False,
        writing: bool = False,
    ) -> Path:
        return self._resolve_workspace_file(relative_or_path, must_exist=must_exist, writing=writing)

    def read_workspace_file(self, target_file: str | Path) -> str:
        """Lê um arquivo confinado ao workspace autorizado após validação anti-traversal e anti-symlink."""
        path = self._resolve_workspace_file(target_file, must_exist=True, writing=False)
        raw = path.read_bytes()
        if len(raw) > MAX_ENGINEER_FILE_BYTES:
            raise SandboxSecurityError("Arquivo alvo excede o limite de leitura de 1 MiB.")
        return raw.decode("utf-8")

    def write_workspace_file(
        self,
        target_file: str | Path,
        new_code: str,
        *,
        backup: BackupSnapshot | None = None,
    ) -> Path:
        """Escreve um arquivo confinado ao workspace com validação de integridade e rollback automático em caso de falha."""
        path = self._resolve_workspace_file(target_file, must_exist=False, writing=True)
        rel_path = str(path.relative_to(self.workspace_root))
        if backup is not None:
            self._backups[rel_path] = backup
        elif rel_path not in self._backups:
            self.create_backup(rel_path)
        try:
            self.validate_modification_integrity(path, new_code)
            self._atomic_write(path, new_code)
        except Exception:
            self.restore_backup(rel_path)
            raise
        return path

    def observe(self, target_file: str | Path, initial_error: str = "") -> dict[str, str]:
        """Fase 1 (Análise): Lê o arquivo alvo confinado ao workspace e estrutura o contexto inicial."""
        path = self._resolve_workspace_file(target_file, must_exist=False, writing=False)
        if path.is_file():
            raw = path.read_bytes()
            if len(raw) > MAX_ENGINEER_FILE_BYTES:
                raise SandboxSecurityError("Arquivo alvo excede o limite de leitura de 1 MiB.")
            content = raw.decode("utf-8")
        else:
            content = ""
        rel_path = str(path.relative_to(self.workspace_root))
        return {
            "target_file": rel_path,
            "content": content,
            "sha256": _sha256_text(content),
            "initial_error": initial_error.strip(),
        }

    def _call_llm(
        self,
        task: str,
        target_file: str,
        current_code: str,
        error_trace: str,
        attempt: int,
        route_decision: str,
    ) -> str:
        prompt = (
            f"TAREFA DE ENGENHARIA (Tentativa {attempt}/{self.max_retries} | Rota: {route_decision}):\n"
            f"{task}\n\n"
            f"ARQUIVO ALVO: {target_file}\n"
            f"CÓDIGO ATUAL:\n```python\n{current_code}\n```\n"
        )
        if error_trace:
            prompt += (
                f"\nERRO / STACK TRACE DA VALIDAÇÃO ANTERIOR:\n"
                f"```\n{error_trace}\n```\n"
                "Analise o stack trace exato acima e corrija o código para que a compilação e os testes passem.\n"
            )
        prompt += "\nRetorne o código completo corrigido dentro de um bloco ```python ... ```."

        if self._llm_callable is not None:
            try:
                return self._llm_callable(
                    prompt=prompt,
                    task=task,
                    target_file=target_file,
                    current_code=current_code,
                    error_trace=error_trace,
                    attempt=attempt,
                    route_decision=route_decision,
                )
            except TypeError:
                return self._llm_callable(prompt)

        cfg = self.cfg or load_config()
        provider = get_provider(cfg)
        system_prompt = (
            "Você é o EngineerAgent autônomo do MARK. "
            "Responda apenas com o código corrigido completo dentro de um bloco markdown."
        )
        response = provider.generate(ModelRequest(prompt=prompt, system=system_prompt))
        return response.text

    def apply_modification(self, target_file: str | Path, new_code: str) -> tuple[bool, str]:
        """Fase 2 (Modificação): Valida integridade, preserva backup automático e aplica alteração atômica no workspace."""
        path = self._resolve_workspace_file(target_file, must_exist=False, writing=True)
        rel_path = str(path.relative_to(self.workspace_root))

        if rel_path not in self._backups:
            self.create_backup(rel_path)

        self.validate_modification_integrity(path, new_code)

        old_code = path.read_text(encoding="utf-8") if path.is_file() else ""
        if old_code == new_code:
            return False, ""
        diff = "".join(
            difflib.unified_diff(
                old_code.splitlines(keepends=True),
                new_code.splitlines(keepends=True),
                fromfile=f"a/{path.name}",
                tofile=f"b/{path.name}",
            )
        )
        self._atomic_write(path, new_code)
        return True, diff

    def validate(
        self,
        target_file: str | Path,
        *,
        test_target: str | Path | Sequence[str] | None = None,
        test_command: str | Sequence[str] | None = None,
    ) -> tuple[bool, bool, SandboxResult]:
        """Fase 3 (Validação): Roda o linter/compilador e a suíte de testes dentro do ExecutionSandbox."""
        path = self._resolve_workspace_file(target_file, must_exist=True, writing=False)
        rel_target = str(path.relative_to(self.workspace_root))

        if path.suffix == ".py":
            lint_res = self.sandbox.run([sys.executable, "-I", "-m", "py_compile", rel_target])
            if not lint_res.success:
                return False, False, lint_res
        elif path.suffix == ".rs" or path.name == "Cargo.toml":
            manifest = path if path.name == "Cargo.toml" else "src-rust/mark_core/Cargo.toml"
            lint_res = self.sandbox.run_rust_build(mode="cargo_check", manifest_path=manifest)
            if not lint_res.success:
                return False, False, lint_res
        else:
            lint_res = SandboxResult(command=["syntax_check", rel_target], stdout="OK", stderr="", exit_code=0)

        if test_command is not None:
            test_res = self.sandbox.run(test_command)
            return True, test_res.success, test_res

        if test_target is not None:
            if isinstance(test_target, Path):
                resolved_test = self._resolve_workspace_file(test_target, must_exist=True, writing=False)
                rel_test = str(resolved_test.relative_to(self.workspace_root))
                test_res = self.sandbox.run_pytest(rel_test)
            elif isinstance(test_target, str):
                resolved_test = self._resolve_workspace_file(test_target.split("::")[0], must_exist=True, writing=False)
                rel_test = str(resolved_test.relative_to(self.workspace_root))
                if "::" in test_target:
                    rel_test = f"{rel_test}::{test_target.split('::', 1)[1]}"
                test_res = self.sandbox.run_pytest(rel_test)
            else:
                test_res = self.sandbox.run_pytest(test_target)
            return True, test_res.success, test_res

        tests_dir = self.workspace_root / "tests"
        if tests_dir.is_dir():
            test_res = self.sandbox.run_pytest("tests")
            return True, test_res.success, test_res

        if path.suffix == ".py":
            exec_res = self.sandbox.run_python_script(rel_target)
            return True, exec_res.success, exec_res

        return True, lint_res.success, lint_res

    def solve(
        self,
        task: str,
        target_file: str | Path,
        *,
        test_target: str | Path | Sequence[str] | None = None,
        test_command: str | Sequence[str] | None = None,
        initial_error: str = "",
    ) -> EngineeringReport:
        """Executa o loop completo Observe -> Plan -> Execute -> Test -> Fix (máximo de 3 tentativas)."""
        if not isinstance(task, str) or not task.strip():
            raise ValueError("A descrição da tarefa de engenharia não pode ser vazia.")

        observation = self.observe(target_file, initial_error=initial_error)
        rel_file = observation["target_file"]
        current_code = observation["content"]
        snapshot = self.create_backup(rel_file)
        error_trace = observation["initial_error"]

        route_decision = self._router_callable(f"{task}\n{error_trace}".strip())
        history: list[AttemptRecord] = []

        for attempt in range(1, self.max_retries + 1):
            log.info("EngineerAgent tentativa %d/%d para %s (rota=%s)", attempt, self.max_retries, rel_file, route_decision)
            before_sha = _sha256_text(current_code)
            raw_reply = self._call_llm(
                task=task,
                target_file=rel_file,
                current_code=current_code,
                error_trace=error_trace,
                attempt=attempt,
                route_decision=route_decision,
            )
            candidate_code = extract_code_from_llm_response(raw_reply)
            try:
                modified, diff = self.apply_modification(rel_file, candidate_code)
            except EngineerIntegrityError:
                self.restore_backup(rel_file)
                raise

            current_code = candidate_code
            after_sha = _sha256_text(current_code)

            lint_ok, test_ok, validation_res = self.validate(
                rel_file,
                test_target=test_target,
                test_command=test_command,
            )

            combined_trace = (validation_res.stderr + "\n" + validation_res.stdout).strip()
            history.append(
                AttemptRecord(
                    attempt=attempt,
                    route_decision=route_decision,
                    modified=modified,
                    diff=diff,
                    lint_passed=lint_ok,
                    test_passed=test_ok,
                    exit_code=validation_res.exit_code,
                    error_trace="" if (lint_ok and test_ok) else combined_trace,
                    before_sha256=before_sha,
                    after_sha256=after_sha,
                    integrity_verified=True,
                )
            )

            if lint_ok and test_ok:
                return EngineeringReport(
                    task=task,
                    target_file=rel_file,
                    success=True,
                    attempts=attempt,
                    max_retries=self.max_retries,
                    route_decision=route_decision,
                    final_code=current_code,
                    history=history,
                    message=f"Tarefa concluída e validada com sucesso na tentativa {attempt}/{self.max_retries}.",
                    workspace_root=str(self.workspace_root),
                    backup_restored=False,
                    integrity_sha256=after_sha,
                )

            error_trace = combined_trace or f"Falha na validação com código de saída {validation_res.exit_code}."
            log.warning("Tentativa %d falhou na validação. Realimentando LLM com erro.", attempt)

        restored = self.restore_backup(rel_file)

        return EngineeringReport(
            task=task,
            target_file=rel_file,
            success=False,
            attempts=len(history),
            max_retries=self.max_retries,
            route_decision=route_decision,
            final_code=current_code,
            history=history,
            message=f"Limite máximo de {self.max_retries} tentativas atingido sem aprovação nos testes.",
            workspace_root=str(self.workspace_root),
            backup_restored=restored,
            integrity_sha256=snapshot.original_sha256,
        )


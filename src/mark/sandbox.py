"""Módulo de Isolamento Seguro de Execução (The Execution Core - Fase 6 / Hardening P0).

Fornece a classe ExecutionSandbox para execução estritamente isolada de comandos
de shell controlados, scripts Python e compilações Rust (cargo build / maturin develop),
combinando isolamento a nível de sistema operacional (Bubblewrap / namespaces Linux),
limites rígidos de recursos (CPU, Memória, File Descriptors, Tamanho de Arquivo, Processos)
e validação estrita de executáveis e argumentos.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
import functools
import logging
import os
from pathlib import Path, PurePath
import re
import resource
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from typing import Any, Mapping, Sequence

log = logging.getLogger("mark.sandbox")

DEFAULT_TIMEOUT_SECONDS = 30
MAX_TIMEOUT_SECONDS = 30
MAX_OUTPUT_BYTES = 128 * 1024

DEFAULT_MAX_MEMORY_BYTES = 1024 * 1024 * 1024  # 1 GiB
MAX_MEMORY_BYTES_LIMIT = 2 * 1024 * 1024 * 1024  # 2 GiB
DEFAULT_MAX_CPU_SECONDS = 30
MAX_CPU_SECONDS_LIMIT = 60
DEFAULT_MAX_OPEN_FILES = 128
MAX_OPEN_FILES_LIMIT = 256
DEFAULT_MAX_FILE_SIZE_BYTES = 32 * 1024 * 1024  # 32 MiB
DEFAULT_MAX_PROCESSES = 64

SENSITIVE_ENV_PATTERNS = re.compile(
    r"(?i)(?:TOKEN|KEY|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTH|BEARER|COOKIE|PRIVATE|CERT|PIN|SESSION)"
)

SAFE_ENV_KEYS = ("PATH", "LANG", "LC_ALL")

ALLOWED_BINARIES = frozenset({
    "python",
    "python3",
    Path(sys.executable).name,
    "pytest",
    "cargo",
    "rustc",
    "maturin",
    "ruff",
    "flake8",
    "mypy",
    "git",
    "echo",
    "ls",
    "cat",
    "head",
    "tail",
    "wc",
    "grep",
    "find",
    "true",
    "false",
    "uname",
    "pwd",
    "env",
    "printenv",
    "file",
    "strings",
    "nm",
    "objdump",
    "readelf",
    "sha256sum",
})

FORBIDDEN_PATH_TOKENS = (
    "/etc/shadow",
    "/etc/gshadow",
    "/etc/passwd",
    "/etc/sudoers",
    "/root",
    "/proc/kcore",
    "/proc/kallsyms",
    "/proc/sysrq-trigger",
    "/sys/firmware",
    ".ssh",
    ".gnupg",
    ".aws",
    ".bashrc",
    ".config",
    ".bash_profile",
    ".profile",
    ".bash_history",
    ".gitconfig",
    "mobile.token",
    "home_assistant.token",
    "smart_home_vault.enc",
    "smart_home_vault.key",
    "mobile-devices.db",
    ".env",
    "id_rsa",
    "id_ed25519",
)

FORBIDDEN_GIT_SUBCOMMANDS = frozenset({
    "clone",
    "fetch",
    "pull",
    "push",
    "remote",
    "submodule",
    "daemon",
    "http-backend",
    "upload-pack",
    "receive-pack",
    "send-pack",
    "credential",
    "config",
})

FORBIDDEN_FIND_FLAGS = frozenset({
    "-exec",
    "-execdir",
    "-ok",
    "-okdir",
    "-delete",
    "-fls",
    "-fprint",
    "-fprint0",
    "-fprintf",
})

FORBIDDEN_PYTHON_NET_PATTERNS = (
    re.compile(r"\bsocket\s*\.\s*(?:socket|create_connection|create_server|fromfd|socketpair|connect)\b"),
    re.compile(r"\burllib\.request\.(?:urlopen|urlretrieve|Request)\b"),
    re.compile(r"\bhttp\.client\.(?:HTTPConnection|HTTPSConnection)\b"),
    re.compile(r"\b(?:ftplib|smtplib|telnetlib|xmlrpc|paramiko|requests|httpx|aiohttp|urllib3)\b"),
    re.compile(r"\basyncio\s*\.\s*(?:open_connection|start_server)\b"),
    re.compile(r"\bos\s*\.\s*(?:system|popen|exec\w*|spawn\w*|fork|kill|setuid|setgid|chown|chroot)\b"),
    re.compile(r"\bctypes\b.*\b(?:CDLL|cdll|syscall)\b"),
)

MALICIOUS_COMMAND_PATTERNS = (
    re.compile(r"(?:^|\s|;|&&|\|\|)rm\s+-[a-zA-Z]*[rf][a-zA-Z]*\s+/(?:\s|$|\*)"),
    re.compile(r"(?:^|\s)rm\s+--no-preserve-root"),
    re.compile(r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:"),
    re.compile(r"\b(?:mkfs|fdisk|parted|wipefs|dd\s+if=/dev/(?:zero|random|urandom)\s+of=/dev/)\b", re.I),
    re.compile(r"\b(?:shutdown|reboot|poweroff|halt|init\s+[06])\b", re.I),
    re.compile(r"\b(?:nc|netcat|ncat|socat|telnet|ftp|ssh|scp|sftp|rsync)\b", re.I),
    re.compile(r"/dev/(?:tcp|udp)/", re.I),
    re.compile(r"\b(?:curl|wget)\b", re.I),
    re.compile(r"\b(?:chmod\s+[0-7]*777\s+/|chown\s+-R\s+.*\s+/(?:\s|$))", re.I),
    re.compile(r"\b(?:sudo|su|pkexec|doas|chroot|nsenter|unshare|mount|umount|insmod|modprobe)\b", re.I),
    re.compile(
        r"(?:cat|less|more|head|tail|cp|scp|grep|strings|file|sha256sum)\s+[^\n]*"
        r"(?:/etc/shadow|/etc/passwd|\.ssh/|mobile\.token|smart_home_vault|\.env)\b",
        re.I,
    ),
)

_SECRET_OUTPUT_SCRUB = re.compile(
    r"(?i)\b(?:bearer\s+[A-Za-z0-9._~+/=-]+|token\s*[:=]\s*\S+|api[_-]?key\s*[:=]\s*\S+|secret\s*[:=]\s*\S+)\b"
)


class SandboxSecurityError(PermissionError):
    """Exceção levantada quando um comando viola as diretrizes de segurança do ExecutionSandbox."""


class SandboxTimeoutError(TimeoutError):
    """Exceção ou marcador de limite de tempo excedido no ExecutionSandbox."""


@dataclass(frozen=True)
class SandboxLimits:
    """Limites rígidos de recursos aplicados aos processos filhos do ExecutionSandbox."""

    max_memory_bytes: int = DEFAULT_MAX_MEMORY_BYTES
    max_cpu_seconds: int = DEFAULT_MAX_CPU_SECONDS
    max_open_files: int = DEFAULT_MAX_OPEN_FILES
    max_file_size_bytes: int = DEFAULT_MAX_FILE_SIZE_BYTES
    max_processes: int = DEFAULT_MAX_PROCESSES

    def __init__(
        self,
        max_memory_bytes: int | None = None,
        max_cpu_seconds: int = DEFAULT_MAX_CPU_SECONDS,
        max_open_files: int = DEFAULT_MAX_OPEN_FILES,
        max_file_size_bytes: int | None = None,
        max_processes: int = DEFAULT_MAX_PROCESSES,
        *,
        max_memory_mb: int | None = None,
        max_file_size_mb: int | None = None,
    ) -> None:
        eff_mem = (
            int(max_memory_mb) * 1024 * 1024
            if max_memory_mb is not None
            else (int(max_memory_bytes) if max_memory_bytes is not None else DEFAULT_MAX_MEMORY_BYTES)
        )
        eff_fsize = (
            int(max_file_size_mb) * 1024 * 1024
            if max_file_size_mb is not None
            else (int(max_file_size_bytes) if max_file_size_bytes is not None else DEFAULT_MAX_FILE_SIZE_BYTES)
        )
        object.__setattr__(self, "max_memory_bytes", eff_mem)
        object.__setattr__(self, "max_cpu_seconds", int(max_cpu_seconds))
        object.__setattr__(self, "max_open_files", int(max_open_files))
        object.__setattr__(self, "max_file_size_bytes", eff_fsize)
        object.__setattr__(self, "max_processes", int(max_processes))
        self.validate()

    def validate(self) -> SandboxLimits:
        if self.max_memory_bytes < 64 * 1024 * 1024 or self.max_memory_bytes > MAX_MEMORY_BYTES_LIMIT:
            raise ValueError("max_memory_bytes fora do intervalo seguro permitido (64 MiB .. 2 GiB).")
        if self.max_cpu_seconds < 1 or self.max_cpu_seconds > MAX_CPU_SECONDS_LIMIT:
            raise ValueError("max_cpu_seconds fora do intervalo seguro permitido (1 .. 60s).")
        if self.max_open_files < 16 or self.max_open_files > MAX_OPEN_FILES_LIMIT:
            raise ValueError("max_open_files fora do intervalo seguro permitido (16 .. 256).")
        if self.max_file_size_bytes < 1024 or self.max_file_size_bytes > 256 * 1024 * 1024:
            raise ValueError("max_file_size_bytes fora do intervalo seguro permitido.")
        if self.max_processes < 4 or self.max_processes > 512:
            raise ValueError("max_processes fora do intervalo seguro permitido.")
        return self


@dataclass(frozen=True)
class SandboxResult:
    command: list[str]
    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool = False
    blocked: bool = False
    duration_seconds: float = 0.0
    isolation_backend: str = "posix-rlimit"

    @property
    def returncode(self) -> int:
        return self.exit_code

    @property
    def success(self) -> bool:
        return not self.blocked and not self.timed_out and self.exit_code == 0

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["returncode"] = self.exit_code
        data["success"] = self.success
        return data


@functools.lru_cache(maxsize=1)
def _probe_bwrap_support() -> bool:
    """Verifica dinamicamente se o Bubblewrap (bwrap) com namespaces isolados funciona neste kernel/host."""
    bwrap_bin = shutil.which("bwrap")
    if not bwrap_bin:
        return False
    probe_cmd = [
        bwrap_bin,
        "--unshare-all",
        "--die-with-parent",
        "--new-session",
        "--cap-drop",
        "ALL",
        "--ro-bind",
        "/usr",
        "/usr",
        "--symlink",
        "usr/bin",
        "/bin",
        "--symlink",
        "usr/lib",
        "/lib",
        "--symlink",
        "usr/lib",
        "/lib64",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--tmpfs",
        "/tmp",  # nosec: B108
        "/usr/bin/true",
    ]
    try:
        res = subprocess.run(
            probe_cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=3,
            check=False,
        )
        return res.returncode == 0
    except OSError:
        return False
    except subprocess.TimeoutExpired:
        return False


def _make_preexec_limiter(limits: SandboxLimits, cpu_seconds: int):
    """Cria callback preexec_fn para impor limites rígidos via setrlimit antes do execve."""

    def _apply_limits() -> None:
        os.setsid()
        rlimit_specs = (
            (getattr(resource, "RLIMIT_CORE", None), (0, 0)),
            (getattr(resource, "RLIMIT_CPU", None), (cpu_seconds, cpu_seconds)),
            (getattr(resource, "RLIMIT_FSIZE", None), (limits.max_file_size_bytes, limits.max_file_size_bytes)),
            (getattr(resource, "RLIMIT_NOFILE", None), (limits.max_open_files, limits.max_open_files)),
            (getattr(resource, "RLIMIT_AS", None), (limits.max_memory_bytes, limits.max_memory_bytes)),
        )
        for res_key, (soft, hard) in rlimit_specs:
            if res_key is None:
                continue
            try:
                resource.setrlimit(res_key, (soft, hard))
            except (ValueError, OSError):
                try:
                    cur_soft, cur_hard = resource.getrlimit(res_key)
                    resource.setrlimit(res_key, (min(soft, cur_hard), cur_hard))
                except (ValueError, OSError):
                    pass

    return _apply_limits


def sanitize_environment(
    base_env: Mapping[str, str] | None = None,
    extra_env: Mapping[str, str] | None = None,
    *,
    sandbox_home: str | Path = "/tmp",  # nosec: B108
) -> dict[str, str]:
    """Constrói um ambiente estritamente higienizado, bloqueando tokens, chaves de API e credenciais."""
    source = os.environ if base_env is None else base_env
    clean: dict[str, str] = {
        "HOME": str(sandbox_home),
        "LANG": source.get("LANG", "C.UTF-8"),
        "LC_ALL": source.get("LC_ALL", "C.UTF-8"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "PYTHONNOUSERSITE": "1",
    }

    raw_path = source.get("PATH", "/usr/local/bin:/usr/bin:/bin")
    cargo_bin = str(Path.home() / ".cargo" / "bin")
    path_parts = [p for p in raw_path.split(os.pathsep) if p]
    if cargo_bin not in path_parts and Path(cargo_bin).is_dir():
        path_parts.insert(0, cargo_bin)
    clean["PATH"] = os.pathsep.join(path_parts) if path_parts else "/usr/bin:/bin"

    for key in ("PYTHONPATH", "VIRTUAL_ENV", "RUSTUP_HOME", "CARGO_HOME", "QT_QPA_PLATFORM", "CI"):
        if key in source and not SENSITIVE_ENV_PATTERNS.search(key):
            val = str(source[key])
            if not _SECRET_OUTPUT_SCRUB.search(val):
                clean[key] = val

    if extra_env:
        for k, v in extra_env.items():
            if not isinstance(k, str) or not isinstance(v, str):
                continue
            if SENSITIVE_ENV_PATTERNS.search(k):
                log.warning("Variável de ambiente sensível bloqueada pelo sandbox: %s", k)
                continue
            if "\x00" in k or "\x00" in v:
                raise SandboxSecurityError("Nulos não são permitidos em variáveis de ambiente.")
            clean[k] = v

    return clean


class ExecutionSandbox:
    """Sandbox seguro para execução isolada de comandos de shell, scripts Python e builds Rust.

    Implementa isolamento real a nível de sistema operacional (Bubblewrap com `--unshare-all`,
    incluindo `--unshare-net` para bloqueio de sockets de rede, `--cap-drop ALL`, sistema de
    arquivos somente leitura e confinamento estrito ao `workspace`), além de limites rígidos de
    memória (`RLIMIT_AS`), CPU (`RLIMIT_CPU`), descritores de arquivo (`RLIMIT_NOFILE`) e
    validação estrita de argumentos por executável.
    """

    def __init__(
        self,
        workspace: str | Path | None = None,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        allow_all_binaries: bool = False,
        limits: SandboxLimits | None = None,
        use_os_isolation: bool = True,
        allow_network: bool = False,
        require_bwrap: bool | None = None,
    ):
        if timeout <= 0:
            raise ValueError("O timeout do sandbox deve ser positivo.")
        if allow_network:
            raise SandboxSecurityError("Acesso a sockets de rede não autorizados é proibido no ExecutionSandbox.")
        self.timeout = min(float(timeout), float(MAX_TIMEOUT_SECONDS))
        self.allow_all_binaries = allow_all_binaries
        self.limits = limits or SandboxLimits()
        self.use_os_isolation = bool(use_os_isolation)
        self.allow_network = False
        env_require = os.environ.get("MARK_SANDBOX_REQUIRE_BWRAP", "").strip().lower() in ("1", "true", "yes")
        self.require_bwrap = env_require if require_bwrap is None else bool(require_bwrap)
        self._temp_dir: tempfile.TemporaryDirectory[str] | None = None
        if workspace is not None:
            raw_ws = Path(workspace).expanduser()
            if raw_ws.is_symlink():
                raise SandboxSecurityError("O diretório de workspace do sandbox não pode ser um symlink.")
            if any(token in str(raw_ws).lower() for token in FORBIDDEN_PATH_TOKENS):
                raise SandboxSecurityError(f"Workspace sensível ou protegido para o sandbox: {raw_ws}")
            resolved = raw_ws.resolve()
            if not resolved.exists() or not resolved.is_dir():
                raise ValueError(f"Workspace inválido para o sandbox: {workspace}")
            if resolved in (Path("/"), Path("/etc"), Path("/usr"), Path("/var"), Path("/root"), Path.home()):
                raise SandboxSecurityError(f"Workspace amplo ou sensível demais para o sandbox: {resolved}")
            sensitive_home_subdirs = {
                (Path.home() / sub).resolve()
                for sub in (".ssh", ".gnupg", ".config", ".aws", ".bashrc", ".bash_profile", ".profile", ".gitconfig")
            }
            if resolved in sensitive_home_subdirs or any(token in str(resolved).lower() for token in FORBIDDEN_PATH_TOKENS):
                raise SandboxSecurityError(f"Workspace sensível ou protegido para o sandbox: {resolved}")
            self.workspace = resolved
        else:
            self._temp_dir = tempfile.TemporaryDirectory(prefix="mark-exec-sandbox-")
            self.workspace = Path(self._temp_dir.name).resolve()

    @property
    def bwrap_available(self) -> bool:
        return self.use_os_isolation and _probe_bwrap_support()

    @property
    def isolation_backend(self) -> str:
        return "bwrap" if self.bwrap_available else "posix-rlimit"

    def close(self) -> None:
        if self._temp_dir is not None:
            self._temp_dir.cleanup()
            self._temp_dir = None

    def __enter__(self) -> ExecutionSandbox:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def _is_allowed_readonly_runtime_path(self, candidate: Path) -> bool:
        """Verifica se um caminho absoluto pertence ao runtime somente leitura autorizado (ex: sys.executable)."""
        exe_abs = Path(sys.executable).absolute()
        exe_resolved = Path(sys.executable).resolve()
        venv_prefix = Path(sys.prefix).resolve()
        allowed_roots = [
            Path("/usr"),
            Path("/bin"),
            Path("/lib"),
            Path("/lib64"),
            venv_prefix,
            exe_abs.parent,
            exe_abs.parent.parent,
            exe_resolved.parent,
            exe_resolved.parent.parent,
        ]
        mark_src = Path(__file__).resolve().parents[1]
        allowed_roots.append(mark_src)
        for root in allowed_roots:
            try:
                candidate.relative_to(root)
                return True
            except ValueError:
                continue
        return False

    def _validate_path_argument(self, arg: str, *, exe_name: str, allow_runtime_ro: bool = False) -> None:
        """Impede escape de diretório (path traversal), leitura/escrita fora do workspace e symlinks maliciosos."""
        lowered = arg.lower()
        for token in FORBIDDEN_PATH_TOKENS:
            if token in lowered:
                raise SandboxSecurityError(f"Acesso a caminho ou credencial protegida bloqueado: {arg}")

        if arg.startswith("-"):
            # Se for flag no formato --flag=/caminho, valida a parte do valor
            if "=" in arg:
                _, val = arg.split("=", 1)
                if val:
                    self._validate_path_argument(val, exe_name=exe_name, allow_runtime_ro=allow_runtime_ro)
            return

        pure = PurePath(arg)
        if ".." in pure.parts:
            resolved_rel = (self.workspace / arg).resolve()
            try:
                resolved_rel.relative_to(self.workspace)
            except ValueError as exc:
                raise SandboxSecurityError(
                    f"Tentativa de escape de diretório (path traversal) bloqueada nos argumentos de '{exe_name}': {arg}"
                ) from exc

        if arg.startswith("/") or arg.startswith("~"):
            expanded = Path(arg).expanduser()
            if expanded.is_symlink():
                target_resolved = expanded.resolve()
            else:
                target_resolved = expanded.resolve()
            try:
                target_resolved.relative_to(self.workspace)
                return
            except ValueError:
                if allow_runtime_ro and self._is_allowed_readonly_runtime_path(target_resolved):
                    return
                raise SandboxSecurityError(
                    f"Caminho absoluto fora do workspace autorizado ({self.workspace}) bloqueado: {arg}"
                )

        # Verifica se argumento relativo aponta para um symlink no workspace que escapa para fora
        rel_candidate = self.workspace / arg.split("::")[0]
        if rel_candidate.exists() or rel_candidate.is_symlink():
            resolved_candidate = rel_candidate.resolve()
            try:
                resolved_candidate.relative_to(self.workspace)
            except ValueError as exc:
                raise SandboxSecurityError(
                    f"Symlink ou caminho relativo escapa do workspace autorizado: {arg}"
                ) from exc

    def _validate_binary_specific_args(self, exe_name: str, argv: list[str]) -> None:
        """Valida estritamente os argumentos específicos de cada executável permitido."""
        args = argv[1:]
        py_bins = {"python", "python3", Path(sys.executable).name}

        if exe_name == "rm":
            raise SandboxSecurityError("Remoção direta via rm é bloqueada no sandbox.")

        if exe_name in py_bins:
            idx = 0
            while idx < len(args):
                arg = args[idx]
                if arg == "-c":
                    if idx + 1 >= len(args):
                        raise SandboxSecurityError("Código ausente após flag -c do Python.")
                    inline_code = args[idx + 1]
                    self._validate_inline_python_code(inline_code)
                    idx += 2
                    continue
                if arg == "-m":
                    if idx + 1 >= len(args):
                        raise SandboxSecurityError("Módulo ausente após flag -m do Python.")
                    mod_name = args[idx + 1]
                    if mod_name in {"http.server", "smtpd", "webbrowser", "pip", "ensurepip", "venv", "pdb", "code"}:
                        raise SandboxSecurityError(f"Módulo Python restrito não permitido no sandbox: {mod_name}")
                    idx += 2
                    continue
                self._validate_path_argument(arg, exe_name=exe_name, allow_runtime_ro=True)
                idx += 1
            return

        if exe_name == "find":
            for arg in args:
                if arg in FORBIDDEN_FIND_FLAGS:
                    raise SandboxSecurityError(f"Flag de execução/mutação do find bloqueada: {arg}")
                self._validate_path_argument(arg, exe_name=exe_name)
            return

        if exe_name == "git":
            for arg in args:
                if arg.startswith("-c") or arg.startswith("--config-env") or arg.startswith("--upload-pack") or arg.startswith("--exec-path"):
                    raise SandboxSecurityError(f"Flag de configuração/execução arbitrária do git bloqueada: {arg}")
            subcommands = [a for a in args if not a.startswith("-")]
            if subcommands and subcommands[0] in FORBIDDEN_GIT_SUBCOMMANDS:
                raise SandboxSecurityError(f"Subcomando git de rede/mutação externa bloqueado: {subcommands[0]}")
            for arg in args:
                self._validate_path_argument(arg, exe_name=exe_name)
            return

        if exe_name in {"cargo", "rustc", "maturin"}:
            for arg in args:
                if arg in {"publish", "login", "owner", "yank", "install"}:
                    raise SandboxSecurityError(f"Subcomando externo do {exe_name} bloqueado: {arg}")
                self._validate_path_argument(arg, exe_name=exe_name, allow_runtime_ro=True)
            return

        if exe_name in {"ls", "cat", "head", "tail", "wc", "grep", "file", "strings", "nm", "objdump", "readelf", "sha256sum", "pytest", "ruff", "flake8", "mypy"}:
            for arg in args:
                self._validate_path_argument(arg, exe_name=exe_name)
            return

        for arg in args:
            self._validate_path_argument(arg, exe_name=exe_name)

    def _validate_inline_python_code(self, code: str) -> None:
        """Inspeciona código Python inline/script contra criação de sockets de rede ou escapes explícitos."""
        lowered = code.lower()
        for token in ("/etc/shadow", "/etc/gshadow", ".ssh/id_", "mobile.token", "smart_home_vault.key"):
            if token in lowered:
                raise SandboxSecurityError("Código Python referencia arquivo de credencial protegido do sistema.")
        for pattern in FORBIDDEN_PYTHON_NET_PATTERNS:
            if pattern.search(code):
                raise SandboxSecurityError(
                    "Código Python contém chamada de rede ou primitiva de sistema não autorizada no ExecutionSandbox."
                )

    def validate_command(self, command: str | Sequence[str]) -> list[str]:
        """Valida o comando contra injeções, padrões destrutivos, escape de caminhos e binários não autorizados."""
        if isinstance(command, str):
            raw_text = command.strip()
            if not raw_text:
                raise SandboxSecurityError("Comando vazio não é permitido.")
            if "\x00" in raw_text:
                raise SandboxSecurityError("Caractere nulo detectado no comando.")
            for pattern in MALICIOUS_COMMAND_PATTERNS:
                if pattern.search(raw_text):
                    raise SandboxSecurityError(f"Comando malicioso ou destrutivo bloqueado pelo sandbox: {raw_text[:80]}")
            if any(op in raw_text for op in (";", "&&", "||", "|", "`", "$(", ">", "<", "\n", "\r")):
                raise SandboxSecurityError("Encadeamento de shell, redirecionamento ou subshells arbitrários são bloqueados no sandbox.")
            try:
                argv = shlex.split(raw_text)
            except ValueError as exc:
                raise SandboxSecurityError(f"Sintaxe de comando inválida: {exc}") from exc
        elif isinstance(command, (list, tuple)):
            if not command or not all(isinstance(part, str) for part in command):
                raise SandboxSecurityError("Lista de argumentos inválida ou vazia.")
            argv = [str(part) for part in command]
            joined = " ".join(argv)
            if any("\x00" in part for part in argv):
                raise SandboxSecurityError("Caractere nulo detectado nos argumentos.")
            for pattern in MALICIOUS_COMMAND_PATTERNS:
                if pattern.search(joined):
                    raise SandboxSecurityError(f"Comando malicioso ou destrutivo bloqueado pelo sandbox: {joined[:80]}")
        else:
            raise SandboxSecurityError("Formato de comando inválido.")

        if not argv:
            raise SandboxSecurityError("Nenhum executável especificado.")

        exe_raw = argv[0]
        exe_name = Path(exe_raw).name
        if not self.allow_all_binaries and exe_name not in ALLOWED_BINARIES:
            raise SandboxSecurityError(f"Executável não autorizado na allowlist do sandbox: {exe_name}")

        if "/" in exe_raw:
            exe_path = Path(exe_raw).expanduser().resolve()
            if not self._is_allowed_readonly_runtime_path(exe_path):
                try:
                    exe_path.relative_to(self.workspace)
                except ValueError as exc:
                    raise SandboxSecurityError(f"Caminho de executável fora dos diretórios confiáveis: {exe_raw}") from exc

        self._validate_binary_specific_args(exe_name, argv)
        return argv

    def _resolve_cwd(self, cwd: str | Path | None) -> Path:
        if cwd is None:
            return self.workspace
        candidate = Path(cwd)
        if candidate.is_symlink():
            raise SandboxSecurityError("Diretório de execução não pode ser um link simbólico.")
        if not candidate.is_absolute():
            candidate = (self.workspace / candidate).resolve()
        else:
            candidate = candidate.resolve()
        try:
            candidate.relative_to(self.workspace)
        except ValueError as exc:
            raise SandboxSecurityError("Diretório de execução escapa do workspace autorizado do sandbox.") from exc
        if not candidate.is_dir():
            raise ValueError(f"Diretório de trabalho não existe: {candidate}")
        return candidate

    def _build_bwrap_command(
        self,
        argv: list[str],
        work_dir: Path,
        clean_env: Mapping[str, str],
        temp_home: str,
        cpu_seconds: int,
    ) -> list[str]:
        """Constrói o comando de confinamento Bubblewrap + prlimit com namespaces isolados e sem rede."""
        bwrap_bin = shutil.which("bwrap") or "/usr/bin/bwrap"
        exe_target = argv[0]
        if "/" not in exe_target:
            resolved_exe = shutil.which(exe_target, path=clean_env.get("PATH"))
            if resolved_exe:
                argv = [resolved_exe, *argv[1:]]

        bwrap_cmd: list[str] = [
            bwrap_bin,
            "--unshare-all",  # Inclui --unshare-net (bloqueia sockets externos/locais), user, pid, ipc, uts, cgroup
            "--die-with-parent",
            "--new-session",
            "--cap-drop",
            "ALL",
            "--clearenv",
            "--ro-bind",
            "/usr",
            "/usr",
            "--symlink",
            "usr/bin",
            "/bin",
            "--symlink",
            "usr/lib",
            "/lib",
            "--symlink",
            "usr/lib",
            "/lib64",
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--tmpfs",
            "/tmp",  # nosec: B108
            "--tmpfs",
            "/home",
            "--dir",
            temp_home,
        ]

        # Monta bibliotecas e interpretador do .venv e código-fonte do MARK apenas como SOMENTE LEITURA (--ro-bind)
        ro_mounts: set[Path] = set()
        for venv_candidate in (
            Path(sys.prefix).resolve(),
            Path(sys.executable).absolute().parents[1],
            Path(sys.executable).resolve().parents[1],
        ):
            if venv_candidate != Path("/usr") and venv_candidate != Path("/") and venv_candidate.exists():
                ro_mounts.add(venv_candidate)
        mark_repo_src = Path(__file__).resolve().parents[1]
        if mark_repo_src.exists():
            ro_mounts.add(mark_repo_src)
        cargo_dir = Path.home() / ".cargo"
        rustup_dir = Path.home() / ".rustup"
        user_local_lib = Path.home() / ".local" / "lib"
        for opt_dir in (cargo_dir, rustup_dir, user_local_lib):
            if opt_dir.is_dir():
                ro_mounts.add(opt_dir.resolve())

        for ro_path in sorted(ro_mounts):
            try:
                ro_path.relative_to(self.workspace)
                continue
            except ValueError:
                pass
            if str(ro_path).startswith("/usr"):
                continue
            # Bloqueio estrito de qualquer montagem contendo diretórios ou tokens sensíveis do usuário host
            str_ro = str(ro_path).lower()
            if any(token in str_ro for token in FORBIDDEN_PATH_TOKENS):
                log.warning("Diretório sensível do host bloqueado de ro-bind no sandbox: %s", ro_path)
                continue
            bwrap_cmd.extend(["--ro-bind", str(ro_path), str(ro_path)])

        # Único diretório com permissão de leitura e escrita é o workspace autorizado (e /workspace apontando para ele)
        bwrap_cmd.extend(["--bind", str(self.workspace), str(self.workspace)])
        if self.workspace != Path("/workspace"):
            bwrap_cmd.extend(["--bind", str(self.workspace), "/workspace"])

        for env_k, env_v in clean_env.items():
            bwrap_cmd.extend(["--setenv", env_k, env_v])

        bwrap_cmd.extend(["--chdir", str(work_dir), "--", *argv])

        prlimit_bin = shutil.which("prlimit")
        if prlimit_bin:
            return [
                prlimit_bin,
                f"--as={self.limits.max_memory_bytes}",
                f"--cpu={cpu_seconds}",
                f"--fsize={self.limits.max_file_size_bytes}",
                f"--nofile={self.limits.max_open_files}",
                "--core=0",
                "--",
                *bwrap_cmd,
            ]
        return bwrap_cmd

    def run(
        self,
        command: str | Sequence[str],
        *,
        cwd: str | Path | None = None,
        timeout: float | None = None,
        env: Mapping[str, str] | None = None,
        raise_on_security: bool = True,
        require_bwrap: bool | None = None,
    ) -> SandboxResult:
        """Executa um comando validado em processo isolado a nível de SO (bwrap + rlimits), com timeout estrito."""
        started = time.monotonic()
        try:
            argv = self.validate_command(command)
            work_dir = self._resolve_cwd(cwd)
        except SandboxSecurityError as exc:
            if raise_on_security:
                raise
            return SandboxResult(
                command=[command] if isinstance(command, str) else list(command),
                stdout="",
                stderr=str(exc),
                exit_code=126,
                timed_out=False,
                blocked=True,
                duration_seconds=round(time.monotonic() - started, 4),
                isolation_backend="blocked",
            )

        effective_timeout = self.timeout if timeout is None else min(float(timeout), float(MAX_TIMEOUT_SECONDS))
        if effective_timeout <= 0:
            raise ValueError("Timeout deve ser maior que zero.")

        cpu_limit = max(1, min(int(effective_timeout) + 2, self.limits.max_cpu_seconds))

        with tempfile.TemporaryDirectory(prefix="mark-sbx-home-") as temp_home:
            clean_env = sanitize_environment(extra_env=env, sandbox_home=temp_home)
            use_bwrap = self.bwrap_available
            effective_require_bwrap = self.require_bwrap if require_bwrap is None else bool(require_bwrap)
            if effective_require_bwrap and not use_bwrap:
                msg = (
                    "Isolamento a nível de SO (Bubblewrap / bwrap) indisponível ou inoperável. "
                    "Execução abortada por fail-safe para impedir comandos não isolados."
                )
                if raise_on_security:
                    raise SandboxSecurityError(msg)
                return SandboxResult(
                    command=[command] if isinstance(command, str) else list(command),
                    stdout="",
                    stderr=msg,
                    exit_code=126,
                    timed_out=False,
                    blocked=True,
                    duration_seconds=round(time.monotonic() - started, 4),
                    isolation_backend="blocked",
                )
            if use_bwrap:
                exec_argv = self._build_bwrap_command(argv, work_dir, clean_env, temp_home, cpu_limit)
                backend_label = "bwrap-namespaces+rlimit"
            else:
                exec_argv = argv
                backend_label = "posix-rlimit"

            try:
                process = subprocess.Popen(
                    exec_argv,
                    cwd=str(work_dir),
                    env=clean_env,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=False,
                    preexec_fn=_make_preexec_limiter(self.limits, cpu_limit),
                )
            except FileNotFoundError as exc:
                return SandboxResult(
                    command=argv,
                    stdout="",
                    stderr=f"Executável não encontrado: {argv[0]} ({exc})",
                    exit_code=127,
                    timed_out=False,
                    blocked=False,
                    duration_seconds=round(time.monotonic() - started, 4),
                    isolation_backend=backend_label,
                )

            timed_out = False
            try:
                raw_out, raw_err = process.communicate(timeout=effective_timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                raw_out, raw_err = process.communicate()

        stdout = _decode_and_scrub(raw_out)
        stderr = _decode_and_scrub(raw_err)
        if timed_out:
            stderr = (stderr + f"\n[SANDBOX TIMEOUT] Execução abortada após {effective_timeout:.1f}s.").strip()
            exit_code = -9
        else:
            exit_code = int(process.returncode if process.returncode is not None else -1)

        return SandboxResult(
            command=argv,
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            timed_out=timed_out,
            blocked=False,
            duration_seconds=round(time.monotonic() - started, 4),
            isolation_backend=backend_label,
        )

    def run_python_script(
        self,
        code_or_path: str | Path,
        *,
        args: Sequence[str] = (),
        timeout: float | None = None,
        cwd: str | Path | None = None,
    ) -> SandboxResult:
        """Executa um script Python (arquivo existente no workspace ou código inline) de forma isolada."""
        work_dir = self._resolve_cwd(cwd)
        for a in args:
            self._validate_path_argument(str(a), exe_name="python", allow_runtime_ro=False)

        if isinstance(code_or_path, Path) or (
            isinstance(code_or_path, str)
            and "\n" not in code_or_path
            and code_or_path.endswith(".py")
            and (work_dir / code_or_path).is_file()
        ):
            raw_candidate = work_dir / code_or_path if not Path(code_or_path).is_absolute() else Path(code_or_path)
            if raw_candidate.is_symlink():
                raise SandboxSecurityError("Script Python não pode ser um link simbólico.")
            target = raw_candidate.resolve()
            try:
                target.relative_to(self.workspace)
            except ValueError as exc:
                raise SandboxSecurityError("Script Python escapa do workspace autorizado.") from exc
            script_text = target.read_text(encoding="utf-8", errors="replace")
            self._validate_inline_python_code(script_text)
            cmd = [sys.executable, "-I", str(target), *[str(a) for a in args]]
            return self.run(cmd, cwd=work_dir, timeout=timeout)

        if not isinstance(code_or_path, str) or len(code_or_path.encode("utf-8")) > 256 * 1024:
            raise ValueError("Código Python inválido ou excede 256 KB.")

        self._validate_inline_python_code(code_or_path)

        temp_script = work_dir / f".sandbox_exec_{os.getpid()}_{int(time.monotonic()*1000)}.py"
        try:
            temp_script.write_text(code_or_path, encoding="utf-8")
            temp_script.chmod(0o600)
            env_override = {"PYTHONPATH": f"{work_dir}{os.pathsep}{work_dir / 'src'}"}
            cmd = [sys.executable, str(temp_script), *[str(a) for a in args]]
            return self.run(cmd, cwd=work_dir, timeout=timeout, env=env_override)
        finally:
            try:
                temp_script.unlink()
            except FileNotFoundError:
                pass

    def run_pytest(
        self,
        target: str | Sequence[str] = "tests",
        *,
        timeout: float | None = None,
        cwd: str | Path | None = None,
    ) -> SandboxResult:
        """Executa suítes de teste pytest dentro do workspace isolado."""
        work_dir = self._resolve_cwd(cwd)
        targets = [target] if isinstance(target, str) else [str(t) for t in target]
        for t in targets:
            if t.startswith("-"):
                raise SandboxSecurityError("Flags arbitrárias no alvo do pytest não são permitidas.")
            raw_path = work_dir / t.split("::")[0]
            if raw_path.is_symlink():
                raise SandboxSecurityError("Alvo de teste não pode ser um link simbólico.")
            candidate = raw_path.resolve()
            try:
                candidate.relative_to(self.workspace)
            except ValueError as exc:
                raise SandboxSecurityError("Caminho de teste escapa do workspace autorizado.") from exc

        import site
        user_site = site.getusersitepackages()
        pythonpath_parts = [str(work_dir), str(work_dir / "src")]
        if user_site and Path(user_site).exists():
            pythonpath_parts.append(str(user_site))
        env_override = {"PYTHONPATH": os.pathsep.join(pythonpath_parts)}
        cmd = [sys.executable, "-m", "pytest", "-q", *targets]
        return self.run(cmd, cwd=work_dir, timeout=timeout, env=env_override)

    def run_rust_build(
        self,
        *,
        mode: str = "cargo_check",
        manifest_path: str | Path = "src-rust/mark_core/Cargo.toml",
        release: bool = False,
        timeout: float | None = None,
    ) -> SandboxResult:
        """Executa compilações Rust (cargo check, cargo build ou maturin develop) no sandbox."""
        raw_manifest = self.workspace / manifest_path if not Path(manifest_path).is_absolute() else Path(manifest_path)
        if raw_manifest.is_symlink():
            raise SandboxSecurityError("Manifesto Cargo.toml não pode ser um link simbólico.")
        manifest = raw_manifest.resolve()
        try:
            manifest.relative_to(self.workspace)
        except ValueError as exc:
            raise SandboxSecurityError("Caminho do Cargo.toml escapa do workspace autorizado.") from exc
        if not manifest.is_file():
            raise FileNotFoundError(f"Manifesto Rust não encontrado: {manifest}")

        if mode == "cargo_check":
            cmd = ["cargo", "check", "--offline", "--manifest-path", str(manifest)]
            if release:
                cmd.append("--release")
        elif mode == "cargo_build":
            cmd = ["cargo", "build", "--offline", "--manifest-path", str(manifest)]
            if release:
                cmd.append("--release")
        elif mode == "maturin_develop":
            if not shutil.which("maturin"):
                raise FileNotFoundError("Executável 'maturin' não está disponível no PATH.")
            cmd = ["maturin", "develop", "--manifest-path", str(manifest)]
            if release:
                cmd.append("--release")
        else:
            raise ValueError(f"Modo de build Rust desconhecido: {mode}")

        return self.run(cmd, cwd=self.workspace, timeout=timeout)


def _decode_and_scrub(raw: bytes | None) -> str:
    if not raw:
        return ""
    clipped = raw[:MAX_OUTPUT_BYTES]
    text = clipped.decode("utf-8", errors="replace")
    return _SECRET_OUTPUT_SCRUB.sub("[REDACTED]", text)


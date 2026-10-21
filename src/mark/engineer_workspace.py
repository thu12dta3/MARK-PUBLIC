
import difflib
import os
from pathlib import Path, PurePosixPath
import secrets
import shutil
import signal
import stat
import subprocess
import time

from .missions import MissionGrant, MissionStore, _workspace_root


MAX_READ_BYTES = 2_000_000
MAX_WRITE_BYTES = 1_000_000
PROTECTED_PARTS = {".git", ".ssh", ".gnupg", ".aws", ".config", ".local"}
PROTECTED_NAMES = {".env", ".netrc", ".npmrc", "id_rsa", "id_ed25519"}
PROTECTED_SUFFIXES = {".pem", ".key", ".p12", ".pfx"}
PROTECTED_EXACT = {
    "src/mark/evolution/policy.py", "src/mark/mobile_api.py", "src/mark/mission.py",
    "src/mark/missions.py", "src/mark/engineer_workspace.py", "src/mark/desktop_missions.py",
    "src/mark/config.py", "src/mark/brain.py", "src/mark/cognition/providers.py",
    "src/mark/cognition/tools.py", "src/mark/core/context.py", "src/mark/core/events.py",
}


class WorkspaceTools:
    def __init__(self, store: MissionStore):
        self.store = store

    def _target(self, root: Path, relative: str, *, writing=False) -> tuple[Path, str]:
        root = _workspace_root(root)
        if not isinstance(relative, str) or not relative or len(relative) > 512 or "\\" in relative or "\x00" in relative:
            raise ValueError("Caminho inválido.")
        pure = PurePosixPath(relative)
        if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
            raise ValueError("O caminho precisa ser relativo e permanecer no workspace.")
        normalized = pure.as_posix()
        if any(part.casefold() in PROTECTED_PARTS or part.casefold() in PROTECTED_NAMES
               or Path(part).suffix.casefold() in PROTECTED_SUFFIXES for part in pure.parts):
            raise PermissionError("O caminho parece conter credenciais ou metadados protegidos.")
        if normalized in PROTECTED_EXACT:
            raise PermissionError("Políticas centrais e ferramentas de autorização são protegidas.")
        target = root.joinpath(*pure.parts)
        cursor = root
        for part in pure.parts[:-1] if writing else pure.parts:
            cursor = cursor / part
            if cursor.is_symlink():
                raise PermissionError("Symlinks dentro do workspace não são seguidos.")
        resolved_parent = (target.parent if writing else target).resolve(strict=False)
        try:
            resolved_parent.relative_to(root)
        except ValueError:
            raise PermissionError("O caminho escapou do workspace autorizado.") from None
        if target.exists() and target.is_symlink():
            raise PermissionError("Symlinks não são permitidos.")
        return target, normalized

    def _authorize(self, grant: MissionGrant, operation: str, root: Path):
        if not self.store.validate_grant(grant, operation, root):
            raise PermissionError("A autorização da missão expirou ou não cobre este workspace/operação.")

    def _open_parent(self, root: Path, normalized: str):

        parts = PurePosixPath(normalized).parts
        current = os.open(_workspace_root(root), os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
                          | getattr(os, "O_CLOEXEC", 0))
        try:
            for part in parts[:-1]:
                following = os.open(part, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0), dir_fd=current)
                os.close(current)
                current = following
            return current, parts[-1]
        except Exception:
            os.close(current)
            raise

    def _open_directory(self, root: Path, relative: str) -> int:
        if relative == ".":
            return os.open(_workspace_root(root), os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
                           | getattr(os, "O_CLOEXEC", 0))
        _, normalized = self._target(root, relative)
        parent_fd, name = self._open_parent(root, normalized)
        try:
            return os.open(name, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0), dir_fd=parent_fd)
        finally:
            os.close(parent_fd)

    def list_files(self, grant: MissionGrant, root: Path, relative: str = ".") -> dict:
        self._authorize(grant, "workspace.list", root)
        try:
            directory_fd = self._open_directory(root, relative)
        except (OSError, ValueError):
            raise ValueError("A pasta solicitada não existe ou não é válida.") from None
        entries = []
        try:
            with os.scandir(directory_fd) as items:
                for item in sorted(items, key=lambda p: p.name.casefold()):
                    relative_item = item.name if relative == "." else f"{relative}/{item.name}"
                    if (item.is_symlink() or item.name.casefold() in PROTECTED_PARTS
                            or item.name.casefold() in PROTECTED_NAMES
                            or Path(item.name).suffix.casefold() in PROTECTED_SUFFIXES
                            or relative_item in PROTECTED_EXACT):
                        continue
                    is_directory = item.is_dir(follow_symlinks=False)
                    entries.append({"name": item.name, "type": "directory" if is_directory else "file"})
                    if len(entries) >= 1000:
                        break
        finally:
            os.close(directory_fd)
        return {"relative_path": relative, "entries": entries}

    def read_file(self, grant: MissionGrant, root: Path, relative: str) -> dict:
        self._authorize(grant, "workspace.read", root)
        _, normalized = self._target(root, relative)
        parent_fd, name = self._open_parent(root, normalized)
        try:
            fd = os.open(name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
                         | getattr(os, "O_NONBLOCK", 0), dir_fd=parent_fd)
        except OSError as exc:
            os.close(parent_fd)
            if isinstance(exc, FileNotFoundError):
                raise ValueError("Arquivo ausente ou acima do limite de leitura (2 MB).") from None
            raise
        os.close(parent_fd)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_READ_BYTES:
                raise ValueError("Arquivo ausente ou acima do limite de leitura (2 MB).")
            data = stream.read(MAX_READ_BYTES + 1)
        if len(data) > MAX_READ_BYTES:
            raise ValueError("Arquivo ausente ou acima do limite de leitura (2 MB).")
        try:
            content = data.decode("utf-8")
        except UnicodeDecodeError:
            raise ValueError("A ferramenta de leitura aceita somente texto UTF-8.") from None
        return {"path": normalized, "content": content}

    def write_file(self, grant: MissionGrant, root: Path, relative: str, content: str) -> dict:
        self._authorize(grant, "workspace.write", root)
        _, normalized = self._target(root, relative, writing=True)
        if not isinstance(content, str) or len(content.encode("utf-8")) > MAX_WRITE_BYTES:
            raise ValueError("Conteúdo inválido ou acima de 1 MB.")
        parent_fd, name = self._open_parent(root, normalized)
        original_mode = 0o600
        try:
            try:
                original_fd = os.open(name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                                      | getattr(os, "O_NONBLOCK", 0), dir_fd=parent_fd)
            except FileNotFoundError:
                old = ""
            else:
                with os.fdopen(original_fd, "rb") as stream:
                    info = os.fstat(stream.fileno())
                    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
                        raise PermissionError("O destino precisa ser um arquivo regular pertencente ao usuário.")
                    if info.st_size > MAX_WRITE_BYTES:
                        raise ValueError("O arquivo atual é grande demais para alteração automática.")
                    original_mode = info.st_mode & 0o777
                    try:
                        old = stream.read(MAX_WRITE_BYTES + 1).decode("utf-8")
                    except UnicodeDecodeError:
                        raise ValueError("O arquivo atual não é texto UTF-8.") from None
        except Exception:
            os.close(parent_fd)
            raise
        if old == content:
            os.close(parent_fd)
            return {"path": normalized, "changed": False, "diff": ""}
        diff = "".join(difflib.unified_diff(old.splitlines(keepends=True), content.splitlines(keepends=True),
                                            fromfile=f"a/{normalized}", tofile=f"b/{normalized}"))
        temporary = ".mark-write-" + secrets.token_hex(12)
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), original_mode,
                         dir_fd=parent_fd)
            os.fchmod(fd, original_mode)
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        except Exception:
            try: os.unlink(temporary, dir_fd=parent_fd)
            except FileNotFoundError: pass
            raise
        finally:
            os.close(parent_fd)
        return {"path": normalized, "changed": True, "diff": diff[:50000]}

    def verify_python(self, grant: MissionGrant, root: Path, test_root: str = "tests",
                      cancel=None, deadline: float | None = None) -> dict:
        self._authorize(grant, "workspace.verify", root)
        if not all(shutil.which(command) for command in ("bwrap", "prlimit", "systemd-run")):
            raise RuntimeError("Isolamento bubblewrap/prlimit/systemd-run indisponível; execução recusada.")
        directory, normalized = self._target(root, test_root)
        if not directory.is_dir():
            raise ValueError("Diretório de testes ausente.")
        root = _workspace_root(root)
        timeout = max(1, min(300, int((deadline or time.monotonic() + 60) - time.monotonic())))
        args = ["/usr/bin/systemd-run", "--user", "--scope", "--quiet", "--property=MemoryMax=2G",
                "--property=TasksMax=64", "--property=CPUQuota=200%", "--",
                "/usr/bin/prlimit", "--as=2147483648", "--cpu=240", "--fsize=16777216", "--core=0", "--nofile=128", "--",
                "/usr/bin/bwrap", "--unshare-all", "--die-with-parent", "--new-session", "--cap-drop", "ALL", "--clearenv",
                "--ro-bind", "/usr", "/usr", "--symlink", "usr/bin", "/bin", "--symlink", "usr/lib", "/lib", "--symlink", "usr/lib", "/lib64",
                "--proc", "/proc", "--dev", "/dev",
                "--tmpfs", "/tmp",  # nosec: B108
                "--dir", "/home", "--dir", "/home/mark",
                "--setenv", "HOME", "/home/mark", "--setenv", "PATH", "/usr/bin:/bin", "--setenv", "PYTHONDONTWRITEBYTECODE", "1",
                "--ro-bind", str(root), "/workspace", "--chdir", "/workspace",
                "/usr/bin/python3", "-I", "-c",
                "import sys,unittest;sys.path[:0]=['/workspace','/workspace/src'];suite=unittest.defaultTestLoader.discover('/workspace/'+sys.argv[1]);result=unittest.TextTestRunner(verbosity=2).run(suite);sys.exit(not result.wasSuccessful())",
                normalized]
        host_env = {key: os.environ[key] for key in ("DBUS_SESSION_BUS_ADDRESS", "XDG_RUNTIME_DIR") if key in os.environ}
        host_env.update({"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"})
        process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                   start_new_session=True, env=host_env)
        started = time.monotonic()
        while process.poll() is None:
            if cancel is not None and cancel.is_set():
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
                return {"status": "CANCELLED", "executed": True, "duration_seconds": round(time.monotonic()-started, 2)}
            if time.monotonic() >= (deadline or started + timeout):
                os.killpg(process.pid, signal.SIGKILL)
                output, _ = process.communicate()
                return {"status": "TIMEOUT", "executed": True, "output": output[-12000:], "duration_seconds": round(time.monotonic()-started, 2)}
            time.sleep(0.1)
        output, _ = process.communicate()
        return {"status": "PASS" if process.returncode == 0 else "FAIL", "executed": True,
                "returncode": process.returncode, "test_root": normalized,
                "output": output[-12000:], "duration_seconds": round(time.monotonic()-started, 2)}

"""Armazenamento Local Seguro e Validação de Permissões POSIX (Fase P2).

Garante permissões estritas (0600 para arquivos, 0700 para diretórios),
rejeição categórica de links simbólicos (symlinks) e criação atômica
de arquivos temporários e privados via os.O_EXCL e os.O_NOFOLLOW.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import stat
from typing import Any


class StorageSecurityError(PermissionError):
    """Violação de segurança em arquivos, diretórios, symlinks ou permissões POSIX."""


def ensure_private_dir(path: Path | str) -> Path:
    """Cria e assegura permissões restritas (0700) para diretório pertencente ao usuário atual."""
    p = Path(path)
    if p.is_symlink():
        raise StorageSecurityError(f"Diretório não pode ser um symlink: {p}")
    p.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = p.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise StorageSecurityError(f"Diretório privado inválido ou pertence a outro usuário: {p}")
    p.chmod(0o700)
    return p


def verify_private_path(path: Path | str, *, is_dir: bool = False) -> None:
    """Verifica modo POSIX estrito (0600 para arquivo, 0700 para diretório), propriedade e ausência de symlinks."""
    p = Path(path)
    if p.is_symlink():
        raise StorageSecurityError(f"Symlinks são proibidos no armazenamento local: {p}")
    if not p.exists():
        raise StorageSecurityError(f"Caminho não encontrado para verificação: {p}")
    info = p.lstat()
    if info.st_uid != os.getuid():
        raise StorageSecurityError(f"Arquivo ou pasta não pertence ao usuário corrente: {p}")

    if is_dir:
        if not stat.S_ISDIR(info.st_mode):
            raise StorageSecurityError(f"Caminho esperado como diretório regular: {p}")
        if info.st_mode & 0o077:
            raise StorageSecurityError(f"Diretório deve possuir permissão estrita 0700: {p}")
    else:
        if not stat.S_ISREG(info.st_mode):
            raise StorageSecurityError(f"Caminho esperado como arquivo regular: {p}")
        if info.st_mode & 0o077:
            raise StorageSecurityError(f"Arquivo deve possuir permissão estrita 0600: {p}")


def private_file(path: Path | str) -> Path:
    """Garante arquivo regular privado (0600) em diretório privado (0700), sem symlinks."""
    p = Path(path)
    if p.is_symlink():
        raise StorageSecurityError(f"Arquivo alvo não pode ser um symlink: {p}")
    ensure_private_dir(p.parent)
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(p, flags, 0o600)
        os.close(fd)
    except FileExistsError:
        pass
    verify_private_path(p, is_dir=False)
    p.chmod(0o600)
    return p


def atomic_create_temp_file(dir_path: Path | str | None = None, prefix: str = "mark_tmp_") -> Path:
    """Cria atomicamente um arquivo temporário privado com O_CREAT | O_EXCL | O_NOFOLLOW e modo 0600."""
    target_dir = Path(dir_path) if dir_path is not None else (Path.home() / ".local/state/mark/tmp")
    ensure_private_dir(target_dir)

    for _ in range(100):
        candidate = target_dir / f"{prefix}{os.getpid()}_{secrets.token_hex(8)}"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(candidate, flags, 0o600)
            os.close(fd)
            candidate.chmod(0o600)
            return candidate
        except FileExistsError:
            continue
    raise StorageSecurityError("Não foi possível alocar arquivo temporário atômico único.")


def read_json(path: Path | str, max_bytes: int = 2_000_000, *, require_private: bool = False):
    p = Path(path)
    if require_private:
        verify_private_path(p, is_dir=False)
    info = p.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > max_bytes:
        raise ValueError("Arquivo JSON inválido ou muito grande.")
    return json.loads(
        p.read_text(encoding="utf-8"),
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Número JSON inválido")),
    )


def write_private_json(path: Path | str, data: Any):
    p = private_file(Path(path))
    tmp = atomic_create_temp_file(dir_path=p.parent, prefix=f".{p.name}_tmp_")
    flags = os.O_WRONLY | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(tmp, flags)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    tmp.chmod(0o600)
    os.replace(tmp, p)
    p.chmod(0o600)

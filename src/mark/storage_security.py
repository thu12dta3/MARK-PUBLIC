"""Re-exportação de utilitários de armazenamento e segurança POSIX."""

from .cognition.storage import (
    StorageSecurityError,
    atomic_create_temp_file,
    ensure_private_dir,
    private_file,
    read_json,
    verify_private_path,
    write_private_json,
)

__all__ = [
    "StorageSecurityError",
    "atomic_create_temp_file",
    "ensure_private_dir",
    "private_file",
    "read_json",
    "verify_private_path",
    "write_private_json",
]

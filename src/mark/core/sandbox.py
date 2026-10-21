"""Módulo de Isolamento Seguro de Execução a nível de Kernel (Bubblewrap / bwrap) - MARK Core.

Este módulo re-exporta a camada de execução isolada segura (ExecutionSandbox) para integração
direta no núcleo da arquitetura do MARK (mark.core.sandbox).
"""

from __future__ import annotations

from ..sandbox import (
    ALLOWED_BINARIES,
    DEFAULT_MAX_CPU_SECONDS,
    DEFAULT_MAX_FILE_SIZE_BYTES,
    DEFAULT_MAX_MEMORY_BYTES,
    DEFAULT_MAX_OPEN_FILES,
    DEFAULT_MAX_PROCESSES,
    DEFAULT_TIMEOUT_SECONDS,
    FORBIDDEN_FIND_FLAGS,
    FORBIDDEN_GIT_SUBCOMMANDS,
    FORBIDDEN_PATH_TOKENS,
    FORBIDDEN_PYTHON_NET_PATTERNS,
    MALICIOUS_COMMAND_PATTERNS,
    SAFE_ENV_KEYS,
    SENSITIVE_ENV_PATTERNS,
    ExecutionSandbox,
    SandboxLimits,
    SandboxResult,
    SandboxSecurityError,
    _probe_bwrap_support,
    sanitize_environment,
)

__all__ = [
    "ALLOWED_BINARIES",
    "DEFAULT_MAX_CPU_SECONDS",
    "DEFAULT_MAX_FILE_SIZE_BYTES",
    "DEFAULT_MAX_MEMORY_BYTES",
    "DEFAULT_MAX_OPEN_FILES",
    "DEFAULT_MAX_PROCESSES",
    "DEFAULT_TIMEOUT_SECONDS",
    "ExecutionSandbox",
    "FORBIDDEN_FIND_FLAGS",
    "FORBIDDEN_GIT_SUBCOMMANDS",
    "FORBIDDEN_PATH_TOKENS",
    "FORBIDDEN_PYTHON_NET_PATTERNS",
    "MALICIOUS_COMMAND_PATTERNS",
    "SAFE_ENV_KEYS",
    "SENSITIVE_ENV_PATTERNS",
    "SandboxLimits",
    "SandboxResult",
    "SandboxSecurityError",
    "_probe_bwrap_support",
    "sanitize_environment",
]

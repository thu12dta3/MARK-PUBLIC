"""Filtro Global de Higienização e Purga de Segredos em Logs e Diagnósticos do MARK (Fase P2).

Sanitiza e redige automaticamente:
1. Tokens de autorização Bearer e JWT (JSON Web Tokens).
2. Chaves privadas criptográficas (RSA, EC, DSA, OpenSSH, etc.).
3. Credenciais e senhas embutidas em URLs (ex: https://user:pass@host).
4. Parâmetros sensíveis em URLs (token, password, secret, api_key).
5. Atribuições chave-valor de credenciais (JSON, formulários e cabeçalhos).
6. Stack traces de exceções, redigindo mensagens de erro e variáveis sensíveis.
7. Relatórios de diagnóstico estruturados (dicionários, listas e strings).
"""

from __future__ import annotations

import copy
import logging
import re
import traceback
from typing import Any

# Expressões regulares consolidadas de sanitização
_RE_BEARER = re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._\-]{8,}\b")
_RE_JWT = re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]+\b")
_RE_PRIVATE_KEY = re.compile(
    r"-----BEGIN (?:[A-Z0-9_\-]+ )?PRIVATE KEY-----[\s\S]*?-----END (?:[A-Z0-9_\-]+ )?PRIVATE KEY-----",
    re.MULTILINE,
)
_RE_URL_AUTH = re.compile(r"(?i)(https?://)([^:\s/@]+):([^@\s/]+)(@)")
_RE_URL_PARAMS = re.compile(
    r"(?i)([?&](?:password|passwd|pwd|token|api_key|apikey|secret|access_token|client_secret)=)[^&\s\"'<>]+"
)
_RE_KV_SECRETS = re.compile(
    r"(?i)([\"']?(?:password|passwd|secret|api_key|apikey|access_token|client_secret|master_key|auth_token)[\"']?\s*[:=]\s*[\"']?)[^\"',\s}\n\r]+",
)

SENSITIVE_DICT_KEYS = frozenset({
    "password",
    "passwd",
    "pwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "access_token",
    "client_secret",
    "master_key",
    "auth_token",
    "private_key",
    "proof_b64",
    "session_token",
})


_DYNAMIC_SECRETS: set[str] = set()


def register_secret_for_redaction(secret: str) -> None:
    """Registra dinamicamente uma chave de API ou segredo para redação global em logs."""
    if isinstance(secret, str):
        cleaned = secret.strip()
        if len(cleaned) >= 6:
            _DYNAMIC_SECRETS.add(cleaned)


def clear_registered_secrets() -> None:
    """Limpa os segredos dinâmicos registrados (usado principalmente em testes)."""
    _DYNAMIC_SECRETS.clear()


def sanitize_log_text(text: str) -> str:
    """Higieniza uma string removendo tokens, chaves privadas, senhas e parâmetros sensíveis."""
    if not isinstance(text, str) or not text:
        return text or ""

    # 1. Chaves privadas
    sanitized = _RE_PRIVATE_KEY.sub("[REDACTED_PRIVATE_KEY]", text)

    # 2. Credenciais de URL (user:pass@host)
    sanitized = _RE_URL_AUTH.sub(r"\1\2:[REDACTED_PASSWORD]\4", sanitized)

    # 3. Parâmetros de URL com tokens/senhas
    sanitized = _RE_URL_PARAMS.sub(r"\1[REDACTED]", sanitized)

    # 4. Tokens Bearer
    sanitized = _RE_BEARER.sub(r"\1[REDACTED_TOKEN]", sanitized)

    # 5. JWTs
    sanitized = _RE_JWT.sub("[REDACTED_JWT]", sanitized)

    # 6. Atribuições sensíveis chave-valor
    sanitized = _RE_KV_SECRETS.sub(r"\1[REDACTED]", sanitized)

    # 7. Segredos dinamicamente registrados (chaves de API de LLMs do cofre/ambiente)
    for sec in _DYNAMIC_SECRETS:
        if sec in sanitized:
            sanitized = sanitized.replace(sec, "[REDACTED_API_KEY]")

    return sanitized


def sanitize_stack_trace(exc_info: Any) -> str:
    """Formata e higieniza stack trace de exceção, purificando mensagens e valores sensíveis."""
    if not exc_info:
        return ""
    if isinstance(exc_info, tuple) and len(exc_info) == 3:
        formatted = "".join(traceback.format_exception(*exc_info))
    elif isinstance(exc_info, BaseException):
        formatted = "".join(traceback.format_exception(type(exc_info), exc_info, exc_info.__traceback__))
    else:
        formatted = str(exc_info)
    return sanitize_log_text(formatted)


def sanitize_diagnostic_report(report: Any) -> Any:
    """Sanitiza recursivamente relatórios de diagnóstico (dicionários, listas ou strings)."""
    if isinstance(report, dict):
        sanitized_dict: dict[str, Any] = {}
        for k, v in report.items():
            key_str = str(k).lower().strip()
            if any(s in key_str for s in SENSITIVE_DICT_KEYS):
                sanitized_dict[k] = "[REDACTED]"
            else:
                sanitized_dict[k] = sanitize_diagnostic_report(v)
        return sanitized_dict
    elif isinstance(report, list):
        return [sanitize_diagnostic_report(item) for item in report]
    elif isinstance(report, tuple):
        return tuple(sanitize_diagnostic_report(item) for item in report)
    elif isinstance(report, str):
        return sanitize_log_text(report)
    return report


class LogSanitizerFilter(logging.Filter):
    """Filtro do logging da biblioteca padrão para interceptar e sanitizar mensagens e exceções."""

    def filter(self, record: logging.LogRecord) -> bool:
        # Sanitiza a mensagem principal
        if isinstance(record.msg, str):
            record.msg = sanitize_log_text(record.msg)

        # Sanitiza argumentos se houver
        if record.args:
            if isinstance(record.args, tuple):
                record.args = tuple(
                    sanitize_log_text(arg) if isinstance(arg, str) else arg
                    for arg in record.args
                )
            elif isinstance(record.args, dict):
                record.args = {
                    k: (sanitize_log_text(v) if isinstance(v, str) else v)
                    for k, v in record.args.items()
                }

        # Sanitiza stack traces anexados
        if record.exc_info:
            try:
                # Sanitiza a representação do erro na exceção
                exc_type, exc_val, exc_tb = record.exc_info
                if exc_val is not None and hasattr(exc_val, "args") and exc_val.args:
                    new_args = tuple(
                        sanitize_log_text(arg) if isinstance(arg, str) else arg
                        for arg in exc_val.args
                    )
                    try:
                        exc_val.args = new_args
                    except Exception:
                        pass
                record.exc_text = sanitize_stack_trace(record.exc_info)
            except Exception:
                pass

        if record.stack_info and isinstance(record.stack_info, str):
            record.stack_info = sanitize_log_text(record.stack_info)

        return True


def install_global_log_sanitizer(target_logger: logging.Logger | None = None) -> LogSanitizerFilter:
    """Instala o filtro de sanitização no logger raiz e nos handlers associados."""
    sanitizer = LogSanitizerFilter()
    root = target_logger if target_logger is not None else logging.getLogger()

    # Evita duplicação do filtro
    if not any(isinstance(f, LogSanitizerFilter) for f in root.filters):
        root.addFilter(sanitizer)

    for handler in root.handlers:
        if not any(isinstance(f, LogSanitizerFilter) for f in handler.filters):
            handler.addFilter(sanitizer)

    return sanitizer

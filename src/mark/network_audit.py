"""Auditoria de Interfaces, Portas de Escuta, Tailscale Serve e Isolamento de Loopback."""

from __future__ import annotations

import hmac
import re
from typing import Any, Mapping

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "::ffff:127.0.0.1", "localhost"})
FORBIDDEN_BIND_ADDRESSES = frozenset({"0.0.0.0", "::", "*"})  # nosec: B104
PROXY_OR_TAILSCALE_HEADERS = frozenset({
    "tailscale-user-login",
    "tailscale-user-name",
    "tailscale-headers-info",
    "x-forwarded-for",
    "x-forwarded-host",
    "x-real-ip",
    "forwarded",
})


class NetworkSecurityError(Exception):
    """Lançada quando uma interface de rede, porta ou configuração de rota viola as diretrizes de segurança."""


def audit_listening_interfaces(bindings: list[tuple[str, int]] | None = None) -> dict[str, Any]:
    """Audita todas as portas e interfaces de escuta registradas no MARK.

    Garante que nenhum serviço escute em interfaces públicas (0.0.0.0 ou ::)
    e que nenhuma porta HTTP plana (80) esteja ativa.
    """
    if bindings is None:
        # Padrões do sistema MARK: Mobile API (8765), Vite/God's Eye (4173), Ollama (11434)
        bindings = [("127.0.0.1", 8765), ("127.0.0.1", 4173)]

    findings = []
    audited = []
    for host, port in bindings:
        host_clean = str(host).strip().lower()
        audited.append({"host": host_clean, "port": port})
        if host_clean in FORBIDDEN_BIND_ADDRESSES:
            findings.append(f"Interface pública insegura detectada: {host_clean}:{port}. O MARK deve escutar apenas em 127.0.0.1.")
        if port == 80:
            findings.append("Porta HTTP 80 legada proibida. Todo o tráfego externo deve ser tunelado via Tailscale HTTPS 443.")
        if host_clean not in LOOPBACK_HOSTS:
            findings.append(f"Host não-loopback não autorizado: {host_clean}:{port}.")

    is_secure = len(findings) == 0
    return {
        "secure": is_secure,
        "audited_endpoints": audited,
        "findings": findings,
    }


def audit_tailscale_serve_configuration(serve_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Valida a configuração do Tailscale Serve contra tráfego inseguro e rotas alternativas."""
    cfg = serve_config or {
        "TCP": {"443": {"HTTPS": True}},
        "Web": {"https://mark.tailnet.ts.net:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8765"}}}},
        "AllowPlainHttp80": False,
        "MinTlsVersion": "TLSv1.3",
    }

    issues = []
    if cfg.get("AllowPlainHttp80") is True:
        issues.append("HTTP 80 plano permitido no Tailscale Serve. Deve ser estritamente False.")
    if "80" in cfg.get("TCP", {}):
        issues.append("Porta 80 aberta na tabela TCP do Tailscale Serve.")

    min_tls = cfg.get("MinTlsVersion", "")
    if min_tls != "TLSv1.3":
        issues.append(f"MinTlsVersion no Tailscale Serve deve ser 'TLSv1.3', obtido '{min_tls}'.")

    return {
        "secure": len(issues) == 0,
        "issues": issues,
        "tls_enforced": min_tls == "TLSv1.3",
        "plaintext_blocked": "80" not in cfg.get("TCP", {}) and not cfg.get("AllowPlainHttp80", False),
    }


def verify_loopback_access_gate(
    client_ip: str,
    headers: Mapping[str, str],
    expected_token: str,
) -> tuple[bool, str, int]:
    """Bloqueia qualquer interface ou rota alternativa que tente burlar o canal autenticado

    ou acessar serviços locais por loopback sem credenciais válidas.
    Retorna (autorizado, mensagem, status_code).
    """
    clean_ip = str(client_ip).strip().lower()
    norm_headers = {k.lower(): v for k, v in headers.items()}

    # 1. Bloqueio de tráfego HTTP plano mascarado por proxy
    proto = norm_headers.get("x-forwarded-proto", "").lower()
    port = norm_headers.get("x-forwarded-port", "")
    fwd = norm_headers.get("forwarded", "").lower()
    if proto == "http" or port == "80" or "proto=http" in fwd:
        return False, "Tráfego HTTP plano (porta 80) desativado. Use Tailscale Serve HTTPS com TLS 1.3.", 403

    # 2. Rejeição de tokens estáticos vindos via Tailscale ou proxy reverso
    if any(h in norm_headers for h in PROXY_OR_TAILSCALE_HEADERS):
        return False, "Tokens Bearer estáticos legados foram desativados no Tailscale. Use chave ECDSA rotativa vinculada ao aparelho.", 401

    # 3. Restrição exclusiva a conexões originadas no loopback
    if clean_ip not in LOOPBACK_HOSTS:
        return False, f"Acesso local por token restrito exclusivamente ao loopback. IP de origem '{clean_ip}' rejeitado.", 401

    # 4. Exigência estrita de token de autorização válido
    auth = norm_headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        return False, "Acesso por loopback exige credencial válida ('Authorization: Bearer <token>').", 401

    provided_token = auth[7:].strip()
    if not expected_token or not hmac.compare_digest(provided_token.encode("utf-8"), expected_token.encode("utf-8")):
        return False, "Chave de acesso por loopback inválida.", 401

    return True, "Acesso autorizado por loopback.", 200

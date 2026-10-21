"""Política de Isolamento de Aplicação e Allowlist do MARK Link."""

from __future__ import annotations

import enum
from typing import Any, Final, Optional

# Categoria de rotas para separação estrita de privilégios
class RouteCategory(str, enum.Enum):
    READ_ONLY = "READ_ONLY"
    MUTATE = "MUTATE"


# Rotas do ecossistema MARK estritamente de leitura (somente GET)
READONLY_ROUTES: Final[frozenset[str]] = frozenset({
    "/health",
    "/v1/history",
    "/v1/agenda",
    "/v1/pair/status",
    "/v1/wear/summary",
})

# Rotas do ecossistema MARK de mutação / escrita (somente POST)
MUTATE_ROUTES: Final[frozenset[str]] = frozenset({
    "/v1/chat/completions",
    "/v1/voice/turn",
    "/v1/session",
    "/v1/session/renew",
    "/v1/pair/enroll",
    "/v1/device/rotate",
    "/v1/wear/telemetry",
    "/call",
})

# Todas as rotas legítimas do ecossistema MARK
ALLOWED_MARK_ROUTES: Final[frozenset[str]] = READONLY_ROUTES | MUTATE_ROUTES

ALLOWED_METHODS: Final[frozenset[str]] = frozenset({"GET", "POST"})

MUTATION_QUERY_KEYWORDS: Final[frozenset[str]] = frozenset({
    "action=delete",
    "mutate=",
    "mutate=true",
    "mutate=1",
    "write=",
    "exec=",
    "admin=",
    "update=",
    "drop=",
    "force=",
})


class AccessPolicy:
    """Garante que apenas requisições aos serviços legítimos do MARK sejam processadas."""

    def __init__(self, custom_routes: frozenset[str] | None = None) -> None:
        self.allowed_routes = custom_routes or ALLOWED_MARK_ROUTES

    def _normalize_path(self, path: str) -> Optional[str]:
        if not isinstance(path, str) or not path.startswith("/") or "\x00" in path:
            return None
        # Defesa contra path traversal
        segments = path.split("?")[0].split("/")
        for seg in segments:
            if seg == "..":
                return None
        clean_path = path.split("?")[0].rstrip("/")
        return clean_path if clean_path else "/"

    def is_route_allowed(self, path: str) -> bool:
        clean_path = self._normalize_path(path)
        if clean_path is None:
            return False
        if clean_path not in self.allowed_routes:
            return False
        # Se contiver query string, rejeita mutações disfarçadas se o endpoint for ReadOnly
        if "?" in path:
            query = path.split("?", 1)[1].lower()
            if clean_path in READONLY_ROUTES:
                for kw in MUTATION_QUERY_KEYWORDS:
                    if kw in query:
                        return False
        return True

    def get_route_category(self, path: str) -> RouteCategory:
        clean_path = self._normalize_path(path)
        if clean_path is None or clean_path not in self.allowed_routes:
            raise PermissionError(f"Rota '{path}' proibida: acesso restrito a endpoints do MARK.")
        if clean_path in READONLY_ROUTES:
            return RouteCategory.READ_ONLY
        return RouteCategory.MUTATE

    def is_method_allowed(self, method: str) -> bool:
        if not isinstance(method, str):
            return False
        return method.strip().upper() in ALLOWED_METHODS

    def validate_request(self, method: str, path: str, body: bytes = b"") -> None:
        if not self.is_method_allowed(method):
            raise PermissionError(f"Método '{method}' não autorizado pelo MARK Link.")

        clean_path = self._normalize_path(path)
        if clean_path is None or clean_path not in self.allowed_routes:
            raise PermissionError(f"Rota '{path}' proibida: acesso restrito a endpoints do MARK.")

        category = self.get_route_category(clean_path)

        # 1. Endpoints ReadOnly: somente GET, sem corpo e sem query de mutação
        if category == RouteCategory.READ_ONLY:
            if method.strip().upper() != "GET":
                raise PermissionError("Mutação não permitida em endpoint ReadOnly (somente leitura: use GET).")
            if body and len(body.strip()) > 0:
                raise PermissionError("Payload não permitido em requisição ReadOnly (corpo não permitido).")
            if "?" in path:
                query = path.split("?", 1)[1].lower()
                for kw in MUTATION_QUERY_KEYWORDS:
                    if kw in query:
                        raise PermissionError(f"Argumento de mutação proibido em endpoint ReadOnly (query de mutação: '{kw}').")

        # 2. Endpoints Mutate: exigem POST
        elif category == RouteCategory.MUTATE:
            if method.strip().upper() != "POST":
                raise PermissionError(f"Método '{method}' não autorizado para endpoint de mutação (requer POST).")

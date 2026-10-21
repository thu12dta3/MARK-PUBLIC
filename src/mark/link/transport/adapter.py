"""Adaptador de Transporte do MARK Link para a API Móvel Local."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any, Dict, Optional, Tuple

from ..identity.allowlist import AccessPolicy, RouteCategory
from .idempotency import IdempotencyCache

logger = logging.getLogger("mark.link.transport.adapter")


class LinkTransportAdapter:
    """Encaminha requisições decifradas do MARK Link para a API local em 127.0.0.1:8765."""

    def __init__(
        self,
        target_base_url: str = "http://127.0.0.1:8765",
        policy: AccessPolicy | None = None,
        idempotency_cache: IdempotencyCache | None = None,
    ) -> None:
        self.target_base_url = target_base_url.rstrip("/")
        self.policy = policy or AccessPolicy()
        self.idempotency_cache = idempotency_cache or IdempotencyCache()

    def forward_request(
        self,
        method: str,
        path: str,
        headers: dict[str, str],
        body: bytes,
        device_id: str,
        request_id: str | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        # Validação estrita de isolamento de rota, método e integridade de mutação
        self.policy.validate_request(method, path, body=body)

        # 1. Verificação de Idempotência para operações de mutação (POST / Mutate)
        req_id = request_id or headers.get("X-Request-ID") or headers.get("x-request-id")
        is_mutation = False
        try:
            cat = self.policy.get_route_category(path)
            is_mutation = cat == RouteCategory.MUTATE or method.upper() == "POST"
        except Exception:
            is_mutation = method.upper() == "POST"

        if is_mutation and req_id:
            cached = self.idempotency_cache.get(req_id)
            if cached is not None:
                logger.info("Requisição idempotente detectada para %s (Request ID: %s)", path, req_id)
                replay_headers = dict(cached.headers)
                replay_headers["X-Mark-Idempotent-Replay"] = "true"
                return cached.status, replay_headers, cached.body

        url = f"{self.target_base_url}{path}"
        req_headers = {k: v for k, v in headers.items() if not k.lower().startswith("host")}
        req_headers["X-Mark-Device"] = device_id
        req_headers["X-Mark-Link-Transport"] = "direct-v1"
        if req_id:
            req_headers["X-Request-ID"] = req_id

        req = urllib.request.Request(
            url,
            data=body if method.upper() == "POST" else None,
            headers=req_headers,
            method=method.upper(),
        )

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                status_code = resp.status
                resp_headers = dict(resp.headers)
                resp_body = resp.read()
                if is_mutation and req_id:
                    self.idempotency_cache.put(req_id, status_code, resp_headers, resp_body)
                return status_code, resp_headers, resp_body
        except urllib.error.HTTPError as err:
            status_code = err.code
            resp_headers = dict(err.headers)
            resp_body = err.read()
            if is_mutation and req_id and status_code < 500:
                self.idempotency_cache.put(req_id, status_code, resp_headers, resp_body)
            return status_code, resp_headers, resp_body
        except Exception as exc:
            logger.warning("Falha ao contatar serviço local do MARK: %s", exc)
            err_payload = json.dumps({
                "error": {"message": f"Serviço MARK indisponível: {exc}", "type": "link_error"}
            }).encode("utf-8")
            return 503, {"Content-Type": "application/json"}, err_payload

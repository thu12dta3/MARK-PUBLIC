"""Módulo de Idempotência e Cache LRU para Requisições do MARK Link (Marco C).

Garante que operações de mutação (ex: acionamento de dispositivos, envio de mensagens)
não sejam executadas duplicadamente em caso de quedas de rede e retransmissões do cliente.
"""

from __future__ import annotations

import collections
import logging
import threading
import time
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("mark.link.transport.idempotency")


class CachedResponse:
    """Representa uma resposta de requisição cacheada para replay idempotente."""

    def __init__(
        self,
        status: int,
        headers: Dict[str, str],
        body: bytes,
        created_at: float | None = None,
    ) -> None:
        self.status = status
        self.headers = headers
        self.body = body
        self.created_at = created_at if created_at is not None else time.time()

    def is_expired(self, ttl_seconds: float) -> bool:
        return (time.time() - self.created_at) > ttl_seconds


class IdempotencyCache:
    """Cache thread-safe com política de despejo LRU e expiração por tempo (TTL)."""

    def __init__(self, max_entries: int = 1024, ttl_seconds: float = 600.0) -> None:
        self.max_entries = max_entries
        self.ttl_seconds = ttl_seconds
        self._entries: collections.OrderedDict[str, CachedResponse] = collections.OrderedDict()
        self._lock = threading.Lock()

    def get(self, request_id: str) -> CachedResponse | None:
        """Recupera uma resposta cacheada pelo Request ID, renovando sua posição no LRU."""
        if not request_id:
            return None
        with self._lock:
            cached = self._entries.get(request_id)
            if cached is None:
                return None
            if cached.is_expired(self.ttl_seconds):
                del self._entries[request_id]
                return None
            self._entries.move_to_end(request_id)
            return cached

    def put(
        self,
        request_id: str,
        status: int,
        headers: Dict[str, str],
        body: bytes,
    ) -> None:
        """Armazena a resposta de uma requisição executada."""
        if not request_id:
            return
        with self._lock:
            if request_id in self._entries:
                del self._entries[request_id]
            elif len(self._entries) >= self.max_entries:
                # Remove o item mais antigo (LRU)
                self._entries.popitem(last=False)

            self._entries[request_id] = CachedResponse(
                status=status,
                headers=dict(headers),
                body=body,
            )

    def contains(self, request_id: str) -> bool:
        """Verifica se o Request ID já foi processado e permanece válido na cache."""
        return self.get(request_id) is not None

    def clear(self) -> None:
        """Limpa todas as entradas da cache."""
        with self._lock:
            self._entries.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

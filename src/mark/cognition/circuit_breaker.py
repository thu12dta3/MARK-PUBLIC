"""Módulo de Circuit Breaker para Resiliência Cognitiva e Fallback de Inferência.

Implementa a máquina de estados (FECHADO, ABERTO, MEIO-ABERTO) para proteger
a camada de inferência do MARK contra quedas do serviço local (Ollama) ou
sobrecargas nas APIs de nuvem, redirecionando o fluxo de forma fluida e assíncrona.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from enum import Enum
import json
import logging
import time
from typing import Any, Callable
import urllib.error
import urllib.request

from ..url_security import validate_url_scheme

logger = logging.getLogger("mark.cognition.circuit_breaker")


class CircuitState(str, Enum):
    CLOSED = "FECHADO"       # Operação normal: requisições fluem para o provedor primário
    OPEN = "ABERTO"          # Falhas consecutivas excederam o limiar: requisições são desviadas imediatamente
    HALF_OPEN = "MEIO_ABERTO" # Período de teste: permite sondagem para verificar se o serviço se recuperou


class CircuitBreakerOpenError(Exception):
    """Exceção levantada quando o circuito está ABERTO e uma chamada direta é rejeitada pelo fail-fast."""
    def __init__(self, message: str = "Circuito ABERTO: provedor indisponível ou em recuperação.", provider: str = "ollama") -> None:
        super().__init__(message)
        self.provider = provider


class CircuitBreaker:
    """Circuit Breaker para provedores de inferência LLM (Ollama / Cloud)."""

    def __init__(
        self,
        name: str = "ollama",
        failure_threshold: int = 3,
        recovery_timeout: float = 15.0,
        health_check_timeout: float = 2.0,
    ) -> None:
        self.name = name
        self.failure_threshold = max(1, int(failure_threshold))
        self.recovery_timeout = max(0.01, float(recovery_timeout))
        self.health_check_timeout = max(0.01, float(health_check_timeout))

        self._state: CircuitState = CircuitState.CLOSED
        self._failures_count: int = 0
        self._consecutive_successes: int = 0
        self._last_state_change: float = time.monotonic()
        self._last_failure_time: float = 0.0
        self._last_error: str | None = None
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix=f"cb-{name}")

    @property
    def state(self) -> CircuitState:
        """Retorna o estado atual considerando a expiração automática de recuperação (OPEN -> HALF_OPEN)."""
        if self._state == CircuitState.OPEN:
            elapsed = time.monotonic() - self._last_state_change
            if elapsed >= self.recovery_timeout:
                logger.info(
                    "Circuit Breaker '%s': tempo de recuperação esgotado (%.1fs). Transicionando ABERTO -> MEIO_ABERTO para teste.",
                    self.name,
                    elapsed,
                )
                self._state = CircuitState.HALF_OPEN
                self._last_state_change = time.monotonic()
        return self._state

    @property
    def is_open(self) -> bool:
        return self.state == CircuitState.OPEN

    @property
    def is_closed(self) -> bool:
        return self.state == CircuitState.CLOSED

    @property
    def is_half_open(self) -> bool:
        return self.state == CircuitState.HALF_OPEN

    def can_execute(self) -> bool:
        """Determina se o provedor pode ser chamado ou se o circuito está bloqueando requisições."""
        current = self.state
        if current == CircuitState.CLOSED:
            return True
        if current == CircuitState.HALF_OPEN:
            return True
        return False

    def record_success(self) -> None:
        """Registra uma execução ou health-check bem-sucedido."""
        if self._state == CircuitState.HALF_OPEN:
            logger.info("Circuit Breaker '%s': teste bem-sucedido. Transicionando MEIO_ABERTO -> FECHADO.", self.name)
            self._state = CircuitState.CLOSED
            self._last_state_change = time.monotonic()

        self._failures_count = 0
        self._consecutive_successes += 1
        self._last_error = None

    def record_failure(self, exc: Exception | None = None) -> None:
        """Registra uma falha (timeout, connection refused, 500, etc.) e atualiza o estado."""
        self._failures_count += 1
        self._consecutive_successes = 0
        self._last_failure_time = time.monotonic()
        if exc is not None:
            self._last_error = f"{type(exc).__name__}: {exc}"

        logger.warning(
            "Circuit Breaker '%s': falha registrada (%d/%d). Detalhes: %s",
            self.name,
            self._failures_count,
            self.failure_threshold,
            self._last_error or "desconhecido",
        )

        if self._state == CircuitState.HALF_OPEN:
            logger.warning(
                "Circuit Breaker '%s': teste falhou no estado MEIO_ABERTO. Reabrindo circuito imediatamente para ABERTO.",
                self.name,
            )
            self._state = CircuitState.OPEN
            self._last_state_change = time.monotonic()

        elif self._state == CircuitState.CLOSED and self._failures_count >= self.failure_threshold:
            logger.error(
                "Circuit Breaker '%s': limiar de falhas atingido (%d >= %d). TRIPANDO circuito para ABERTO.",
                self.name,
                self._failures_count,
                self.failure_threshold,
            )
            self._state = CircuitState.OPEN
            self._last_state_change = time.monotonic()

    def trip(self, reason: str = "Acionamento manual / teste") -> None:
        """Força a abertura imediata do circuito para o estado ABERTO."""
        self._state = CircuitState.OPEN
        self._last_state_change = time.monotonic()
        self._last_error = reason
        logger.warning("Circuit Breaker '%s' forçado para ABERTO. Razão: %s", self.name, reason)

    def reset(self) -> None:
        """Reinicia o circuito forçando o estado FECHADO e zerando falhas."""
        self._state = CircuitState.CLOSED
        self._failures_count = 0
        self._last_state_change = time.monotonic()
        self._last_error = None
        logger.info("Circuit Breaker '%s' resetado para FECHADO.", self.name)

    def check_health(self, base_url: str = "http://127.0.0.1:11434", timeout: float | None = None) -> bool:
        """Executa verificação síncrona de integridade no endpoint de health-check do provedor."""
        probe_timeout = float(timeout) if timeout is not None else self.health_check_timeout
        probe_url = base_url.rstrip("/") + "/api/tags"
        try:
            req = urllib.request.Request(
                probe_url,
                headers={"Accept": "application/json", "User-Agent": "MARK-HealthCheck/1.0"},
            )
            validate_url_scheme(req)
            with urllib.request.urlopen(req, timeout=probe_timeout) as resp:  # nosec: B310
                if resp.status == 200:
                    self.record_success()
                    return True
                self.record_failure(RuntimeError(f"HTTP Status {resp.status}"))
                return False
        except Exception as exc:
            self.record_failure(exc)
            return False

    def async_health_check(
        self,
        base_url: str = "http://127.0.0.1:11434",
        timeout: float | None = None,
        callback: Callable[[bool], None] | None = None,
    ):
        """Executa a verificação de saúde em thread separada de forma não bloqueante."""
        def _run():
            ok = self.check_health(base_url=base_url, timeout=timeout)
            if callback:
                try:
                    callback(ok)
                except Exception as cb_err:
                    logger.debug("Erro no callback de health check: %s", cb_err)
            return ok

        return self._executor.submit(_run)

    def get_status(self) -> dict[str, Any]:
        """Retorna o snapshot de telemetria do Circuit Breaker."""
        return {
            "name": self.name,
            "state": self.state.value,
            "can_execute": self.can_execute(),
            "failures_count": self._failures_count,
            "failure_threshold": self.failure_threshold,
            "recovery_timeout": self.recovery_timeout,
            "last_error": self._last_error,
            "seconds_since_state_change": round(time.monotonic() - self._last_state_change, 2),
        }

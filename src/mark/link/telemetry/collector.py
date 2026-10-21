"""Coletor de Telemetria, Métricas Locais e Diagnóstico Segregado do MARK Link."""

from __future__ import annotations

import os
import re
import threading
import time
from dataclasses import asdict, dataclass
from typing import Any

from ..peer.state import PeerState

_PEM_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN (?:[A-Z0-9_\-]+ )?PRIVATE KEY-----[\s\S]*?-----END (?:[A-Z0-9_\-]+ )?PRIVATE KEY-----"
)
_HEX_KEY_RE = re.compile(r"\b[a-fA-F0-9]{32,128}\b")
_JWT_LIKE_RE = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")


@dataclass
class LayerStatus:
    """Estado de diagnóstico de uma camada individual de rede."""

    status: str  # "ok", "degraded", "down", "disconnected", "standby"
    error: str | None = None
    details: str | None = None


@dataclass
class NetworkLayersReport:
    """Diagnóstico consolidado das camadas com separação explícita de falhas."""

    service: LayerStatus
    loopback: LayerStatus
    lan: LayerStatus
    direct: LayerStatus
    relay: LayerStatus
    auth: LayerStatus


@dataclass
class DiagnosticReport:
    state: str
    mode: str | None
    peer_id_masked: str
    rtt_ms: float | None
    bytes_sent: int
    bytes_received: int
    packets_dropped: int
    reconnect_count: int
    memory_rss_kb: int
    uptime_seconds: float
    last_error: str | None
    layers: NetworkLayersReport


class LinkTelemetryCollector:
    """Coletor atômico de métricas e diagnósticos segregados sem vazamento de segredos."""

    def __init__(self) -> None:
        self.start_time = time.time()
        self.bytes_sent = 0
        self.bytes_received = 0
        self.packets_dropped = 0
        self.reconnect_count = 0
        self.rtt_ms: float | None = None
        self.state = PeerState.DISCONNECTED
        self.peer_id: str | None = None
        self.last_error: str | None = None
        self.lock = threading.Lock()

        # Camadas com isolamento de status e falhas
        self._layer_statuses: dict[str, LayerStatus] = {
            "service": LayerStatus(status="ok"),
            "loopback": LayerStatus(status="ok"),
            "lan": LayerStatus(status="standby"),
            "direct": LayerStatus(status="disconnected"),
            "relay": LayerStatus(status="standby"),
            "auth": LayerStatus(status="ok"),
        }

    def set_peer(self, peer_id: str) -> None:
        with self.lock:
            self.peer_id = peer_id

    def set_state(self, state: PeerState) -> None:
        with self.lock:
            self.state = state
            if state == PeerState.LOOPBACK:
                self._layer_statuses["loopback"] = LayerStatus(
                    status="ok", details="Loopback connection active"
                )
            elif state in {
                PeerState.DIRECT_LOCAL,
                PeerState.DIRECT_REMOTE,
            }:
                self._layer_statuses["direct"] = LayerStatus(
                    status="ok", details=state.value
                )
            elif state == PeerState.RELAYED:
                self._layer_statuses["relay"] = LayerStatus(
                    status="ok", details="Envelope relay active"
                )
            elif state == PeerState.DISCONNECTED:
                self._layer_statuses["direct"] = LayerStatus(status="disconnected")

    def record_bytes_sent(self, count: int) -> None:
        with self.lock:
            self.bytes_sent += count

    def record_bytes_received(self, count: int) -> None:
        with self.lock:
            self.bytes_received += count

    def record_drop(self) -> None:
        with self.lock:
            self.packets_dropped += 1

    def record_reconnect(self) -> None:
        with self.lock:
            self.reconnect_count += 1

    def set_rtt(self, rtt_ms: float) -> None:
        with self.lock:
            self.rtt_ms = rtt_ms

    def record_error(self, err_msg: str) -> None:
        sanitized = self._sanitize(err_msg)
        with self.lock:
            self.last_error = sanitized

    def set_layer_status(
        self,
        layer: str,
        status: str,
        error: str | None = None,
        details: str | None = None,
    ) -> None:
        sanitized_err = self._sanitize(error) if error else None
        sanitized_det = self._sanitize(details) if details else None
        with self.lock:
            self._layer_statuses[layer] = LayerStatus(
                status=status, error=sanitized_err, details=sanitized_det
            )

    def record_service_failure(self, error: str) -> None:
        self.set_layer_status("service", "down", error=error)

    def record_lan_failure(self, error: str) -> None:
        self.set_layer_status("lan", "degraded", error=error)

    def record_direct_failure(self, error: str) -> None:
        self.set_layer_status("direct", "down", error=error)

    def record_relay_failure(self, error: str) -> None:
        self.set_layer_status("relay", "degraded", error=error)

    def record_auth_failure(self, error: str) -> None:
        self.set_layer_status("auth", "degraded", error=error)

    @classmethod
    def _sanitize(cls, text: str) -> str:
        """Sanitiza mensagens de log e diagnósticos eliminando chaves, tokens e segredos."""
        if not text:
            return ""

        lower = text.lower()
        for term in (
            "token",
            "key",
            "secret",
            "bearer",
            "password",
            "private",
            "passwd",
        ):
            if term in lower:
                return f"[REDACTED_DIAGNOSTIC_INFO: {term}]"

        if _HEX_KEY_RE.search(text):
            return "[REDACTED_DIAGNOSTIC_INFO: hex_key]"

        if _JWT_LIKE_RE.search(text):
            return "[REDACTED_DIAGNOSTIC_INFO: jwt_token]"

        if _PEM_PRIVATE_KEY_RE.search(text):
            return "[REDACTED_DIAGNOSTIC_INFO: pem_private_key]"

        return text

    @staticmethod
    def get_memory_rss_kb() -> int:
        try:
            with open("/proc/self/statm", "r", encoding="utf-8") as f:
                parts = f.read().split()
                if len(parts) >= 2:
                    return int(parts[1]) * 4  # 4 KB páginas
        except Exception:
            pass
        return 0

    def generate_report(
        self, layers_override: NetworkLayersReport | None = None
    ) -> DiagnosticReport:
        with self.lock:
            masked = "none"
            if self.peer_id:
                masked = (
                    f"{self.peer_id[:6]}…{self.peer_id[-4:]}"
                    if len(self.peer_id) > 10
                    else self.peer_id
                )

            mode_str = None
            if self.state in {
                PeerState.LOOPBACK,
                PeerState.DIRECT_LOCAL,
                PeerState.DIRECT_REMOTE,
                PeerState.RELAYED,
            }:
                mode_str = self.state.value

            layers = layers_override or NetworkLayersReport(
                service=self._layer_statuses.get(
                    "service", LayerStatus(status="ok")
                ),
                loopback=self._layer_statuses.get(
                    "loopback", LayerStatus(status="ok")
                ),
                lan=self._layer_statuses.get(
                    "lan", LayerStatus(status="standby")
                ),
                direct=self._layer_statuses.get(
                    "direct", LayerStatus(status="disconnected")
                ),
                relay=self._layer_statuses.get(
                    "relay", LayerStatus(status="standby")
                ),
                auth=self._layer_statuses.get("auth", LayerStatus(status="ok")),
            )

            return DiagnosticReport(
                state=self.state.value,
                mode=mode_str,
                peer_id_masked=masked,
                rtt_ms=round(self.rtt_ms, 2) if self.rtt_ms is not None else None,
                bytes_sent=self.bytes_sent,
                bytes_received=self.bytes_received,
                packets_dropped=self.packets_dropped,
                reconnect_count=self.reconnect_count,
                memory_rss_kb=self.get_memory_rss_kb(),
                uptime_seconds=round(time.time() - self.start_time, 2),
                last_error=self.last_error,
                layers=layers,
            )

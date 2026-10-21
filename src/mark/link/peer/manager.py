"""Gerenciador de Ciclo de Vida e Estados de Pares do MARK Link."""

from __future__ import annotations

import random
import time
from typing import Any, Dict, Final, List, Optional, Tuple

from .state import CandidateType, ConnectionCandidate, PeerState


class LinkPeerManager:
    """Controla estados, keepalive, reconexão e seleção de transporte para um par remoto."""

    def __init__(
        self,
        peer_id: str,
        idle_threshold_secs: float = 30.0,
        active_ping_secs: float = 5.0,
        idle_ping_secs: float = 30.0,
    ) -> None:
        self.peer_id = peer_id
        self.state = PeerState.DISCONNECTED
        self.candidates: list[ConnectionCandidate] = []
        self.last_activity = time.time()
        self.last_ping_sent: float | None = None
        self.rtt_ms: float | None = None
        self.reconnect_attempts = 0
        self.idle_threshold_secs = idle_threshold_secs
        self.active_ping_secs = active_ping_secs
        self.idle_ping_secs = idle_ping_secs

    def add_candidate(self, candidate: ConnectionCandidate) -> None:
        if not any(c.address == candidate.address for c in self.candidates):
            self.candidates.append(candidate)
            self.candidates.sort(key=lambda c: c.priority, reverse=True)

    def set_connected(self, candidate_type: CandidateType) -> None:
        if candidate_type == CandidateType.LOOPBACK:
            self.state = PeerState.LOOPBACK
        elif candidate_type == CandidateType.LOCAL_LAN:
            self.state = PeerState.DIRECT_LOCAL
        elif candidate_type == CandidateType.PUBLIC_REFLEXIVE:
            self.state = PeerState.DIRECT_REMOTE
        else:
            self.state = PeerState.RELAYED
        self.reconnect_attempts = 0
        self.last_activity = time.time()

    def set_disconnected(self) -> None:
        self.state = PeerState.DISCONNECTED

    def trigger_reconnect(self) -> float:
        self.state = PeerState.RECONNECTING
        self.reconnect_attempts += 1
        return self.calculate_backoff(self.reconnect_attempts)

    @staticmethod
    def calculate_backoff(
        attempt: int,
        base_ms: float = 1000.0,
        max_ms: float = 30000.0,
        jitter_ratio: float = 0.20,
    ) -> float:
        exp = 2 ** min(attempt, 6)
        nominal_ms = min(base_ms * exp, max_ms)
        jitter = random.uniform(-jitter_ratio, jitter_ratio)
        actual_ms = max(nominal_ms * (1.0 + jitter), 100.0)
        return actual_ms / 1000.0

    def current_ping_interval(self) -> float:
        elapsed = time.time() - self.last_activity
        if elapsed >= self.idle_threshold_secs:
            return self.idle_ping_secs  # Reduz frequência em repouso para economizar bateria
        return self.active_ping_secs

    def record_activity(self, now: float | None = None) -> None:
        """Registra tráfego de dados para manter o keepalive em modo de baixa latência (ativo)."""
        self.last_activity = time.time() if now is None else now

    def mark_ping_sent(self) -> None:
        self.last_ping_sent = time.time()

    def handle_pong(self) -> float | None:
        if self.last_ping_sent is not None:
            now = time.time()
            measured = (now - self.last_ping_sent) * 1000.0
            self.last_ping_sent = None
            if self.rtt_ms is None:
                self.rtt_ms = measured
            else:
                self.rtt_ms = (self.rtt_ms * 3.0 + measured) / 4.0
            self.last_activity = now
            return self.rtt_ms
        return None

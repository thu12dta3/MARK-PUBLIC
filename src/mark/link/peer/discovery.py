"""Descoberta de Endpoints e Coleta de Candidatos para o MARK Link."""

from __future__ import annotations

import logging
import socket
from typing import Any

from .state import CandidateType, ConnectionCandidate

logger = logging.getLogger("mark.link.peer.discovery")


class EndpointDiscovery:
    """Descobre e gera os candidatos de conexão para o nó local."""

    @staticmethod
    def get_local_lan_ip() -> str:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("10.255.255.255", 1))
                return s.getsockname()[0]
        except Exception:
            return "127.0.0.1"

    @classmethod
    def gather_candidates(
        cls,
        local_port: int = 8766,
        relay_address: str | None = None,
        public_ip: str | None = None,
    ) -> list[ConnectionCandidate]:
        candidates: list[ConnectionCandidate] = []

        # 1. Candidato Local LAN (Prioridade Máxima = 100)
        lan_ip = cls.get_local_lan_ip()
        candidates.append(ConnectionCandidate(CandidateType.LOCAL_LAN, f"{lan_ip}:{local_port}"))

        # 2. Candidato Reflexivo Público (STUN / WAN) (Prioridade Média = 50)
        if public_ip:
            candidates.append(ConnectionCandidate(CandidateType.PUBLIC_REFLEXIVE, f"{public_ip}:{local_port}"))

        # 3. Candidato de Relay Cego de Contingência (Prioridade = 10)
        if relay_address:
            candidates.append(ConnectionCandidate(CandidateType.RELAY_SERVER, relay_address))

        # Ordena decrescente por prioridade
        candidates.sort(key=lambda c: c.priority, reverse=True)
        return candidates

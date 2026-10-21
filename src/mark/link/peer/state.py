"""Estados da Máquina de Estados e Candidatos de Conexão do MARK Link."""

from __future__ import annotations

from enum import Enum, IntEnum
from typing import Any


class PeerState(str, Enum):
    DISCONNECTED = "Disconnected"
    CONNECTING = "Connecting"
    LOOPBACK = "Loopback"
    DIRECT_LOCAL = "DirectLocal"
    DIRECT_REMOTE = "DirectRemote"
    RELAYED = "Relayed"
    SUSPENDED = "Suspended"
    RECONNECTING = "Reconnecting"

    @property
    def is_connected(self) -> bool:
        return self in {
            PeerState.LOOPBACK,
            PeerState.DIRECT_LOCAL,
            PeerState.DIRECT_REMOTE,
            PeerState.RELAYED,
        }

    @property
    def is_direct(self) -> bool:
        return self in {
            PeerState.LOOPBACK,
            PeerState.DIRECT_LOCAL,
            PeerState.DIRECT_REMOTE,
        }


class CandidateType(IntEnum):
    RELAY_SERVER = 10
    PUBLIC_REFLEXIVE = 50
    LOCAL_LAN = 100
    LOOPBACK = 200


class ConnectionCandidate:
    """Candidato de endereço de rede para estabelecimento de transporte."""

    def __init__(self, candidate_type: CandidateType, address: str, priority: int | None = None) -> None:
        self.candidate_type = candidate_type
        self.address = address
        self.priority = priority if priority is not None else int(candidate_type)

    @classmethod
    def loopback(cls, address: str = "127.0.0.1:8766") -> ConnectionCandidate:
        return cls(CandidateType.LOOPBACK, address, priority=int(CandidateType.LOOPBACK))

    @classmethod
    def local_lan(cls, address: str) -> ConnectionCandidate:
        return cls(CandidateType.LOCAL_LAN, address, priority=int(CandidateType.LOCAL_LAN))

    @classmethod
    def public_reflexive(cls, address: str) -> ConnectionCandidate:
        return cls(CandidateType.PUBLIC_REFLEXIVE, address, priority=int(CandidateType.PUBLIC_REFLEXIVE))

    @classmethod
    def relay(cls, address: str) -> ConnectionCandidate:
        return cls(CandidateType.RELAY_SERVER, address, priority=int(CandidateType.RELAY_SERVER))

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.candidate_type.name,
            "address": self.address,
            "priority": self.priority,
        }

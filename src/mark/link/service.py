"""Serviço Central e Orquestrador do MARK Link."""

from __future__ import annotations

import logging
import secrets
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from .identity import AccessPolicy, LinkDeviceManager
from .peer import CandidateType, EndpointDiscovery, LinkPeerManager, PeerState
from .relay import BlindRelayRouter, RelayClient
from .telemetry import (
    DiagnosticReport,
    LayerStatus,
    LinkTelemetryCollector,
    NetworkLayersReport,
)
from .transport import (
    DirectClient,
    DirectListener,
    DirectionalSecureChannel,
    LinkTransportAdapter,
    SecureChannel,
)

logger = logging.getLogger("mark.link.service")


class MarkLinkService:
    """Orquestrador do subsistema de conectividade privada MARK Link.

    Substitui a necessidade de Tailscale para a comunicação entre MARK PC e MARK Mobile.
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8766,
        api_target_url: str = "http://127.0.0.1:8765",
        device_manager: LinkDeviceManager | None = None,
        policy: AccessPolicy | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.device_manager = device_manager or LinkDeviceManager()
        self.policy = policy or AccessPolicy()
        self.telemetry = LinkTelemetryCollector()
        self.relay_router = BlindRelayRouter()

        self.adapter = LinkTransportAdapter(target_base_url=api_target_url, policy=self.policy)
        self.listener = DirectListener(
            host=self.host,
            port=self.port,
            adapter=self.adapter,
            device_manager=self.device_manager,
        )
        self.peer_managers: dict[str, LinkPeerManager] = {}
        self.running = False

    @property
    def server_public_der(self) -> bytes:
        """Retorna a chave pública DER do hub/servidor MARK Link."""
        return self.listener.server_public_der

    def start(self) -> None:
        if self.running:
            return
        self.listener.start()
        self.running = True
        logger.info("MARK Link Service iniciado com sucesso.")

    def stop(self) -> None:
        if not self.running:
            return
        self.listener.stop()
        self.running = False
        self.telemetry.set_state(PeerState.DISCONNECTED)
        logger.info("MARK Link Service encerrado.")

    def get_or_create_peer_manager(self, peer_id: str) -> LinkPeerManager:
        if peer_id not in self.peer_managers:
            pm = LinkPeerManager(peer_id)
            # Adiciona candidatos de conexão
            candidates = EndpointDiscovery.gather_candidates(
                local_port=self.port,
                relay_address=f"{self.host}:{self.port}/relay",
            )
            for c in candidates:
                pm.add_candidate(c)
            self.peer_managers[peer_id] = pm
        return self.peer_managers[peer_id]

    def authorize_connection(self, device_id: str) -> bool:
        """Verifica se o dispositivo tem autorização ativa no ecossistema MARK."""
        if not self.device_manager.is_authorized(device_id):
            logger.warning("Tentativa de conexão de dispositivo não autorizado ou revogado: %s", device_id)
            self.telemetry.record_drop()
            self.telemetry.record_error(f"Dispositivo não autorizado: {device_id[:8]}…")
            return False
        self.telemetry.set_peer(device_id)
        return True

    def establish_peer_session(
        self,
        device_id: str,
        preferred_mode: CandidateType = CandidateType.LOCAL_LAN,
        peer_ephemeral_pub_der: bytes | None = None,
    ) -> DirectionalSecureChannel:
        """Negocia canal criptografado autenticado para o dispositivo autorizado com segredos efêmeros."""
        if not self.authorize_connection(device_id):
            raise PermissionError("Dispositivo não autorizado ou revogado no MARK.")

        pm = self.get_or_create_peer_manager(device_id)
        pm.set_connected(preferred_mode)

        if preferred_mode == CandidateType.LOOPBACK:
            self.telemetry.set_state(PeerState.LOOPBACK)
        elif preferred_mode == CandidateType.LOCAL_LAN:
            self.telemetry.set_state(PeerState.DIRECT_LOCAL)
        elif preferred_mode == CandidateType.PUBLIC_REFLEXIVE:
            self.telemetry.set_state(PeerState.DIRECT_REMOTE)
        else:
            self.telemetry.set_state(PeerState.RELAYED)

        # Troca efêmera ECDH P-256 e geração de nonces independentes
        local_eph = ec.generate_private_key(ec.SECP256R1())
        if peer_ephemeral_pub_der:
            remote_pub = serialization.load_der_public_key(peer_ephemeral_pub_der)
        else:
            remote_pub = ec.generate_private_key(ec.SECP256R1()).public_key()

        shared_secret = local_eph.exchange(ec.ECDH(), remote_pub)
        client_nonce = secrets.token_bytes(32)
        server_nonce = secrets.token_bytes(32)

        return DirectionalSecureChannel.derive(
            shared_secret=shared_secret,
            client_nonce=client_nonce,
            server_nonce=server_nonce,
            is_server=True,
        )

    def compile_layer_diagnostics(self) -> NetworkLayersReport:
        """Compila o diagnóstico em tempo real segregando falhas por camada de rede."""
        layers = self.telemetry._layer_statuses
        service_st = layers.get("service", LayerStatus(status="ok"))
        if not service_st.error:
            if self.running:
                service_st = LayerStatus(
                    status="ok", details=f"Daemon active on {self.host}:{self.port}"
                )
            else:
                service_st = LayerStatus(status="down", details="Daemon stopped")

        loopback_st = layers.get("loopback", LayerStatus(status="ok"))
        if not loopback_st.error:
            loopback_st = LayerStatus(
                status="ok" if self.host in {"127.0.0.1", "localhost", "::1"} else "degraded",
                details=f"Host bound to {self.host}:{self.port}",
            )

        lan_st = layers.get("lan", LayerStatus(status="standby"))
        direct_st = layers.get("direct", LayerStatus(status="disconnected"))
        relay_st = layers.get("relay", LayerStatus(status="standby"))
        auth_st = layers.get("auth", LayerStatus(status="ok"))

        return NetworkLayersReport(
            service=service_st,
            loopback=loopback_st,
            lan=lan_st,
            direct=direct_st,
            relay=relay_st,
            auth=auth_st,
        )

    def get_diagnostics(self) -> DiagnosticReport:
        layers = self.compile_layer_diagnostics()
        return self.telemetry.generate_report(layers_override=layers)


//! MARK Link — Camada Própria de Conectividade Privada em Rust do Ecossistema MARK.
//!
//! Substitui a dependência do Tailscale, garantindo:
//! 1. Isolamento estrito por aplicação (sem VPN global no SO).
//! 2. Identidade criptográfica por dispositivo e aperto de mão autenticado.
//! 3. Preferência por conexões diretas (LAN / NAT hole punching) e fallback para blind relay.
//! 4. Gestão de recursos com baixo consumo de memória (RSS < 15 MB) e bateria (baixo overhead e jitter).
//! 5. Proteção anti-replay e allowlist inflexível de rotas do MARK.

pub mod daemon;
pub mod export;
pub mod identity;
pub mod jni;
pub mod peer;
pub mod quic_spike;
pub mod relay;
pub mod telemetry;
pub mod transport;
pub mod updater;

pub use daemon::{ActiveSession, DaemonConfig, DaemonServer};
pub use export::{
    export_config_bytes, import_config_bytes, ExportError, ExportedConfig, ExportedDevice,
    ImportResult,
};
pub use identity::{
    canonical_handshake_init, canonical_handshake_response, compute_transcript_hash,
    derive_resumption_token, verify_p256_der_signature, AccessPolicy, CryptoSuiteError,
    CryptoSuiteValidator, DeviceId, DeviceRecord, DeviceStatus, DeviceStore, HandshakeInitPayload,
    HandshakeResponsePayload, IdentityError, PairingChallenge, PairingError, ReplayFilter,
    RouteCategory, SessionResumeOkPayload, SessionResumePayload, MUTATE_ROUTES, READONLY_ROUTES,
};
pub use peer::{CandidateType, ConnectionCandidate, PeerManager, PeerState};
pub use relay::{BlindRelayRouter, EncryptedEnvelope, RelayClient, RelayError, RelayServer};
pub use telemetry::{DiagnosticReport, LayerStatus, NetworkLayersReport, TelemetryCollector};
pub use transport::{
    CachedResponse, ConnectionMode, DirectionalChannel, IdempotencyCache, PacketHeader, PacketType,
    SecureChannel, TransportError,
};
pub use updater::{canonical_update_manifest, UpdateError, UpdateManifest, UpdateVerifier};

/// Versão semântica do MARK Link Core.
pub const VERSION: &str = "0.1.0";

/// Ping simples para verificação de liveness do núcleo Rust.
pub fn mark_link_ping() -> &'static str {
    "MARK Link Rust Core Online"
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_core_liveness() {
        assert_eq!(mark_link_ping(), "MARK Link Rust Core Online");
    }
}

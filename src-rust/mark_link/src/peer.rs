//! Módulo de Gestão de Pares, Descoberta e Máquina de Estados do MARK Link.
//!
//! Gerencia:
//! - Estados de conexão do par remoto (MARK Mobile <-> PC).
//! - Keepalive adaptativo e modo de baixo consumo em repouso (preservação de bateria).
//! - Reconexão resiliente com backoff exponencial e jitter (elimina busy-loops).
//! - Negociação de candidatos ICE-like priorizando DirectLocal > DirectRemote > Relayed.

use rand::Rng;
use serde::{Deserialize, Serialize};
use std::time::{Duration, Instant};

/// Estados possíveis do ciclo de vida da conexão do par.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum PeerState {
    Disconnected,
    Connecting,
    Loopback,
    DirectLocal,
    DirectRemote,
    Relayed,
    Suspended,
    Reconnecting,
}

impl PeerState {
    pub fn is_connected(&self) -> bool {
        matches!(
            self,
            PeerState::Loopback
                | PeerState::DirectLocal
                | PeerState::DirectRemote
                | PeerState::Relayed
        )
    }

    pub fn is_direct(&self) -> bool {
        matches!(
            self,
            PeerState::Loopback | PeerState::DirectLocal | PeerState::DirectRemote
        )
    }

    pub fn as_str(&self) -> &'static str {
        match self {
            PeerState::Disconnected => "Disconnected",
            PeerState::Connecting => "Connecting",
            PeerState::Loopback => "Loopback",
            PeerState::DirectLocal => "DirectLocal",
            PeerState::DirectRemote => "DirectRemote",
            PeerState::Relayed => "Relayed",
            PeerState::Suspended => "Suspended",
            PeerState::Reconnecting => "Reconnecting",
        }
    }
}

/// Tipos de candidatos de conexão descobertos para o nó.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
pub enum CandidateType {
    RelayServer = 10,
    PublicReflexive = 50,
    LocalLan = 100,
    Loopback = 200,
}

/// Candidato de rede para estabelecimento de transporte.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ConnectionCandidate {
    pub candidate_type: CandidateType,
    pub address: String, // "ip:porta"
    pub priority: u32,
}

impl ConnectionCandidate {
    pub fn loopback(address: String) -> Self {
        Self {
            candidate_type: CandidateType::Loopback,
            address,
            priority: CandidateType::Loopback as u32,
        }
    }

    pub fn local_lan(address: String) -> Self {
        Self {
            candidate_type: CandidateType::LocalLan,
            address,
            priority: CandidateType::LocalLan as u32,
        }
    }

    pub fn public_reflexive(address: String) -> Self {
        Self {
            candidate_type: CandidateType::PublicReflexive,
            address,
            priority: CandidateType::PublicReflexive as u32,
        }
    }

    pub fn relay(address: String) -> Self {
        Self {
            candidate_type: CandidateType::RelayServer,
            address,
            priority: CandidateType::RelayServer as u32,
        }
    }
}

/// Configurador e gestor do ciclo de vida do par remoto.
pub struct PeerManager {
    pub peer_id: String,
    pub state: PeerState,
    pub candidates: Vec<ConnectionCandidate>,
    pub last_activity: Instant,
    pub last_ping_sent: Option<Instant>,
    pub rtt_ms: Option<u32>,
    pub reconnect_attempts: u32,
    pub idle_threshold: Duration,
    pub active_ping_interval: Duration,
    pub idle_ping_interval: Duration,
}

impl PeerManager {
    pub fn new(peer_id: String) -> Self {
        Self {
            peer_id,
            state: PeerState::Disconnected,
            candidates: Vec::new(),
            last_activity: Instant::now(),
            last_ping_sent: None,
            rtt_ms: None,
            reconnect_attempts: 0,
            idle_threshold: Duration::from_secs(30),
            active_ping_interval: Duration::from_secs(5),
            idle_ping_interval: Duration::from_secs(30),
        }
    }

    /// Adiciona candidato e reordena por prioridade decrescente (LocalLan primeiro).
    pub fn add_candidate(&mut self, candidate: ConnectionCandidate) {
        if !self
            .candidates
            .iter()
            .any(|c| c.address == candidate.address)
        {
            self.candidates.push(candidate);
            self.candidates.sort_by(|a, b| b.priority.cmp(&a.priority));
        }
    }

    /// Atualiza estado para conectado no modo correspondente.
    pub fn set_connected(&mut self, candidate_type: CandidateType) {
        self.state = match candidate_type {
            CandidateType::Loopback => PeerState::Loopback,
            CandidateType::LocalLan => PeerState::DirectLocal,
            CandidateType::PublicReflexive => PeerState::DirectRemote,
            CandidateType::RelayServer => PeerState::Relayed,
        };
        self.reconnect_attempts = 0;
        self.last_activity = Instant::now();
    }

    /// Tenta conexão com um candidato de rede validando o handshake obrigatório.
    pub fn connect_candidate_with_handshake(
        &mut self,
        candidate: &ConnectionCandidate,
        handshake_success: bool,
    ) -> Result<(), &'static str> {
        if handshake_success {
            self.set_connected(candidate.candidate_type);
            Ok(())
        } else {
            // O peer só é marcado como conectado após o handshake real comprovado!
            self.state = PeerState::Connecting;
            Err("Handshake falhou")
        }
    }

    /// Executa a tentativa de conexão em camadas na ordem hierárquica estrita:
    /// Loopback -> LAN -> Direct (NAT Traversal) -> Relay Autenticado.
    /// O peer só é marcado como conectado após o handshake real com sucesso.
    pub fn connect_best_candidate<F>(
        &mut self,
        mut try_handshake: F,
    ) -> Result<CandidateType, &'static str>
    where
        F: FnMut(&ConnectionCandidate) -> bool,
    {
        self.state = PeerState::Connecting;
        let candidates = self.candidates.clone();
        for candidate in candidates {
            if try_handshake(&candidate) {
                self.set_connected(candidate.candidate_type);
                return Ok(candidate.candidate_type);
            }
        }
        self.state = PeerState::Disconnected;
        Err("Todos os candidatos falharam no handshake ou na conectividade")
    }

    /// Notifica perda de conexão e calcula próximo intervalo de reconexão com jitter.
    pub fn trigger_reconnect(&mut self) -> Duration {
        self.state = PeerState::Reconnecting;
        self.reconnect_attempts = self.reconnect_attempts.saturating_add(1);
        Self::calculate_backoff(self.reconnect_attempts, 1000, 30_000, 0.20)
    }

    /// Algoritmo de Backoff Exponencial com Jitter aleatório.
    /// Evita sincronização de tempestades de reconexão e reduz consumo de CPU/rádio.
    pub fn calculate_backoff(
        attempt: u32,
        base_ms: u64,
        max_ms: u64,
        jitter_ratio: f64,
    ) -> Duration {
        let exp = 2u64.saturating_pow(attempt.min(6));
        let nominal_ms = (base_ms.saturating_mul(exp)).min(max_ms);

        let mut rng = rand::thread_rng();
        let jitter_factor = 1.0 + rng.gen_range(-jitter_ratio..=jitter_ratio);
        let actual_ms = ((nominal_ms as f64) * jitter_factor).max(100.0) as u64;
        Duration::from_millis(actual_ms)
    }

    /// Determina o intervalo ideal de keepalive dependendo de atividade recente (baixo consumo).
    pub fn current_ping_interval(&self) -> Duration {
        if self.last_activity.elapsed() >= self.idle_threshold {
            self.idle_ping_interval // Reduz wake-ups para economizar bateria
        } else {
            self.active_ping_interval
        }
    }

    /// Registra envio de ping.
    pub fn mark_ping_sent(&mut self) {
        self.last_ping_sent = Some(Instant::now());
    }

    /// Processa pong e calcula RTT suavizado.
    pub fn handle_pong(&mut self) -> Option<u32> {
        if let Some(sent) = self.last_ping_sent.take() {
            let rtt = sent.elapsed().as_millis() as u32;
            self.rtt_ms = Some(match self.rtt_ms {
                Some(prev) => (prev * 3 + rtt) / 4, // Média móvel exponencial
                None => rtt,
            });
            self.last_activity = Instant::now();
            self.rtt_ms
        } else {
            None
        }
    }

    /// Registra tráfego de dados para manter keepalive ativo (modo de alta performance).
    pub fn record_activity(&mut self) {
        self.last_activity = Instant::now();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_candidate_ordering_priority() {
        let mut mgr = PeerManager::new("device-m23".to_string());
        mgr.add_candidate(ConnectionCandidate::relay(
            "relay.mark.local:443".to_string(),
        ));
        mgr.add_candidate(ConnectionCandidate::public_reflexive(
            "203.0.113.198:50407".to_string(),
        ));
        mgr.add_candidate(ConnectionCandidate::local_lan(
            "192.168.15.21:8766".to_string(),
        ));

        assert_eq!(mgr.candidates.len(), 3);
        // Primeiro da lista deve ser LocalLan (maior prioridade)
        assert_eq!(mgr.candidates[0].candidate_type, CandidateType::LocalLan);
        assert_eq!(
            mgr.candidates[1].candidate_type,
            CandidateType::PublicReflexive
        );
        assert_eq!(mgr.candidates[2].candidate_type, CandidateType::RelayServer);
    }

    #[test]
    fn test_backoff_calculation_with_jitter() {
        let b1 = PeerManager::calculate_backoff(1, 1000, 30_000, 0.20);
        let b2 = PeerManager::calculate_backoff(2, 1000, 30_000, 0.20);
        let b3 = PeerManager::calculate_backoff(3, 1000, 30_000, 0.20);

        assert!(b1.as_millis() >= 1500 && b1.as_millis() <= 2500);
        assert!(b2.as_millis() >= 3000 && b2.as_millis() <= 5000);
        assert!(b3.as_millis() >= 6000 && b3.as_millis() <= 10000);
    }

    #[test]
    fn test_rtt_moving_average() {
        let mut mgr = PeerManager::new("device-test".to_string());
        mgr.mark_ping_sent();
        std::thread::sleep(Duration::from_millis(10));
        let rtt = mgr.handle_pong().expect("RTT esperado");
        assert!(rtt >= 10);
        assert_eq!(mgr.rtt_ms, Some(rtt));
    }

    #[test]
    fn test_layered_connectivity_hierarchy_loopback_lan_direct_relay() {
        let mut mgr = PeerManager::new("device-peer-layers".to_string());
        mgr.add_candidate(ConnectionCandidate::relay(
            "relay.mark.local:8767".to_string(),
        ));
        mgr.add_candidate(ConnectionCandidate::public_reflexive(
            "203.0.113.198:50407".to_string(),
        ));
        mgr.add_candidate(ConnectionCandidate::local_lan(
            "192.168.15.21:8766".to_string(),
        ));
        mgr.add_candidate(ConnectionCandidate::loopback("127.0.0.1:8766".to_string()));

        assert_eq!(mgr.candidates.len(), 4);
        assert_eq!(mgr.candidates[0].candidate_type, CandidateType::Loopback);
        assert_eq!(mgr.candidates[1].candidate_type, CandidateType::LocalLan);
        assert_eq!(
            mgr.candidates[2].candidate_type,
            CandidateType::PublicReflexive
        );
        assert_eq!(mgr.candidates[3].candidate_type, CandidateType::RelayServer);
    }

    #[test]
    fn test_candidate_connection_aborts_and_does_not_mark_connected_if_handshake_fails() {
        let mut mgr = PeerManager::new("device-peer-1".to_string());
        mgr.add_candidate(ConnectionCandidate::local_lan(
            "192.168.1.50:8766".to_string(),
        ));

        // Tentativa de conexão com candidato LAN viável onde o handshake falha
        let cand = mgr.candidates[0].clone();
        let result = mgr.connect_candidate_with_handshake(&cand, false);

        assert!(result.is_err(), "Handshake falho DEVE retornar erro");
        assert!(
            !mgr.state.is_connected(),
            "FALHA RED: Peer foi marcado como conectado mesmo com falha no handshake! Estado atual: {:?}",
            mgr.state
        );
        assert_ne!(mgr.state, PeerState::DirectLocal);
    }

    #[test]
    fn test_adaptive_keepalive_reduces_frequency_in_idle_and_resets_on_data() {
        let mut mgr = PeerManager::new("device-peer-battery".to_string());
        mgr.idle_threshold = Duration::from_millis(50);
        mgr.active_ping_interval = Duration::from_secs(5);
        mgr.idle_ping_interval = Duration::from_secs(45);

        assert_eq!(mgr.current_ping_interval(), Duration::from_secs(5));
        std::thread::sleep(Duration::from_millis(60));
        assert_eq!(mgr.current_ping_interval(), Duration::from_secs(45));

        mgr.record_activity();
        assert_eq!(
            mgr.current_ping_interval(),
            Duration::from_secs(5),
            "FALHA RED: record_activity não restaurou o intervalo ativo de 5s"
        );
    }
}

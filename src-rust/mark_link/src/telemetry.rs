//! Módulo de Telemetria Local, Diagnóstico e Métricas de Desempenho do MARK Link.
//!
//! Monitora:
//! - Modo de conexão atual (DirectLocal, DirectRemote, Relayed, Disconnected).
//! - Separação explícita de falhas por camadas: Serviço, Loopback, LAN, Direta, Relay e Autenticação.
//! - Latência estimada (RTT em ms).
//! - Tráfego transferido (bytes/pacotes enviados e recebidos).
//! - Consumo real de memória RSS (Resident Set Size).
//! - Sanitização estrita: proibição absoluta de inclusão de chaves, tokens ou texto de conversas.

use crate::transport::ConnectionMode;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::sync::atomic::{AtomicU32, AtomicU64, Ordering};
use std::sync::Mutex;
use std::time::Instant;

/// Diagnóstico de status de uma camada individual de rede.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct LayerStatus {
    pub status: String, // "ok", "degraded", "down", "disconnected", "standby"
    pub error: Option<String>,
    pub details: Option<String>,
}

/// Diagnóstico estruturado consolidado das camadas de rede do MARK Link.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct NetworkLayersReport {
    pub service: LayerStatus,
    pub loopback: LayerStatus,
    pub lan: LayerStatus,
    pub direct: LayerStatus,
    pub relay: LayerStatus,
    pub auth: LayerStatus,
}

/// Diagnóstico estruturado e sanitizado do MARK Link.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DiagnosticReport {
    pub state: String,
    pub mode: Option<String>,
    pub peer_id_masked: String,
    pub rtt_ms: Option<u32>,
    pub bytes_sent: u64,
    pub bytes_received: u64,
    pub packets_dropped: u64,
    pub reconnect_count: u32,
    pub failover_count: u32,
    pub memory_rss_kb: usize,
    pub uptime_seconds: u64,
    pub last_error: Option<String>,
    pub layers: NetworkLayersReport,
}

pub struct TelemetryCollector {
    start_time: Instant,
    bytes_sent: AtomicU64,
    bytes_received: AtomicU64,
    packets_dropped: AtomicU64,
    reconnect_count: AtomicU32,
    failover_count: AtomicU32,
    rtt_ms: AtomicU32, // 0 = desconhecido
    active_mode: Mutex<Option<ConnectionMode>>,
    peer_id: Mutex<Option<String>>,
    last_error: Mutex<Option<String>>,
    layer_statuses: Mutex<HashMap<String, LayerStatus>>,
}

impl Default for TelemetryCollector {
    fn default() -> Self {
        Self::new()
    }
}

impl TelemetryCollector {
    pub fn new() -> Self {
        let mut layers = HashMap::new();
        layers.insert(
            "service".to_string(),
            LayerStatus {
                status: "ok".to_string(),
                error: None,
                details: None,
            },
        );
        layers.insert(
            "loopback".to_string(),
            LayerStatus {
                status: "ok".to_string(),
                error: None,
                details: None,
            },
        );
        layers.insert(
            "lan".to_string(),
            LayerStatus {
                status: "standby".to_string(),
                error: None,
                details: None,
            },
        );
        layers.insert(
            "direct".to_string(),
            LayerStatus {
                status: "disconnected".to_string(),
                error: None,
                details: None,
            },
        );
        layers.insert(
            "relay".to_string(),
            LayerStatus {
                status: "standby".to_string(),
                error: None,
                details: None,
            },
        );
        layers.insert(
            "auth".to_string(),
            LayerStatus {
                status: "ok".to_string(),
                error: None,
                details: None,
            },
        );

        Self {
            start_time: Instant::now(),
            bytes_sent: AtomicU64::new(0),
            bytes_received: AtomicU64::new(0),
            packets_dropped: AtomicU64::new(0),
            reconnect_count: AtomicU32::new(0),
            failover_count: AtomicU32::new(0),
            rtt_ms: AtomicU32::new(0),
            active_mode: Mutex::new(None),
            peer_id: Mutex::new(None),
            last_error: Mutex::new(None),
            layer_statuses: Mutex::new(layers),
        }
    }

    pub fn set_peer(&self, peer_id: &str) {
        let mut p = self.peer_id.lock().unwrap();
        *p = Some(peer_id.to_string());
    }

    pub fn set_mode(&self, mode: Option<ConnectionMode>) {
        let mut m = self.active_mode.lock().unwrap();
        *m = mode;

        let mut layers = self.layer_statuses.lock().unwrap();
        if let Some(cm) = mode {
            match cm {
                ConnectionMode::DirectLoopback => {
                    layers.insert(
                        "loopback".to_string(),
                        LayerStatus {
                            status: "ok".to_string(),
                            error: None,
                            details: Some("DirectLoopback active".to_string()),
                        },
                    );
                }
                ConnectionMode::DirectLocal | ConnectionMode::DirectRemote => {
                    layers.insert(
                        "direct".to_string(),
                        LayerStatus {
                            status: "ok".to_string(),
                            error: None,
                            details: Some(cm.as_str().to_string()),
                        },
                    );
                }
                ConnectionMode::Relayed => {
                    layers.insert(
                        "relay".to_string(),
                        LayerStatus {
                            status: "ok".to_string(),
                            error: None,
                            details: Some("Relay session active".to_string()),
                        },
                    );
                }
            }
        } else {
            layers.insert(
                "direct".to_string(),
                LayerStatus {
                    status: "disconnected".to_string(),
                    error: None,
                    details: None,
                },
            );
        }
    }

    pub fn record_bytes_sent(&self, bytes: u64) {
        self.bytes_sent.fetch_add(bytes, Ordering::Relaxed);
    }

    pub fn record_bytes_received(&self, bytes: u64) {
        self.bytes_received.fetch_add(bytes, Ordering::Relaxed);
    }

    pub fn record_drop(&self) {
        self.packets_dropped.fetch_add(1, Ordering::Relaxed);
    }

    pub fn record_reconnect(&self) {
        self.reconnect_count.fetch_add(1, Ordering::Relaxed);
    }

    pub fn record_failover(&self) {
        self.failover_count.fetch_add(1, Ordering::Relaxed);
    }

    pub fn set_rtt(&self, rtt: u32) {
        self.rtt_ms.store(rtt, Ordering::Relaxed);
    }

    pub fn record_error(&self, err: &str) {
        let sanitized = Self::sanitize_error_message(err);
        let mut e = self.last_error.lock().unwrap();
        *e = Some(sanitized);
    }

    pub fn set_layer_status(
        &self,
        layer: &str,
        status: &str,
        error: Option<&str>,
        details: Option<&str>,
    ) {
        let sanitized_err = error.map(Self::sanitize_error_message);
        let sanitized_det = details.map(Self::sanitize_error_message);
        let mut layers = self.layer_statuses.lock().unwrap();
        layers.insert(
            layer.to_string(),
            LayerStatus {
                status: status.to_string(),
                error: sanitized_err,
                details: sanitized_det,
            },
        );
    }

    pub fn record_service_failure(&self, err: &str) {
        self.set_layer_status("service", "down", Some(err), None);
    }

    pub fn record_lan_failure(&self, err: &str) {
        self.set_layer_status("lan", "degraded", Some(err), None);
    }

    pub fn record_direct_failure(&self, err: &str) {
        self.set_layer_status("direct", "down", Some(err), None);
    }

    pub fn record_relay_failure(&self, err: &str) {
        self.set_layer_status("relay", "degraded", Some(err), None);
    }

    pub fn record_auth_failure(&self, err: &str) {
        self.set_layer_status("auth", "degraded", Some(err), None);
    }

    /// Limpa segredos embutidos em mensagens de erro garantindo zero vazamento.
    pub fn sanitize_error_message(msg: &str) -> String {
        let mut words: Vec<String> = Vec::new();
        let mut redact_next = false;
        for word in msg.split_whitespace() {
            let lower = word.to_lowercase();
            if redact_next {
                if let Some((prefix, _)) = word.split_once('=') {
                    words.push(format!("{}=[REDACTED]", prefix));
                } else {
                    words.push("[REDACTED]".to_string());
                }
                redact_next = false;
                continue;
            }
            if lower == "bearer" {
                words.push(word.to_string());
                redact_next = true;
                continue;
            }
            if lower.contains("key")
                || lower.contains("token")
                || lower.contains("secret")
                || lower.contains("pass")
                || (word.len() >= 32
                    && word
                        .chars()
                        .all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_' || c == '.'))
            {
                if let Some((prefix, _)) = word.split_once('=') {
                    words.push(format!("{}=[REDACTED]", prefix));
                } else {
                    words.push("[REDACTED]".to_string());
                }
            } else {
                words.push(word.to_string());
            }
        }
        words.join(" ")
    }

    /// Coleta RSS real em Linux via /proc/self/statm.
    pub fn get_memory_rss_kb() -> usize {
        #[cfg(target_os = "linux")]
        {
            if let Ok(content) = std::fs::read_to_string("/proc/self/statm") {
                let parts: Vec<&str> = content.split_whitespace().collect();
                if parts.len() >= 2 {
                    if let Ok(resident_pages) = parts[1].parse::<usize>() {
                        let page_size_kb = 4; // páginas padrão de 4 KB
                        return resident_pages * page_size_kb;
                    }
                }
            }
        }
        0
    }

    /// Produz relatório de diagnóstico sanitizado segregando as camadas de rede.
    pub fn generate_report(&self) -> DiagnosticReport {
        let peer_guard = self.peer_id.lock().unwrap();
        let masked_peer = peer_guard.as_ref().map_or("none".to_string(), |id| {
            if id.len() > 10 {
                format!("{}…{}", &id[..6], &id[id.len() - 4..])
            } else {
                id.clone()
            }
        });

        let mode_guard = self.active_mode.lock().unwrap();
        let mode_str = mode_guard.map(|m| m.as_str().to_string());

        let rtt = self.rtt_ms.load(Ordering::Relaxed);
        let err_guard = self.last_error.lock().unwrap();

        let layers_guard = self.layer_statuses.lock().unwrap();
        let default_status = |st: &str| LayerStatus {
            status: st.to_string(),
            error: None,
            details: None,
        };

        let layers = NetworkLayersReport {
            service: layers_guard
                .get("service")
                .cloned()
                .unwrap_or_else(|| default_status("ok")),
            loopback: layers_guard
                .get("loopback")
                .cloned()
                .unwrap_or_else(|| default_status("ok")),
            lan: layers_guard
                .get("lan")
                .cloned()
                .unwrap_or_else(|| default_status("standby")),
            direct: layers_guard
                .get("direct")
                .cloned()
                .unwrap_or_else(|| default_status("disconnected")),
            relay: layers_guard
                .get("relay")
                .cloned()
                .unwrap_or_else(|| default_status("standby")),
            auth: layers_guard
                .get("auth")
                .cloned()
                .unwrap_or_else(|| default_status("ok")),
        };

        DiagnosticReport {
            state: if mode_str.is_some() {
                "connected".to_string()
            } else {
                "disconnected".to_string()
            },
            mode: mode_str,
            peer_id_masked: masked_peer,
            rtt_ms: if rtt > 0 { Some(rtt) } else { None },
            bytes_sent: self.bytes_sent.load(Ordering::Relaxed),
            bytes_received: self.bytes_received.load(Ordering::Relaxed),
            packets_dropped: self.packets_dropped.load(Ordering::Relaxed),
            reconnect_count: self.reconnect_count.load(Ordering::Relaxed),
            failover_count: self.failover_count.load(Ordering::Relaxed),
            memory_rss_kb: Self::get_memory_rss_kb(),
            uptime_seconds: self.start_time.elapsed().as_secs(),
            last_error: err_guard.clone(),
            layers,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_telemetry_metrics_and_sanitization() {
        let telem = TelemetryCollector::new();
        telem.set_peer("my-super-secret-device-id-1234567890");
        telem.set_mode(Some(ConnectionMode::DirectLocal));
        telem.record_bytes_sent(1500);
        telem.record_bytes_received(3000);
        telem.set_rtt(18);
        telem.record_error("Falha temporária de handshake no socket");

        let report = telem.generate_report();
        assert_eq!(report.state, "connected");
        assert_eq!(report.mode.as_deref(), Some("DirectLocal"));
        assert_eq!(report.rtt_ms, Some(18));
        assert_eq!(report.bytes_sent, 1500);
        assert_eq!(report.bytes_received, 3000);
        assert!(report.peer_id_masked.contains("…")); // ID mascarado
        assert!(report.last_error.is_some());
        assert_eq!(report.layers.service.status, "ok");
        assert_eq!(report.layers.direct.status, "ok");
    }

    #[test]
    fn test_telemetry_layer_separation_and_failure_segregation() {
        let telem = TelemetryCollector::new();
        telem.record_service_failure("Falha de porta TCP 8766 com chave secret_key=12345");
        telem.record_lan_failure("LAN broadcast timeout");
        telem.record_relay_failure("Relay recusou com token=bearer_fake_99");

        let report = telem.generate_report();
        assert_eq!(report.layers.service.status, "down");
        assert_eq!(report.layers.lan.status, "degraded");
        assert_eq!(report.layers.relay.status, "degraded");
        assert_eq!(report.layers.auth.status, "ok");

        let serialized = serde_json::to_string(&report).unwrap();
        assert!(!serialized.contains("secret_key=12345"));
        assert!(!serialized.contains("bearer_fake_99"));
        assert!(serialized.contains("[REDACTED]"));
    }

    #[test]
    fn test_memory_rss_reading() {
        let rss = TelemetryCollector::get_memory_rss_kb();
        // Em Linux, deve retornar valor > 0
        assert!(rss > 0);
    }

    #[test]
    fn test_telemetry_zero_leakage_of_cryptographic_keys_and_payloads() {
        let telem = TelemetryCollector::new();
        telem.set_peer("my-super-secret-device-id-1234567890");
        telem.set_mode(Some(ConnectionMode::DirectLocal));
        telem.record_error("Crash with private_key_hex=4a5b6c7d8e9f0123456789abcdef0123456789abcdef0123456789abcdef and bearer token=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9");
        telem.record_bytes_sent(1024);
        telem.record_drop();

        let report = telem.generate_report();
        let serialized = serde_json::to_string(&report).unwrap();

        assert!(
            !serialized.contains("4a5b6c7d8e9f0123456789abcdef0123456789abcdef0123456789abcdef")
        );
        assert!(!serialized.contains("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"));
        assert!(serialized.contains("[REDACTED]"));
    }
}

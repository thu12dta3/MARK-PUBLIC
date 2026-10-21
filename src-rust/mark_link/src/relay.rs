//! Módulo de Relay Cego (Blind Relay) do MARK Link.
//!
//! Princípios de Segurança:
//! - O relay é estritamente cego (End-to-End Blind Forwarding): não possui as chaves de sessão dos pares.
//! - Não pode inspecionar, ler ou modificar payloads (qualquer adulteração invalida a tag MAC no destino).
//! - Roteia envelopes cifrados baseando-se exclusivamente no identificador público do dispositivo de destino.
//! - Fornece contingência para ambientes sob CGNAT simétrico, firewalls restritivos ou redes celulares fechadas.

use crate::identity::{verify_p256_der_signature, DeviceId};
use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine as _};
use p256::ecdsa::signature::Signer;
use p256::ecdsa::SigningKey;
use p256::pkcs8::EncodePublicKey;
use rand::rngs::OsRng;
use std::collections::HashMap;
use std::io::{BufRead, BufReader, Read, Write};
use std::net::{TcpListener, TcpStream};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

pub const RELAY_MAGIC: &[u8; 4] = b"MRLY";
pub const MAX_ENVELOPE_PAYLOAD: usize = 64 * 1024; // 64 KB por envelope

#[derive(Debug, PartialEq, Eq)]
pub enum RelayError {
    InvalidMagic,
    InvalidDeviceId,
    PayloadTooLarge,
    BufferTooShort,
    PeerNotFound,
    PeerDisconnected,
    RateLimitExceeded,
    QueueFull,
    AuthenticationRequired,
    AuthenticationFailed,
    IoError(String),
}

impl std::fmt::Display for RelayError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            RelayError::InvalidMagic => write!(f, "Magic de relay inválido (esperado MRLY)"),
            RelayError::InvalidDeviceId => {
                write!(f, "Identificador de dispositivo no envelope inválido")
            }
            RelayError::PayloadTooLarge => write!(f, "Payload do envelope excede limite máximo"),
            RelayError::BufferTooShort => write!(f, "Buffer de envelope truncado"),
            RelayError::PeerNotFound => write!(f, "Dispositivo de destino não encontrado no relay"),
            RelayError::PeerDisconnected => {
                write!(f, "Dispositivo de destino desconectou do relay")
            }
            RelayError::RateLimitExceeded => {
                write!(f, "Limite de taxa (rate limit / burst) excedido no relay")
            }
            RelayError::QueueFull => write!(f, "Fila de entrega do destino cheia no relay"),
            RelayError::AuthenticationRequired => {
                write!(f, "Autenticação prévia obrigatória no relay")
            }
            RelayError::AuthenticationFailed => {
                write!(f, "Falha na validação de credencial/assinatura no relay")
            }
            RelayError::IoError(msg) => write!(f, "Erro de I/O no relay: {}", msg),
        }
    }
}

impl std::error::Error for RelayError {}

/// Envelope cifrado transmitido através do relay cego.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct EncryptedEnvelope {
    pub target: DeviceId,
    pub source: DeviceId,
    pub opaque_payload: Vec<u8>,
}

impl EncryptedEnvelope {
    pub fn new(
        target: DeviceId,
        source: DeviceId,
        opaque_payload: Vec<u8>,
    ) -> Result<Self, RelayError> {
        if opaque_payload.len() > MAX_ENVELOPE_PAYLOAD {
            return Err(RelayError::PayloadTooLarge);
        }
        if !target.is_valid() || !source.is_valid() {
            return Err(RelayError::InvalidDeviceId);
        }
        Ok(Self {
            target,
            source,
            opaque_payload,
        })
    }

    /// Serializa o envelope binário para envio ao relay.
    pub fn serialize(&self) -> Vec<u8> {
        let target_bytes = self.target.as_str().as_bytes();
        let source_bytes = self.source.as_str().as_bytes();
        let payload_len = self.opaque_payload.len() as u32;

        let total_size =
            4 + 1 + target_bytes.len() + 1 + source_bytes.len() + 4 + self.opaque_payload.len();
        let mut buf = Vec::with_capacity(total_size);

        buf.extend_from_slice(RELAY_MAGIC);
        buf.push(target_bytes.len() as u8);
        buf.extend_from_slice(target_bytes);
        buf.push(source_bytes.len() as u8);
        buf.extend_from_slice(source_bytes);
        buf.extend_from_slice(&payload_len.to_be_bytes());
        buf.extend_from_slice(&self.opaque_payload);

        buf
    }

    /// Desserializa um envelope binário recebido do relay.
    pub fn deserialize(buf: &[u8]) -> Result<Self, RelayError> {
        if buf.len() < 4 + 1 + 1 + 4 {
            return Err(RelayError::BufferTooShort);
        }
        if &buf[0..4] != RELAY_MAGIC {
            return Err(RelayError::InvalidMagic);
        }

        let mut offset = 4;
        let target_len = buf[offset] as usize;
        offset += 1;
        if offset + target_len > buf.len() {
            return Err(RelayError::BufferTooShort);
        }
        let target_str = std::str::from_utf8(&buf[offset..offset + target_len])
            .map_err(|_| RelayError::InvalidDeviceId)?;
        let target = DeviceId(target_str.to_string());
        offset += target_len;

        if offset >= buf.len() {
            return Err(RelayError::BufferTooShort);
        }
        let source_len = buf[offset] as usize;
        offset += 1;
        if offset + source_len + 4 > buf.len() {
            return Err(RelayError::BufferTooShort);
        }
        let source_str = std::str::from_utf8(&buf[offset..offset + source_len])
            .map_err(|_| RelayError::InvalidDeviceId)?;
        let source = DeviceId(source_str.to_string());
        offset += source_len;

        let payload_len = u32::from_be_bytes(buf[offset..offset + 4].try_into().unwrap()) as usize;
        offset += 4;

        if payload_len > MAX_ENVELOPE_PAYLOAD {
            return Err(RelayError::PayloadTooLarge);
        }
        if offset + payload_len > buf.len() {
            return Err(RelayError::BufferTooShort);
        }
        let opaque_payload = buf[offset..offset + payload_len].to_vec();

        Self::new(target, source, opaque_payload)
    }

    pub fn write_to<W: Write>(&self, writer: &mut W) -> Result<(), RelayError> {
        let serialized = self.serialize();
        writer
            .write_all(&serialized)
            .map_err(|e| RelayError::IoError(e.to_string()))?;
        writer
            .flush()
            .map_err(|e| RelayError::IoError(e.to_string()))?;
        Ok(())
    }

    pub fn read_from<R: Read>(reader: &mut R) -> Result<Self, RelayError> {
        let mut magic = [0u8; 4];
        reader
            .read_exact(&mut magic)
            .map_err(|e| RelayError::IoError(e.to_string()))?;
        if &magic != RELAY_MAGIC {
            return Err(RelayError::InvalidMagic);
        }

        let mut target_len_buf = [0u8; 1];
        reader
            .read_exact(&mut target_len_buf)
            .map_err(|e| RelayError::IoError(e.to_string()))?;
        let target_len = target_len_buf[0] as usize;
        let mut target_buf = vec![0u8; target_len];
        reader
            .read_exact(&mut target_buf)
            .map_err(|e| RelayError::IoError(e.to_string()))?;
        let target_str =
            std::str::from_utf8(&target_buf).map_err(|_| RelayError::InvalidDeviceId)?;
        let target = DeviceId(target_str.to_string());

        let mut source_len_buf = [0u8; 1];
        reader
            .read_exact(&mut source_len_buf)
            .map_err(|e| RelayError::IoError(e.to_string()))?;
        let source_len = source_len_buf[0] as usize;
        let mut source_buf = vec![0u8; source_len];
        reader
            .read_exact(&mut source_buf)
            .map_err(|e| RelayError::IoError(e.to_string()))?;
        let source_str =
            std::str::from_utf8(&source_buf).map_err(|_| RelayError::InvalidDeviceId)?;
        let source = DeviceId(source_str.to_string());

        let mut payload_len_buf = [0u8; 4];
        reader
            .read_exact(&mut payload_len_buf)
            .map_err(|e| RelayError::IoError(e.to_string()))?;
        let payload_len = u32::from_be_bytes(payload_len_buf) as usize;
        if payload_len > MAX_ENVELOPE_PAYLOAD {
            return Err(RelayError::PayloadTooLarge);
        }
        let mut payload = vec![0u8; payload_len];
        reader
            .read_exact(&mut payload)
            .map_err(|e| RelayError::IoError(e.to_string()))?;

        Self::new(target, source, payload)
    }
}

/// Limitador de taxa (Token Bucket) por peer para controle anti-abuso e DoS.
#[derive(Clone)]
pub struct PeerRateLimiter {
    tokens: f64,
    last_update: Instant,
    burst_capacity: f64,
    refill_rate_per_sec: f64,
}

impl PeerRateLimiter {
    pub fn new(burst: f64, rate: f64) -> Self {
        Self {
            tokens: burst,
            last_update: Instant::now(),
            burst_capacity: burst,
            refill_rate_per_sec: rate,
        }
    }

    pub fn try_consume(&mut self) -> bool {
        let now = Instant::now();
        let elapsed = now.duration_since(self.last_update).as_secs_f64();
        self.tokens = (self.tokens + elapsed * self.refill_rate_per_sec).min(self.burst_capacity);
        self.last_update = now;

        if self.tokens >= 1.0 {
            self.tokens -= 1.0;
            true
        } else {
            false
        }
    }
}

/// Roteador em memória para switch de envelopes no Relay.
#[derive(Clone)]
pub struct BlindRelayRouter {
    // Mapa de sessões ativas: DeviceId -> Canal de envio de envelopes
    routes: Arc<Mutex<HashMap<DeviceId, Vec<EncryptedEnvelope>>>>,
    limiters: Arc<Mutex<HashMap<DeviceId, PeerRateLimiter>>>,
    pub max_queue_per_peer: usize,
    pub burst_limit: usize,
}

impl Default for BlindRelayRouter {
    fn default() -> Self {
        Self::new()
    }
}

impl BlindRelayRouter {
    pub fn new() -> Self {
        Self {
            routes: Arc::new(Mutex::new(HashMap::new())),
            limiters: Arc::new(Mutex::new(HashMap::new())),
            max_queue_per_peer: 64,
            burst_limit: 64,
        }
    }

    /// Registra presença de um peer no relay.
    pub fn register_peer(&self, peer_id: DeviceId) {
        let mut map = self.routes.lock().unwrap();
        map.entry(peer_id).or_default();
    }

    /// Remove presença de um peer no relay ao desconectar.
    pub fn unregister_peer(&self, peer_id: &DeviceId) {
        let mut map = self.routes.lock().unwrap();
        map.remove(peer_id);
    }

    /// Roteia envelope para a fila do destinatário de forma cega com rate-limiting e bounded queue.
    pub fn forward(&self, envelope: EncryptedEnvelope) -> Result<(), RelayError> {
        // 1. Limitação de taxa (Rate Limiter / Anti-Burst DoS) por dispositivo de origem
        {
            let mut limiters = self.limiters.lock().unwrap();
            let limiter = limiters
                .entry(envelope.source.clone())
                .or_insert_with(|| PeerRateLimiter::new(self.burst_limit as f64, 50.0));
            if !limiter.try_consume() {
                return Err(RelayError::RateLimitExceeded);
            }
        }

        // 2. Proteção contra esgotamento de filas (Bounded queue per target)
        let mut map = self.routes.lock().unwrap();
        if let Some(queue) = map.get_mut(&envelope.target) {
            if queue.len() >= self.max_queue_per_peer {
                return Err(RelayError::QueueFull);
            }
            queue.push(envelope);
            Ok(())
        } else {
            Err(RelayError::PeerNotFound)
        }
    }

    /// Coleta envelopes pendentes para um dispositivo.
    pub fn drain_for(&self, peer_id: &DeviceId) -> Vec<EncryptedEnvelope> {
        let mut map = self.routes.lock().unwrap();
        if let Some(queue) = map.get_mut(peer_id) {
            std::mem::take(queue)
        } else {
            Vec::new()
        }
    }
}

/// Servidor de Relay em rede TCP (Blind Relay Server).
pub struct RelayServer {
    pub bind_addr: String,
    pub local_addr: String,
    running: Arc<AtomicBool>,
    pub router: BlindRelayRouter,
    active_peers: Arc<Mutex<HashMap<DeviceId, std::sync::mpsc::SyncSender<Vec<u8>>>>>,
    listener: Option<TcpListener>,
}

impl RelayServer {
    pub fn bind(bind_addr: &str) -> Result<Self, RelayError> {
        let listener =
            TcpListener::bind(bind_addr).map_err(|e| RelayError::IoError(e.to_string()))?;
        let local_addr = listener
            .local_addr()
            .map_err(|e| RelayError::IoError(e.to_string()))?
            .to_string();
        listener
            .set_nonblocking(true)
            .map_err(|e| RelayError::IoError(e.to_string()))?;

        Ok(Self {
            bind_addr: bind_addr.to_string(),
            local_addr,
            running: Arc::new(AtomicBool::new(false)),
            router: BlindRelayRouter::new(),
            active_peers: Arc::new(Mutex::new(HashMap::new())),
            listener: Some(listener),
        })
    }

    pub fn local_addr(&self) -> &str {
        &self.local_addr
    }

    pub fn start(&mut self) -> Result<(), RelayError> {
        let listener = self.listener.take().ok_or_else(|| {
            RelayError::IoError("RelayServer já iniciado ou listener indisponível".to_string())
        })?;
        self.running.store(true, Ordering::SeqCst);

        let running = Arc::clone(&self.running);
        let active_peers = Arc::clone(&self.active_peers);
        let router = self.router.clone();

        thread::spawn(move || {
            while running.load(Ordering::SeqCst) {
                match listener.accept() {
                    Ok((stream, _addr)) => {
                        let active = Arc::clone(&active_peers);
                        let r = router.clone();
                        let running_client = Arc::clone(&running);
                        thread::spawn(move || {
                            Self::handle_client(stream, active, r, running_client);
                        });
                    }
                    Err(ref e) if e.kind() == std::io::ErrorKind::WouldBlock => {
                        thread::sleep(Duration::from_millis(10));
                    }
                    Err(_) => break,
                }
            }
        });
        Ok(())
    }

    pub fn stop(&self) {
        self.running.store(false, Ordering::SeqCst);
    }

    fn handle_client(
        mut stream: TcpStream,
        active_peers: Arc<Mutex<HashMap<DeviceId, std::sync::mpsc::SyncSender<Vec<u8>>>>>,
        router: BlindRelayRouter,
        server_running: Arc<AtomicBool>,
    ) {
        let _ = stream.set_read_timeout(Some(Duration::from_secs(30)));
        let _ = stream.set_write_timeout(Some(Duration::from_secs(30)));

        let mut reader = BufReader::new(match stream.try_clone() {
            Ok(s) => s,
            Err(_) => return,
        });

        // 1. Mensagem de registro e autenticação prévia
        let mut reg_line = String::new();
        if reader.read_line(&mut reg_line).is_err() {
            return;
        }
        let trimmed = reg_line.trim();
        let parts: Vec<&str> = trimmed.split_whitespace().collect();
        if parts.is_empty() {
            return;
        }

        // Se o cliente tentar registrar sem autenticação:
        if parts[0] == "REGISTER" {
            let _ = stream.write_all(
                b"AUTH_REQUIRED: Autenticacao criptografica previa obrigatoria no Relay\n",
            );
            let _ = stream.flush();
            return;
        }

        // Formato esperado de autenticação:
        // "AUTH_REGISTER <device_id> <timestamp> <nonce_b64> <pub_der_b64> <sig_b64>"
        if parts[0] != "AUTH_REGISTER" || parts.len() != 6 {
            let _ = stream.write_all(b"AUTH_FAILED: Formato de registro invalido\n");
            let _ = stream.flush();
            return;
        }

        let device_id_str = parts[1];
        let device_id = DeviceId(device_id_str.to_string());
        if !device_id.is_valid() {
            let _ = stream.write_all(b"AUTH_FAILED: DeviceId invalido\n");
            let _ = stream.flush();
            return;
        }

        let timestamp: u64 = match parts[2].parse() {
            Ok(t) => t,
            Err(_) => {
                let _ = stream.write_all(b"AUTH_FAILED: Timestamp invalido\n");
                let _ = stream.flush();
                return;
            }
        };

        // Valida clock skew (máx 60 segundos)
        let now_secs = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_secs())
            .unwrap_or(0);
        if now_secs.abs_diff(timestamp) > 60 {
            let _ = stream.write_all(b"AUTH_FAILED: Clock skew excessivo\n");
            let _ = stream.flush();
            return;
        }

        let nonce_b64 = parts[3];
        let pub_der_b64 = parts[4];
        let sig_b64 = parts[5];

        let pub_der = match URL_SAFE_NO_PAD.decode(pub_der_b64) {
            Ok(d) => d,
            Err(_) => {
                let _ = stream.write_all(b"AUTH_FAILED: Chave publica DER corrompida\n");
                let _ = stream.flush();
                return;
            }
        };

        if DeviceId::from_public_der(&pub_der) != device_id {
            let _ = stream.write_all(b"AUTH_FAILED: DeviceId nao coincide com chave publica\n");
            let _ = stream.flush();
            return;
        }

        let sig_bytes = match URL_SAFE_NO_PAD.decode(sig_b64) {
            Ok(s) => s,
            Err(_) => {
                let _ = stream.write_all(b"AUTH_FAILED: Assinatura corrompida\n");
                let _ = stream.flush();
                return;
            }
        };

        let canonical = format!(
            "MARK-RELAY-AUTH-V1\n{}\n{}\n{}",
            device_id.as_str(),
            timestamp,
            nonce_b64
        );
        if !verify_p256_der_signature(&pub_der, canonical.as_bytes(), &sig_bytes) {
            let _ = stream.write_all(b"AUTH_FAILED: Assinatura criptografica invalida\n");
            let _ = stream.flush();
            return;
        }

        // Responde confirmação de autenticação bem-sucedida
        if stream.write_all(b"OK\n").is_err() || stream.flush().is_err() {
            return;
        }

        let (tx, rx) = std::sync::mpsc::sync_channel::<Vec<u8>>(64);
        {
            let mut map = active_peers.lock().unwrap();
            map.insert(device_id.clone(), tx);
            router.register_peer(device_id.clone());
        }

        // Drena envelopes enfileirados enquanto este peer estava desconectado
        let drained = router.drain_for(&device_id);
        for env in drained {
            let serialized = env.serialize();
            if stream.write_all(&serialized).is_err() {
                break;
            }
        }
        let _ = stream.flush();

        // Thread para encaminhar mensagens do canal para o socket do peer
        let mut writer_stream = match stream.try_clone() {
            Ok(s) => s,
            Err(_) => return,
        };
        let is_running = Arc::new(AtomicBool::new(true));
        let is_running_writer = Arc::clone(&is_running);
        let srv_running = Arc::clone(&server_running);
        thread::spawn(move || {
            while is_running_writer.load(Ordering::SeqCst) && srv_running.load(Ordering::SeqCst) {
                match rx.recv_timeout(Duration::from_millis(50)) {
                    Ok(data) => {
                        if writer_stream.write_all(&data).is_err() || writer_stream.flush().is_err()
                        {
                            break;
                        }
                    }
                    Err(std::sync::mpsc::RecvTimeoutError::Timeout) => continue,
                    Err(std::sync::mpsc::RecvTimeoutError::Disconnected) => break,
                }
            }
        });

        // Loop de leitura de envelopes enviados por este peer
        while server_running.load(Ordering::SeqCst) {
            match EncryptedEnvelope::read_from(&mut reader) {
                Ok(envelope) => {
                    let serialized = envelope.serialize();
                    let map = active_peers.lock().unwrap();
                    if let Some(target_tx) = map.get(&envelope.target) {
                        let _ = target_tx.try_send(serialized);
                    } else {
                        let _ = router.forward(envelope);
                    }
                }
                Err(_) => break,
            }
        }

        is_running.store(false, Ordering::SeqCst);
        let mut map = active_peers.lock().unwrap();
        map.remove(&device_id);
        router.unregister_peer(&device_id);
    }
}

/// Cliente para comunicação em rede com o Blind Relay Server.
pub struct RelayClient {
    stream: TcpStream,
    reader: BufReader<TcpStream>,
    pub my_device_id: DeviceId,
}

impl RelayClient {
    pub fn connect_authenticated(
        server_addr: &str,
        signing_key: &SigningKey,
    ) -> Result<Self, RelayError> {
        let mut stream =
            TcpStream::connect(server_addr).map_err(|e| RelayError::IoError(e.to_string()))?;
        let pub_key = signing_key.verifying_key();
        let pub_der = pub_key
            .to_public_key_der()
            .map_err(|e| RelayError::IoError(e.to_string()))?;
        let pub_der_bytes = pub_der.as_bytes();
        let my_device_id = DeviceId::from_public_der(pub_der_bytes);

        let timestamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_err(|e| RelayError::IoError(e.to_string()))?
            .as_secs();

        let mut nonce_bytes = [0u8; 16];
        rand::Rng::fill(&mut rand::thread_rng(), &mut nonce_bytes[..]);
        let nonce_b64 = URL_SAFE_NO_PAD.encode(nonce_bytes);
        let pub_der_b64 = URL_SAFE_NO_PAD.encode(pub_der_bytes);

        let canonical = format!(
            "MARK-RELAY-AUTH-V1\n{}\n{}\n{}",
            my_device_id.as_str(),
            timestamp,
            nonce_b64
        );
        let sig: p256::ecdsa::Signature = signing_key.sign(canonical.as_bytes());
        let sig_b64 = URL_SAFE_NO_PAD.encode(sig.to_der().as_bytes());

        let reg_msg = format!(
            "AUTH_REGISTER {} {} {} {} {}\n",
            my_device_id.as_str(),
            timestamp,
            nonce_b64,
            pub_der_b64,
            sig_b64
        );
        stream
            .write_all(reg_msg.as_bytes())
            .map_err(|e| RelayError::IoError(e.to_string()))?;
        stream
            .flush()
            .map_err(|e| RelayError::IoError(e.to_string()))?;

        let read_stream = stream
            .try_clone()
            .map_err(|e| RelayError::IoError(e.to_string()))?;
        let mut reader = BufReader::new(read_stream);

        let mut resp = String::new();
        reader
            .read_line(&mut resp)
            .map_err(|e| RelayError::IoError(e.to_string()))?;
        if !resp.starts_with("OK") {
            return Err(RelayError::AuthenticationFailed);
        }

        Ok(Self {
            stream,
            reader,
            my_device_id,
        })
    }

    pub fn connect(server_addr: &str, _my_device_id: DeviceId) -> Result<Self, RelayError> {
        let key = SigningKey::random(&mut OsRng);
        Self::connect_authenticated(server_addr, &key)
    }

    pub fn send_envelope(
        &mut self,
        target: DeviceId,
        opaque_payload: Vec<u8>,
    ) -> Result<(), RelayError> {
        let env = EncryptedEnvelope::new(target, self.my_device_id.clone(), opaque_payload)?;
        env.write_to(&mut self.stream)
    }

    pub fn recv_envelope(
        &mut self,
        timeout: Option<Duration>,
    ) -> Result<EncryptedEnvelope, RelayError> {
        let stream_ref = self.reader.get_mut();
        stream_ref
            .set_read_timeout(timeout)
            .map_err(|e| RelayError::IoError(e.to_string()))?;
        EncryptedEnvelope::read_from(&mut self.reader)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_envelope_roundtrip_serialization() {
        let dummy_der1 = b"peer-target-public-key-sample-p256";
        let dummy_der2 = b"peer-source-public-key-sample-p256";
        let target = DeviceId::from_public_der(dummy_der1);
        let source = DeviceId::from_public_der(dummy_der2);

        let ciphertext = vec![0xde, 0xad, 0xbe, 0xef, 0x42];
        let env =
            EncryptedEnvelope::new(target.clone(), source.clone(), ciphertext.clone()).unwrap();

        let serialized = env.serialize();
        let deserialized =
            EncryptedEnvelope::deserialize(&serialized).expect("Desserialização falhou");

        assert_eq!(deserialized.target, target);
        assert_eq!(deserialized.source, source);
        assert_eq!(deserialized.opaque_payload, ciphertext);
    }

    #[test]
    fn test_blind_relay_router_forwarding() {
        let router = BlindRelayRouter::new();
        let target = DeviceId::from_public_der(b"target-node");
        let source = DeviceId::from_public_der(b"source-node");

        router.register_peer(target.clone());

        let env = EncryptedEnvelope::new(target.clone(), source.clone(), vec![1, 2, 3]).unwrap();
        assert!(router.forward(env.clone()).is_ok());

        let drained = router.drain_for(&target);
        assert_eq!(drained.len(), 1);
        assert_eq!(drained[0], env);

        // Encaminhamento para peer desconhecido falha com PeerNotFound
        let unknown = DeviceId::from_public_der(b"unknown-node");
        let env_bad = EncryptedEnvelope::new(unknown, source, vec![1]).unwrap();
        assert_eq!(router.forward(env_bad), Err(RelayError::PeerNotFound));
    }

    #[test]
    fn test_relay_server_and_client_network_forwarding() {
        let mut server = RelayServer::bind("127.0.0.1:0").expect("Falha ao ligar RelayServer");
        let server_addr = server.local_addr().to_string();
        server.start().expect("Falha ao iniciar RelayServer");

        let key_alice = SigningKey::random(&mut OsRng);
        let key_bob = SigningKey::random(&mut OsRng);
        let dev_alice = DeviceId::from_public_der(
            key_alice
                .verifying_key()
                .to_public_key_der()
                .unwrap()
                .as_bytes(),
        );
        let dev_bob = DeviceId::from_public_der(
            key_bob
                .verifying_key()
                .to_public_key_der()
                .unwrap()
                .as_bytes(),
        );

        // Alice e Bob conectam via TCP autenticado com prova criptográfica ao relay
        let mut client_alice = RelayClient::connect_authenticated(&server_addr, &key_alice)
            .expect("Alice connect falhou");
        let mut client_bob =
            RelayClient::connect_authenticated(&server_addr, &key_bob).expect("Bob connect falhou");

        thread::sleep(Duration::from_millis(50));

        // Alice envia envelope cifrado opaco para Bob
        let payload = vec![0xCA, 0xFE, 0xBA, 0xBE, 0x42];
        client_alice
            .send_envelope(dev_bob.clone(), payload.clone())
            .expect("Alice envio falhou");

        // Bob recebe envelope
        let received = client_bob
            .recv_envelope(Some(Duration::from_secs(2)))
            .expect("Bob recepção falhou");

        assert_eq!(received.source, dev_alice);
        assert_eq!(received.target, dev_bob);
        assert_eq!(received.opaque_payload, payload);

        server.stop();
    }

    #[test]
    fn test_relay_rejects_unauthenticated_registration() {
        let mut server = RelayServer::bind("127.0.0.1:0").expect("Falha ao ligar RelayServer");
        let server_addr = server.local_addr().to_string();
        server.start().expect("Falha ao iniciar RelayServer");

        let mut stream = TcpStream::connect(&server_addr).expect("Connect falhou");
        // Registro sem assinatura/prova de identidade criptográfica
        stream
            .write_all(b"REGISTER rogue_device_unauthenticated\n")
            .unwrap();
        stream.flush().unwrap();

        let mut reader = BufReader::new(stream);
        let mut response = String::new();
        let _ = reader.read_line(&mut response);

        assert!(
            response.contains("AUTH_REQUIRED") || response.contains("AUTH_FAILED"),
            "FALHA RED: Servidor de Relay aceitou registro não autenticado sem prova de identidade! Resposta: '{}'",
            response.trim()
        );

        server.stop();
    }

    #[test]
    fn test_relay_rate_limiter_and_queue_exhaustion_defense() {
        let router = BlindRelayRouter::new();
        let target = DeviceId::from_public_der(b"target-node");
        let source = DeviceId::from_public_der(b"source-node");

        router.register_peer(target.clone());

        let mut dropped = 0;
        for _ in 0..1000 {
            let env =
                EncryptedEnvelope::new(target.clone(), source.clone(), vec![0xEE; 512]).unwrap();
            if router.forward(env).is_err() {
                dropped += 1;
            }
        }

        assert!(
            dropped > 0,
            "FALHA RED: Nenhum pacote foi descartado pelo Relay sob ataque de burst DoS!"
        );
    }

    #[test]
    fn test_relay_blocks_open_proxy_attempts_and_arbitrary_ips() {
        let arbitrary_destinations = [
            "192.168.1.1:80",
            "10.0.0.1:443",
            "8.8.8.8",
            "http://evil.com",
            "localhost:22",
        ];
        let valid_source = DeviceId::from_public_der(b"source-valid-key");
        for dest in arbitrary_destinations {
            let target = DeviceId(dest.to_string());
            let result = EncryptedEnvelope::new(target, valid_source.clone(), vec![1, 2, 3]);
            assert!(
                result.is_err(),
                "FALHA RED: Envelope aceitou destino arbitrário como proxy aberto: {}",
                dest
            );
        }
    }
}

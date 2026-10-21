//! Módulo de Transporte Seguro e Enquadramento Binário do MARK Link.
//!
//! Protocolo binário MLNK (v1):
//! - Cabeçalho autenticado de 20 bytes (passado como AAD no AES-256-GCM).
//! - Nonce determinístico de 96 bits (4 bytes salt de sessão + 8 bytes seq) imune a colisões.
//! - Criptografia autenticada AEAD de ponta a ponta.
//! - Proteção contra exaustão de memória e fragmentação (limites de 64 KB por frame e 12 MB total).

use aes_gcm::{
    aead::{Aead, KeyInit, Payload},
    Aes256Gcm, Nonce,
};
use hkdf::Hkdf;
use sha2::Sha256;
use std::collections::{HashMap, VecDeque};
use std::time::{SystemTime, UNIX_EPOCH};
use zeroize::Zeroize;

pub const MAGIC: &[u8; 4] = b"MLNK";
pub const PROTOCOL_VERSION: u8 = 1;
pub const HEADER_SIZE: usize = 20;
pub const TAG_SIZE: usize = 16;
pub const MAX_FRAME_SIZE: usize = 64 * 1024; // 64 KB por frame de rede
pub const MAX_PAYLOAD_SIZE: usize = 4 * 1024 * 1024; // 4 MB máximo (mitigação rígida de OOM)
pub const NONCE_SALT_SIZE: usize = 4;
pub const MAX_SEQUENCE_NONCE: u64 = (1u64 << 32) - 1; // Prevenção de colisão e wrap-around

/// Tipos de pacotes suportados pelo transporte MARK Link.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(u8)]
pub enum PacketType {
    HandshakeInit = 0x01,
    HandshakeResponse = 0x02,
    Data = 0x03,
    Ping = 0x04,
    Pong = 0x05,
    Close = 0x06,
    Error = 0x07,
    RelayEnvelope = 0x08,
    SessionResume = 0x09,
    SessionResumeOk = 0x0A,
}

impl PacketType {
    pub fn from_u8(val: u8) -> Result<Self, TransportError> {
        match val {
            0x01 => Ok(PacketType::HandshakeInit),
            0x02 => Ok(PacketType::HandshakeResponse),
            0x03 => Ok(PacketType::Data),
            0x04 => Ok(PacketType::Ping),
            0x05 => Ok(PacketType::Pong),
            0x06 => Ok(PacketType::Close),
            0x07 => Ok(PacketType::Error),
            0x08 => Ok(PacketType::RelayEnvelope),
            0x09 => Ok(PacketType::SessionResume),
            0x0A => Ok(PacketType::SessionResumeOk),
            _ => Err(TransportError::InvalidPacketType(val)),
        }
    }
}

/// Modo de conexão ativa negociado entre os nós.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ConnectionMode {
    DirectLoopback,
    DirectLocal,
    DirectRemote,
    Relayed,
}

impl ConnectionMode {
    pub fn as_str(&self) -> &'static str {
        match self {
            ConnectionMode::DirectLoopback => "DirectLoopback",
            ConnectionMode::DirectLocal => "DirectLocal",
            ConnectionMode::DirectRemote => "DirectRemote",
            ConnectionMode::Relayed => "Relayed",
        }
    }
}

#[derive(Debug, PartialEq, Eq)]
pub enum TransportError {
    InvalidMagic,
    UnsupportedVersion(u8),
    InvalidPacketType(u8),
    PayloadTooLarge(usize),
    FrameTooSmall,
    AuthenticationFailed,
    KeyDerivationFailed,
    BufferUnderflow,
    SequenceOverflow,
    ReplayDetected(u64),
}

impl std::fmt::Display for TransportError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            TransportError::InvalidMagic => write!(f, "Magic de pacote inválido (esperado MLNK)"),
            TransportError::UnsupportedVersion(v) => {
                write!(f, "Versão de protocolo não suportada: {}", v)
            }
            TransportError::InvalidPacketType(t) => {
                write!(f, "Tipo de pacote desconhecido: 0x{:02X}", t)
            }
            TransportError::PayloadTooLarge(sz) => {
                write!(f, "Tamanho de payload excede o limite: {} bytes", sz)
            }
            TransportError::FrameTooSmall => write!(f, "Frame menor que o cabeçalho mínimo"),
            TransportError::AuthenticationFailed => write!(
                f,
                "Falha de autenticação ou adulteração de dados (MAC inválido)"
            ),
            TransportError::KeyDerivationFailed => write!(f, "Erro na derivação de chaves HKDF"),
            TransportError::BufferUnderflow => {
                write!(f, "Dados insuficientes no buffer de leitura")
            }
            TransportError::SequenceOverflow => {
                write!(
                    f,
                    "Contador de sequência de nonces esgotado (prevenção de colisão de nonce)"
                )
            }
            TransportError::ReplayDetected(seq) => {
                write!(
                    f,
                    "Pacote duplicado ou fora de janela detectado (replay, seq={})",
                    seq
                )
            }
        }
    }
}

impl std::error::Error for TransportError {}

/// Resposta de requisição armazenada na cache de idempotência.
#[derive(Debug, Clone)]
pub struct CachedResponse {
    pub status: u16,
    pub body: Vec<u8>,
    pub timestamp: u64,
}

impl CachedResponse {
    pub fn is_expired(&self, ttl_secs: u64, now: u64) -> bool {
        now.saturating_sub(self.timestamp) > ttl_secs
    }
}

/// Cache LRU thread-safe para idempotência de mutações no MARK Link.
#[derive(Debug, Clone)]
pub struct IdempotencyCache {
    pub max_capacity: usize,
    pub ttl_secs: u64,
    entries: HashMap<String, CachedResponse>,
    order: VecDeque<String>,
}

impl IdempotencyCache {
    pub fn new(max_capacity: usize, ttl_secs: u64) -> Self {
        Self {
            max_capacity,
            ttl_secs,
            entries: HashMap::new(),
            order: VecDeque::new(),
        }
    }

    pub fn get(&mut self, request_id: &str) -> Option<CachedResponse> {
        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs();

        if let Some(entry) = self.entries.get(request_id) {
            if entry.is_expired(self.ttl_secs, now) {
                self.entries.remove(request_id);
                return None;
            }
            // Move para o final (MRU)
            if let Some(pos) = self.order.iter().position(|k| k == request_id) {
                self.order.remove(pos);
            }
            self.order.push_back(request_id.to_string());
            Some(entry.clone())
        } else {
            None
        }
    }

    pub fn put(&mut self, request_id: String, status: u16, body: Vec<u8>) {
        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs();

        if self.entries.contains_key(&request_id) {
            self.entries.remove(&request_id);
            if let Some(pos) = self.order.iter().position(|k| k == &request_id) {
                self.order.remove(pos);
            }
        } else if self.entries.len() >= self.max_capacity {
            if let Some(oldest) = self.order.pop_front() {
                self.entries.remove(&oldest);
            }
        }

        self.order.push_back(request_id.clone());
        self.entries.insert(
            request_id,
            CachedResponse {
                status,
                body,
                timestamp: now,
            },
        );
    }

    pub fn len(&self) -> usize {
        self.entries.len()
    }

    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }
}

/// Cabeçalho canônico binário de 20 bytes do MARK Link.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PacketHeader {
    pub packet_type: PacketType,
    pub flags: u16,
    pub sequence: u64,
    pub length: u32,
}

impl PacketHeader {
    pub fn encode(&self, out: &mut [u8; HEADER_SIZE]) {
        out[0..4].copy_from_slice(MAGIC);
        out[4] = PROTOCOL_VERSION;
        out[5] = self.packet_type as u8;
        out[6..8].copy_from_slice(&self.flags.to_be_bytes());
        out[8..16].copy_from_slice(&self.sequence.to_be_bytes());
        out[16..20].copy_from_slice(&self.length.to_be_bytes());
    }

    pub fn decode(bytes: &[u8]) -> Result<Self, TransportError> {
        if bytes.len() < HEADER_SIZE {
            return Err(TransportError::FrameTooSmall);
        }
        if &bytes[0..4] != MAGIC {
            return Err(TransportError::InvalidMagic);
        }
        let version = bytes[4];
        if version != PROTOCOL_VERSION {
            return Err(TransportError::UnsupportedVersion(version));
        }
        let packet_type = PacketType::from_u8(bytes[5])?;
        let flags = u16::from_be_bytes(bytes[6..8].try_into().unwrap());
        let sequence = u64::from_be_bytes(bytes[8..16].try_into().unwrap());
        let length = u32::from_be_bytes(bytes[16..20].try_into().unwrap());

        if length as usize > MAX_PAYLOAD_SIZE {
            return Err(TransportError::PayloadTooLarge(length as usize));
        }

        Ok(PacketHeader {
            packet_type,
            flags,
            sequence,
            length,
        })
    }
}

use crate::identity::ReplayFilter;

/// Sessão criptográfica do canal MARK Link.
pub struct SecureChannel {
    cipher: Aes256Gcm,
    nonce_salt: [u8; NONCE_SALT_SIZE],
    tx_sequence: u64,
}

impl SecureChannel {
    /// Inicializa o canal seguro com chave simétrica de 256 bits e salt de 4 bytes para nonces.
    pub fn new(key: &[u8; 32], nonce_salt: [u8; NONCE_SALT_SIZE]) -> Self {
        let cipher = Aes256Gcm::new(key.into());
        Self {
            cipher,
            nonce_salt,
            tx_sequence: 1,
        }
    }

    /// Deriva chave de sessão e salt de nonce a partir do segredo compartilhado (ECDH) via HKDF-SHA256.
    pub fn derive_from_secret(
        shared_secret: &[u8],
        salt: &[u8],
        info: &[u8],
    ) -> Result<Self, TransportError> {
        let hk = Hkdf::<Sha256>::new(Some(salt), shared_secret);
        let mut okm = [0u8; 36]; // 32 bytes de chave AES + 4 bytes de salt de nonce
        hk.expand(info, &mut okm)
            .map_err(|_| TransportError::KeyDerivationFailed)?;

        let mut key = [0u8; 32];
        key.copy_from_slice(&okm[0..32]);
        let mut nonce_salt = [0u8; NONCE_SALT_SIZE];
        nonce_salt.copy_from_slice(&okm[32..36]);

        okm.zeroize();
        let channel = Self::new(&key, nonce_salt);
        key.zeroize();
        Ok(channel)
    }

    /// Constrói o nonce determinístico de 96 bits (12 bytes) para a sequência dada.
    fn make_nonce(&self, seq: u64) -> [u8; 12] {
        let mut nonce = [0u8; 12];
        nonce[0..4].copy_from_slice(&self.nonce_salt);
        nonce[4..12].copy_from_slice(&seq.to_be_bytes());
        nonce
    }

    /// Cifra e empacota uma mensagem em um frame MLNK completo.
    pub fn seal_packet(
        &mut self,
        packet_type: PacketType,
        plaintext: &[u8],
    ) -> Result<Vec<u8>, TransportError> {
        let seq = self.tx_sequence;
        if seq >= MAX_SEQUENCE_NONCE {
            return Err(TransportError::SequenceOverflow);
        }
        self.tx_sequence = self
            .tx_sequence
            .checked_add(1)
            .ok_or(TransportError::SequenceOverflow)?;

        if plaintext.len() + TAG_SIZE > MAX_PAYLOAD_SIZE {
            return Err(TransportError::PayloadTooLarge(plaintext.len()));
        }

        let mut header_bytes = [0u8; HEADER_SIZE];
        let header = PacketHeader {
            packet_type,
            flags: 0,
            sequence: seq,
            length: (plaintext.len() + TAG_SIZE) as u32,
        };
        header.encode(&mut header_bytes);

        let nonce_bytes = self.make_nonce(seq);
        let nonce = Nonce::from_slice(&nonce_bytes);

        // Header como AAD (Additional Authenticated Data) garante integridade total dos metadados
        let payload = Payload {
            msg: plaintext,
            aad: &header_bytes,
        };

        let ciphertext = self
            .cipher
            .encrypt(nonce, payload)
            .map_err(|_| TransportError::AuthenticationFailed)?;

        let mut frame = Vec::with_capacity(HEADER_SIZE + ciphertext.len());
        frame.extend_from_slice(&header_bytes);
        frame.extend_from_slice(&ciphertext);
        Ok(frame)
    }

    /// Decifra e valida a integridade de um frame MLNK recebido.
    pub fn unseal_packet(&self, frame: &[u8]) -> Result<(PacketHeader, Vec<u8>), TransportError> {
        if frame.len() < HEADER_SIZE + TAG_SIZE {
            return Err(TransportError::FrameTooSmall);
        }

        let header = PacketHeader::decode(&frame[0..HEADER_SIZE])?;
        let expected_total = HEADER_SIZE + header.length as usize;
        if frame.len() < expected_total {
            return Err(TransportError::BufferUnderflow);
        }

        let header_bytes = &frame[0..HEADER_SIZE];
        let ciphertext = &frame[HEADER_SIZE..expected_total];

        let nonce_bytes = self.make_nonce(header.sequence);
        let nonce = Nonce::from_slice(&nonce_bytes);

        let payload = Payload {
            msg: ciphertext,
            aad: header_bytes,
        };

        let plaintext = self
            .cipher
            .decrypt(nonce, payload)
            .map_err(|_| TransportError::AuthenticationFailed)?;

        Ok((header, plaintext))
    }
}

/// Canal criptográfico bidirecional com separação estrita de chaves e nonces (TX vs RX).
#[derive(Clone)]
pub struct DirectionalChannel {
    tx_cipher: Aes256Gcm,
    rx_cipher: Aes256Gcm,
    tx_nonce_salt: [u8; NONCE_SALT_SIZE],
    rx_nonce_salt: [u8; NONCE_SALT_SIZE],
    tx_sequence: u64,
    replay_filter: ReplayFilter,
}

impl DirectionalChannel {
    /// Inicializa com chaves simétricas e salts distintos para transmissão e recepção.
    pub fn new(
        tx_key: &[u8; 32],
        tx_nonce_salt: [u8; NONCE_SALT_SIZE],
        rx_key: &[u8; 32],
        rx_nonce_salt: [u8; NONCE_SALT_SIZE],
    ) -> Self {
        let tx_cipher = Aes256Gcm::new(tx_key.into());
        let rx_cipher = Aes256Gcm::new(rx_key.into());
        Self {
            tx_cipher,
            rx_cipher,
            tx_nonce_salt,
            rx_nonce_salt,
            tx_sequence: 1,
            replay_filter: ReplayFilter::default(),
        }
    }

    /// Deriva canal bidirecional seguro via HKDF-SHA256 a partir de segredo compartilhado (ECDH).
    /// Garante que TX e RX utilizem chaves e salts 100% distintos, eliminando qualquer colisão de nonce.
    pub fn derive_session_keys(
        shared_secret: &[u8],
        client_nonce: &[u8],
        server_nonce: &[u8],
        is_server: bool,
    ) -> Result<Self, TransportError> {
        Self::derive_session_keys_bound(shared_secret, client_nonce, server_nonce, None, is_server)
    }

    /// Deriva canal bidirecional vinculando criptograficamente o transcript da sessão (Session Binding).
    pub fn derive_session_keys_bound(
        shared_secret: &[u8],
        client_nonce: &[u8],
        server_nonce: &[u8],
        transcript_hash: Option<&[u8]>,
        is_server: bool,
    ) -> Result<Self, TransportError> {
        let mut salt = Vec::with_capacity(client_nonce.len() + server_nonce.len());
        salt.extend_from_slice(client_nonce);
        salt.extend_from_slice(server_nonce);

        let hk = Hkdf::<Sha256>::new(Some(&salt), shared_secret);
        let mut okm = [0u8; 72]; // 32 c2s_key + 4 c2s_salt + 32 s2c_key + 4 s2c_salt

        let mut info = Vec::with_capacity(64);
        match transcript_hash {
            Some(th) => {
                info.extend_from_slice(b"mark-link-v1-session-bound\n");
                info.extend_from_slice(th);
            }
            None => {
                info.extend_from_slice(b"mark-link-v1-directional-session");
            }
        }

        hk.expand(&info, &mut okm)
            .map_err(|_| TransportError::KeyDerivationFailed)?;

        let mut c2s_key = [0u8; 32];
        c2s_key.copy_from_slice(&okm[0..32]);
        let mut c2s_salt = [0u8; NONCE_SALT_SIZE];
        c2s_salt.copy_from_slice(&okm[32..36]);

        let mut s2c_key = [0u8; 32];
        s2c_key.copy_from_slice(&okm[36..68]);
        let mut s2c_salt = [0u8; NONCE_SALT_SIZE];
        s2c_salt.copy_from_slice(&okm[68..72]);

        okm.zeroize();

        let channel = if is_server {
            Self::new(&s2c_key, s2c_salt, &c2s_key, c2s_salt)
        } else {
            Self::new(&c2s_key, c2s_salt, &s2c_key, s2c_salt)
        };

        c2s_key.zeroize();
        s2c_key.zeroize();

        Ok(channel)
    }

    fn make_tx_nonce(&self, seq: u64) -> [u8; 12] {
        let mut nonce = [0u8; 12];
        nonce[0..4].copy_from_slice(&self.tx_nonce_salt);
        nonce[4..12].copy_from_slice(&seq.to_be_bytes());
        nonce
    }

    fn make_rx_nonce(&self, seq: u64) -> [u8; 12] {
        let mut nonce = [0u8; 12];
        nonce[0..4].copy_from_slice(&self.rx_nonce_salt);
        nonce[4..12].copy_from_slice(&seq.to_be_bytes());
        nonce
    }

    /// Cifra e empacota uma mensagem em um frame MLNK usando a chave e nonce de TX.
    pub fn seal_packet(
        &mut self,
        packet_type: PacketType,
        plaintext: &[u8],
    ) -> Result<Vec<u8>, TransportError> {
        let seq = self.tx_sequence;
        if seq >= MAX_SEQUENCE_NONCE {
            return Err(TransportError::SequenceOverflow);
        }
        self.tx_sequence = self
            .tx_sequence
            .checked_add(1)
            .ok_or(TransportError::SequenceOverflow)?;

        if plaintext.len() + TAG_SIZE > MAX_PAYLOAD_SIZE {
            return Err(TransportError::PayloadTooLarge(plaintext.len()));
        }

        let mut header_bytes = [0u8; HEADER_SIZE];
        let header = PacketHeader {
            packet_type,
            flags: 0,
            sequence: seq,
            length: (plaintext.len() + TAG_SIZE) as u32,
        };
        header.encode(&mut header_bytes);

        let nonce_bytes = self.make_tx_nonce(seq);
        let nonce = Nonce::from_slice(&nonce_bytes);

        let payload = Payload {
            msg: plaintext,
            aad: &header_bytes,
        };

        let ciphertext = self
            .tx_cipher
            .encrypt(nonce, payload)
            .map_err(|_| TransportError::AuthenticationFailed)?;

        let mut frame = Vec::with_capacity(HEADER_SIZE + ciphertext.len());
        frame.extend_from_slice(&header_bytes);
        frame.extend_from_slice(&ciphertext);
        Ok(frame)
    }

    /// Decifra e valida a integridade de um frame recebido com filtro anti-replay.
    pub fn unseal_packet(
        &mut self,
        frame: &[u8],
    ) -> Result<(PacketHeader, Vec<u8>), TransportError> {
        if frame.len() < HEADER_SIZE + TAG_SIZE {
            return Err(TransportError::FrameTooSmall);
        }

        let header = PacketHeader::decode(&frame[0..HEADER_SIZE])?;
        let expected_total = HEADER_SIZE + header.length as usize;
        if frame.len() < expected_total {
            return Err(TransportError::BufferUnderflow);
        }

        // Validação anti-replay preliminar antes da descriptografia
        if !self.replay_filter.check_sequence(header.sequence) {
            return Err(TransportError::ReplayDetected(header.sequence));
        }

        let header_bytes = &frame[0..HEADER_SIZE];
        let ciphertext = &frame[HEADER_SIZE..expected_total];

        let nonce_bytes = self.make_rx_nonce(header.sequence);
        let nonce = Nonce::from_slice(&nonce_bytes);

        let payload = Payload {
            msg: ciphertext,
            aad: header_bytes,
        };

        let plaintext = self
            .rx_cipher
            .decrypt(nonce, payload)
            .map_err(|_| TransportError::AuthenticationFailed)?;

        self.replay_filter.commit_sequence(header.sequence);

        Ok((header, plaintext))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_packet_header_roundtrip() {
        let header = PacketHeader {
            packet_type: PacketType::Data,
            flags: 0x0001,
            sequence: 42,
            length: 1024,
        };
        let mut encoded = [0u8; HEADER_SIZE];
        header.encode(&mut encoded);

        let decoded = PacketHeader::decode(&encoded).expect("Decodificação falhou");
        assert_eq!(header, decoded);
    }

    #[test]
    fn test_packet_header_rejections() {
        // Magic inválido
        let mut bad_magic = [0u8; HEADER_SIZE];
        bad_magic[0..4].copy_from_slice(b"NOPE");
        assert_eq!(
            PacketHeader::decode(&bad_magic),
            Err(TransportError::InvalidMagic)
        );

        // Buffer curto
        assert_eq!(
            PacketHeader::decode(&[0u8; 10]),
            Err(TransportError::FrameTooSmall)
        );
    }

    #[test]
    fn test_secure_channel_seal_and_unseal() {
        let key = [0x5au8; 32];
        let salt = [0x01, 0x02, 0x03, 0x04];
        let mut channel_tx = SecureChannel::new(&key, salt);
        let channel_rx = SecureChannel::new(&key, salt);

        let msg = b"POST /v1/chat/completions HTTP/1.1\r\nContent-Type: application/json\r\n\r\n{\"prompt\": \"teste\"}";
        let frame = channel_tx
            .seal_packet(PacketType::Data, msg)
            .expect("Seal falhou");

        assert!(frame.len() > HEADER_SIZE);

        let (header, plaintext) = channel_rx.unseal_packet(&frame).expect("Unseal falhou");
        assert_eq!(header.packet_type, PacketType::Data);
        assert_eq!(header.sequence, 1);
        assert_eq!(plaintext, msg);
    }

    #[test]
    fn test_tamper_detection_in_ciphertext_and_header() {
        let key = [0x3bu8; 32];
        let salt = [0xaa, 0xbb, 0xcc, 0xdd];
        let mut channel_tx = SecureChannel::new(&key, salt);
        let channel_rx = SecureChannel::new(&key, salt);

        let mut frame = channel_tx
            .seal_packet(PacketType::Data, b"dados ultra confidenciais")
            .unwrap();

        // Adultera um byte no ciphertext
        let last = frame.len() - 1;
        frame[last] ^= 0x01;
        assert_eq!(
            channel_rx.unseal_packet(&frame),
            Err(TransportError::AuthenticationFailed)
        );

        // Restaura e adultera um byte no cabeçalho (AAD)
        frame[last] ^= 0x01;
        frame[5] = PacketType::Ping as u8; // Altera packet_type de Data para Ping
        assert_eq!(
            channel_rx.unseal_packet(&frame),
            Err(TransportError::AuthenticationFailed)
        );
    }

    #[test]
    fn test_directional_channel_roundtrip_and_key_separation() {
        let shared_secret = b"ecdh-shared-secret-p256-32-bytes";
        let client_nonce = b"client-random-nonce-32-bytes-len";
        let server_nonce = b"server-random-nonce-32-bytes-len";

        let mut client_chan = DirectionalChannel::derive_session_keys(
            shared_secret,
            client_nonce,
            server_nonce,
            false,
        )
        .expect("Falha ao derivar canal cliente");
        let mut server_chan = DirectionalChannel::derive_session_keys(
            shared_secret,
            client_nonce,
            server_nonce,
            true,
        )
        .expect("Falha ao derivar canal servidor");

        // 1. Cliente envia para Servidor
        let client_msg = b"GET /v1/chat/completions HTTP/1.1\r\nHost: mark.local\r\n\r\n";
        let frame_c2s = client_chan
            .seal_packet(PacketType::Data, client_msg)
            .expect("Client seal falhou");

        let (hdr_s, unsealed_s) = server_chan
            .unseal_packet(&frame_c2s)
            .expect("Server unseal falhou");
        assert_eq!(hdr_s.packet_type, PacketType::Data);
        assert_eq!(hdr_s.sequence, 1);
        assert_eq!(unsealed_s, client_msg);

        // 2. Servidor responde para Cliente
        let server_msg =
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n{\"status\":\"ok\"}";
        let frame_s2c = server_chan
            .seal_packet(PacketType::Data, server_msg)
            .expect("Server seal falhou");

        let (hdr_c, unsealed_c) = client_chan
            .unseal_packet(&frame_s2c)
            .expect("Client unseal falhou");
        assert_eq!(hdr_c.packet_type, PacketType::Data);
        assert_eq!(hdr_c.sequence, 1);
        assert_eq!(unsealed_c, server_msg);

        // 3. Garantia de separação de chaves e não-colisão de nonces:
        // O frame gerado pelo servidor com seq=1 NÃO pode ser decifrado com c2s_key.
        // Em um canal servidor novo (onde seq=1 não foi filtrado ainda), falha com AuthenticationFailed.
        let mut server_chan_fresh = DirectionalChannel::derive_session_keys(
            shared_secret,
            client_nonce,
            server_nonce,
            true,
        )
        .unwrap();
        assert_eq!(
            server_chan_fresh.unseal_packet(&frame_s2c),
            Err(TransportError::AuthenticationFailed)
        );
    }

    #[test]
    fn test_directional_channel_anti_replay() {
        let shared_secret = b"ecdh-shared-secret-p256-32-bytes";
        let client_nonce = b"client-random-nonce-32-bytes-len";
        let server_nonce = b"server-random-nonce-32-bytes-len";

        let mut client_chan = DirectionalChannel::derive_session_keys(
            shared_secret,
            client_nonce,
            server_nonce,
            false,
        )
        .unwrap();
        let mut server_chan = DirectionalChannel::derive_session_keys(
            shared_secret,
            client_nonce,
            server_nonce,
            true,
        )
        .unwrap();

        let frame = client_chan.seal_packet(PacketType::Ping, b"ping").unwrap();

        // Primeiro unseal: OK
        let (hdr, _) = server_chan.unseal_packet(&frame).unwrap();
        assert_eq!(hdr.sequence, 1);

        // Tentativa de replay do mesmo frame: deve ser rejeitado pelo filtro de sequência
        assert_eq!(
            server_chan.unseal_packet(&frame),
            Err(TransportError::ReplayDetected(1))
        );
    }

    #[test]
    fn test_directional_channel_overflow_failsafe() {
        let mut chan = DirectionalChannel::new(
            &[0x11; 32],
            [0x01, 0x02, 0x03, 0x04],
            &[0x22; 32],
            [0x05, 0x06, 0x07, 0x08],
        );
        chan.tx_sequence = u64::MAX; // Simula limite máximo de nonces
        assert_eq!(
            chan.seal_packet(PacketType::Data, b"test"),
            Err(TransportError::SequenceOverflow)
        );
    }

    #[test]
    fn test_mlnk_strictly_enforces_4mb_payload_limit() {
        assert_eq!(
            MAX_PAYLOAD_SIZE,
            4 * 1024 * 1024,
            "FALHA RED: MAX_PAYLOAD_SIZE deve ser estritamente 4 MB!"
        );
    }

    #[test]
    fn test_mlnk_fuzzing_robustness_and_truncated_rejections() {
        let key = [0x55u8; 32];
        let salt = [1, 2, 3, 4];
        let channel = SecureChannel::new(&key, salt);

        // Frame menor que o cabeçalho (Truncado)
        let truncated = b"MLNK\x01\x03";
        assert_eq!(
            channel.unseal_packet(truncated),
            Err(TransportError::FrameTooSmall)
        );

        // Magic inválido
        let mut bad_magic = [0u8; 36];
        bad_magic[0..4].copy_from_slice(b"BADM");
        bad_magic[4] = 1; // Versão 1
        bad_magic[5] = 3; // Data
        assert_eq!(
            channel.unseal_packet(&bad_magic),
            Err(TransportError::InvalidMagic)
        );

        // Versão incompatível
        let mut bad_version = [0u8; 36];
        bad_version[0..4].copy_from_slice(MAGIC);
        bad_version[4] = 99; // Versão 99
        bad_version[5] = 3; // Data
        assert_eq!(
            channel.unseal_packet(&bad_version),
            Err(TransportError::UnsupportedVersion(99))
        );
    }
}

//! Módulo de Identidade, Autenticação e Controle de Acesso do MARK Link.
//!
//! Garante isolamento estrito por aplicação:
//! - Validação de identidade criptográfica por dispositivo (EC P-256 / SHA-256).
//! - Prova de posse e aperto de mão autenticado.
//! - Allowlist estrita de rotas e métodos autorizados do MARK (rejeição de tráfego genérico).
//! - Proteção anti-replay com janela de nonces e sequências monotônicas.

use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine as _};
use p256::pkcs8::DecodePublicKey;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::HashSet;
use std::time::{SystemTime, UNIX_EPOCH};

pub const CLOCK_SKEW_SECONDS: u64 = 60;
pub const MAX_NONCE_TRACKING: usize = 256;

/// Lista estrita de rotas HTTP do ecossistema MARK permitidas pelo túnel.
/// Qualquer outra rota ou porta é terminantemente bloqueada para isolar o sistema.
pub const ALLOWED_MARK_ROUTES: &[&str] = &[
    "/v1/chat/completions",
    "/v1/voice/turn",
    "/v1/session",
    "/v1/session/renew",
    "/v1/pair/enroll",
    "/v1/pair/status",
    "/v1/device/rotate",
    "/v1/history",
    "/v1/agenda",
    "/v1/wear/telemetry",
    "/v1/wear/summary",
    "/health",
    "/call",
];

/// Status de autorização de um dispositivo no ecossistema MARK.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum DeviceStatus {
    Authorized,
    Pending,
    Revoked,
    Unknown,
}

impl DeviceStatus {
    pub fn as_str(&self) -> &'static str {
        match self {
            DeviceStatus::Authorized => "authorized",
            DeviceStatus::Pending => "pending",
            DeviceStatus::Revoked => "revoked",
            DeviceStatus::Unknown => "unknown",
        }
    }
}

/// Identidade criptográfica de um dispositivo no MARK Link.
#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct DeviceId(pub String);

impl DeviceId {
    /// Constrói um DeviceId a partir da chave pública DER do dispositivo.
    /// Segue a convenção do MARK: URL_SAFE_NO_PAD(SHA256(public_der)).
    pub fn from_public_der(public_der: &[u8]) -> Self {
        let mut hasher = Sha256::new();
        hasher.update(public_der);
        let digest = hasher.finalize();
        DeviceId(URL_SAFE_NO_PAD.encode(digest))
    }

    pub fn as_str(&self) -> &str {
        &self.0
    }

    pub fn is_valid(&self) -> bool {
        self.0.len() == 43
            && self
                .0
                .chars()
                .all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_')
    }
}

/// Categoria de privilégio de endpoints do MARK Link.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum RouteCategory {
    ReadOnly,
    Mutate,
}

pub const READONLY_ROUTES: &[&str] = &[
    "/health",
    "/v1/history",
    "/v1/agenda",
    "/v1/pair/status",
    "/v1/wear/summary",
];

pub const MUTATE_ROUTES: &[&str] = &[
    "/v1/chat/completions",
    "/v1/voice/turn",
    "/v1/session",
    "/v1/session/renew",
    "/v1/pair/enroll",
    "/v1/device/rotate",
    "/v1/wear/telemetry",
    "/call",
];

/// Verificador de autorização e política de isolamento.
#[derive(Debug, Clone)]
pub struct AccessPolicy {
    allowed_routes: HashSet<String>,
}

impl Default for AccessPolicy {
    fn default() -> Self {
        let mut routes = HashSet::new();
        for &r in ALLOWED_MARK_ROUTES {
            routes.insert(r.to_string());
        }
        Self {
            allowed_routes: routes,
        }
    }
}

impl AccessPolicy {
    /// Normaliza o caminho HTTP e impede ataques de path traversal.
    pub fn normalize_path<'a>(&self, path: &'a str) -> Option<&'a str> {
        if !path.starts_with('/') || path.contains('\0') {
            return None;
        }
        for segment in path.split('?').next().unwrap_or(path).split('/') {
            if segment == ".." {
                return None;
            }
        }
        let clean = path.split('?').next().unwrap_or(path).trim_end_matches('/');
        if clean.is_empty() {
            Some("/")
        } else {
            Some(clean)
        }
    }

    /// Retorna a categoria da rota (ReadOnly vs Mutate), ou None se não autorizada.
    pub fn get_route_category(&self, path: &str) -> Option<RouteCategory> {
        let clean = self.normalize_path(path)?;
        if READONLY_ROUTES.contains(&clean) {
            Some(RouteCategory::ReadOnly)
        } else if MUTATE_ROUTES.contains(&clean) {
            Some(RouteCategory::Mutate)
        } else {
            None
        }
    }

    /// Valida se uma rota pertence exclusivamente à allowlist do MARK e não possui mutação em ReadOnly.
    pub fn is_route_allowed(&self, path: &str) -> bool {
        let clean = match self.normalize_path(path) {
            Some(c) => c,
            None => return false,
        };
        if !self.allowed_routes.contains(clean) {
            return false;
        }
        // Se a rota for de leitura e contiver query string com intenção de mutação, bloqueia
        if let Some(RouteCategory::ReadOnly) = self.get_route_category(path) {
            if let Some(query) = path.split('?').nth(1) {
                let q_lower = query.to_lowercase();
                if q_lower.contains("mutate")
                    || q_lower.contains("action=delete")
                    || q_lower.contains("write")
                    || q_lower.contains("exec")
                    || q_lower.contains("drop")
                {
                    return false;
                }
            }
        }
        true
    }

    /// Garante que apenas métodos HTTP seguros/necessários sejam transportados.
    pub fn is_method_allowed(&self, method: &str) -> bool {
        matches!(method.to_uppercase().as_str(), "GET" | "POST")
    }

    /// Valida se a requisição é permitida de acordo com método, rota e corpo.
    pub fn is_request_allowed(&self, method: &str, path: &str, has_mutation_body: bool) -> bool {
        if !self.is_route_allowed(path) || !self.is_method_allowed(method) {
            return false;
        }
        let category = match self.get_route_category(path) {
            Some(cat) => cat,
            None => return false,
        };
        match category {
            RouteCategory::ReadOnly => method.eq_ignore_ascii_case("GET") && !has_mutation_body,
            RouteCategory::Mutate => method.eq_ignore_ascii_case("POST"),
        }
    }
}

/// Gerenciador de proteção anti-replay e controle de sequência.
#[derive(Debug, Clone)]
pub struct ReplayFilter {
    seen_nonces: HashSet<String>,
    highest_seq: u64,
    sliding_window: u64, // bits de janela
}

impl Default for ReplayFilter {
    fn default() -> Self {
        Self {
            seen_nonces: HashSet::with_capacity(MAX_NONCE_TRACKING),
            highest_seq: 0,
            sliding_window: 0,
        }
    }
}

impl ReplayFilter {
    /// Valida timestamp contra deriva de relógio (clock skew).
    pub fn check_timestamp(timestamp_secs: u64) -> bool {
        let now = match SystemTime::now().duration_since(UNIX_EPOCH) {
            Ok(d) => d.as_secs(),
            Err(_) => return false,
        };
        if timestamp_secs > now + CLOCK_SKEW_SECONDS {
            return false;
        }
        if now > timestamp_secs && now - timestamp_secs > CLOCK_SKEW_SECONDS {
            return false;
        }
        true
    }

    /// Registra e valida se um nonce já foi utilizado (anti-replay).
    pub fn register_nonce(&mut self, nonce: &str) -> bool {
        if nonce.is_empty() || nonce.len() > 128 {
            return false;
        }
        if self.seen_nonces.contains(nonce) {
            return false;
        }
        if self.seen_nonces.len() >= MAX_NONCE_TRACKING {
            self.seen_nonces.clear();
        }
        self.seen_nonces.insert(nonce.to_string());
        true
    }

    /// Verifica se o número de sequência é válido e não é replay, sem comitar.
    pub fn check_sequence(&self, seq: u64) -> bool {
        if seq == 0 {
            return false;
        }
        if seq > self.highest_seq {
            return true;
        }
        let diff = self.highest_seq - seq;
        if diff >= 64 {
            return false;
        }
        let mask = 1u64 << diff;
        (self.sliding_window & mask) == 0
    }

    /// Comita o número de sequência após autenticação criptográfica bem-sucedida.
    pub fn commit_sequence(&mut self, seq: u64) {
        if seq > self.highest_seq {
            let diff = seq - self.highest_seq;
            if diff < 64 {
                self.sliding_window <<= diff;
                self.sliding_window |= 1;
            } else {
                self.sliding_window = 1;
            }
            self.highest_seq = seq;
        } else {
            let diff = self.highest_seq - seq;
            if diff < 64 {
                self.sliding_window |= 1u64 << diff;
            }
        }
    }

    /// Valida o número de sequência monotonicamente crescente com janela deslizante de 64 pacotes.
    pub fn validate_sequence(&mut self, seq: u64) -> bool {
        if !self.check_sequence(seq) {
            return false;
        }
        self.commit_sequence(seq);
        true
    }
}

/// Constrói mensagem canônica de handshake de pareamento MARK-PAIR-V1.
pub fn canonical_pair_message(code: &str) -> Vec<u8> {
    format!("MARK-PAIR-V1\n{}", code).into_bytes()
}

/// Constrói mensagem canônica de requisição MARK-MOBILE-V1.
pub fn canonical_v1_message(
    method: &str,
    path: &str,
    body_hash: &str,
    timestamp: &str,
    nonce: &str,
) -> Vec<u8> {
    format!(
        "MARK-MOBILE-V1\n{}\n{}\n{}\n{}\n{}",
        method, path, body_hash, timestamp, nonce
    )
    .into_bytes()
}

/// Constrói mensagem canônica de requisição MARK-MOBILE-V2.
pub fn canonical_v2_message(
    method: &str,
    path: &str,
    body_hash: &str,
    timestamp: &str,
    nonce: &str,
    request_id: &str,
    session_hash: &str,
) -> Vec<u8> {
    format!(
        "MARK-MOBILE-V2\n{}\n{}\n{}\n{}\n{}\n{}\n{}",
        method, path, body_hash, timestamp, nonce, request_id, session_hash
    )
    .into_bytes()
}

/// Payload JSON do HandshakeInit transmitido pelo cliente ao conectar.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct HandshakeInitPayload {
    pub device_id: String,
    pub ephemeral_pk_der_b64: String,
    pub nonce_b64: String,
    pub timestamp: u64,
    pub signature_b64: String,
}

/// Payload JSON do HandshakeResponse transmitido pelo servidor em resposta.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct HandshakeResponsePayload {
    pub server_id: String,
    pub ephemeral_pk_der_b64: String,
    pub nonce_b64: String,
    pub timestamp: u64,
    pub status: String,
    #[serde(default)]
    pub signature_b64: String,
    #[serde(default)]
    pub resumption_token: Option<String>,
}

/// Payload de solicitação de retomada de sessão (Failover sem re-pareamento).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SessionResumePayload {
    pub device_id: String,
    pub resumption_token: String,
    pub timestamp: u64,
}

/// Payload de confirmação de retomada de sessão pelo servidor.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SessionResumeOkPayload {
    pub status: String,
    pub session_id: String,
    pub timestamp: u64,
}

/// Deriva token criptográfico de retomada de sessão (Session Resumption Token)
/// amarrado ao segredo compartilhado e ao hash do transcript.
pub fn derive_resumption_token(shared_secret: &[u8], transcript_hash: &[u8]) -> String {
    use hkdf::Hkdf;
    let hk = Hkdf::<Sha256>::new(Some(b"mark-link-resumption-salt-v1"), shared_secret);
    let mut okm = [0u8; 32];
    hk.expand(transcript_hash, &mut okm)
        .expect("HKDF expand de resumption token falhou");
    URL_SAFE_NO_PAD.encode(okm)
}

/// Mensagem canônica assinada no HandshakeInit.
pub fn canonical_handshake_init(
    device_id: &str,
    ephemeral_pk_der_b64: &str,
    nonce_b64: &str,
    timestamp: u64,
) -> Vec<u8> {
    format!(
        "MARK-LINK-AUTH-INIT-V1\n{}\n{}\n{}\n{}",
        device_id, ephemeral_pk_der_b64, nonce_b64, timestamp
    )
    .into_bytes()
}

/// Mensagem canônica assinada no HandshakeResponse com vinculação de init_hash e parâmetros.
pub fn canonical_handshake_response(
    server_id: &str,
    ephemeral_pk_der_b64: &str,
    client_nonce_b64: &str,
    server_nonce_b64: &str,
    timestamp: u64,
    init_hash_b64: &str,
) -> Vec<u8> {
    if init_hash_b64.is_empty() {
        format!(
            "MARK-LINK-AUTH-RESP-V1\n{}\n{}\n{}\n{}\n{}",
            server_id, ephemeral_pk_der_b64, client_nonce_b64, server_nonce_b64, timestamp
        )
        .into_bytes()
    } else {
        format!(
            "MARK-LINK-AUTH-RESP-V1\n{}\n{}\n{}\n{}\n{}\n{}",
            server_id,
            ephemeral_pk_der_b64,
            client_nonce_b64,
            server_nonce_b64,
            timestamp,
            init_hash_b64
        )
        .into_bytes()
    }
}

/// Calcula o hash SHA-256 do transcript completo (Init + Response) para Session Binding.
pub fn compute_transcript_hash(canonical_init: &[u8], canonical_resp: &[u8]) -> [u8; 32] {
    let mut hasher = Sha256::new();
    hasher.update(canonical_init);
    hasher.update(b"\n");
    hasher.update(canonical_resp);
    let digest = hasher.finalize();
    let mut out = [0u8; 32];
    out.copy_from_slice(&digest);
    out
}

/// Valida assinatura ECDSA P-256 usando chave pública DER (SPKI).
pub fn verify_p256_der_signature(
    public_der: &[u8],
    message: &[u8],
    signature_bytes: &[u8],
) -> bool {
    use p256::ecdsa::signature::Verifier;
    use p256::ecdsa::{Signature, VerifyingKey};
    use p256::pkcs8::DecodePublicKey;

    let verifying_key = match VerifyingKey::from_public_key_der(public_der) {
        Ok(vk) => vk,
        Err(_) => return false,
    };
    let sig = if let Ok(s) = Signature::from_der(signature_bytes) {
        s
    } else if let Ok(s) = Signature::from_slice(signature_bytes) {
        s
    } else {
        return false;
    };
    verifying_key.verify(message, &sig).is_ok()
}

/// Erros de identidade e segurança de dispositivos.
#[derive(Debug, PartialEq, Eq)]
pub enum IdentityError {
    SilentReplacementBlocked,
    InvalidDeviceId,
    DeviceRevoked,
    DeviceNotFound,
    InvalidProof,
}

/// Erros no desafio de pareamento via QR Code.
#[derive(Debug, PartialEq, Eq)]
pub enum PairingError {
    Expired,
    AlreadyUsed,
    CodeMismatch,
}

/// Erros na validação de algoritmos criptográficos.
#[derive(Debug, PartialEq, Eq)]
pub enum CryptoSuiteError {
    UnsupportedAlgorithm,
    InvalidKeyLength,
    InvalidEncoding,
}

/// Desafio efêmero de pareamento de uso único via QR Code.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PairingChallenge {
    pub code: String,
    pub pin: String,
    pub expires_at: u64,
    pub used: bool,
}

impl PairingChallenge {
    pub fn new(pin: &str, validity_seconds: u64, now: u64) -> Self {
        let mut code_bytes = [0u8; 18];
        rand::Rng::fill(&mut rand::thread_rng(), &mut code_bytes[..]);
        let code = URL_SAFE_NO_PAD.encode(code_bytes);
        Self {
            code,
            pin: pin.to_string(),
            expires_at: now + validity_seconds,
            used: false,
        }
    }

    pub fn validate_and_consume(&mut self, code: &str, now: u64) -> Result<(), PairingError> {
        if self.used {
            return Err(PairingError::AlreadyUsed);
        }
        if now > self.expires_at {
            return Err(PairingError::Expired);
        }
        if self.code != code {
            return Err(PairingError::CodeMismatch);
        }
        self.used = true;
        Ok(())
    }
}

/// Validador estrito de suíte de algoritmos para proteção contra ataques de Downgrade.
pub struct CryptoSuiteValidator;

impl CryptoSuiteValidator {
    /// Validação estrita: exige SubjectPublicKeyInfo com curva NIST P-256 (secp256r1 / 1.2.840.10045.3.1.7).
    /// Rejeita categoricamente chaves RSA, Ed25519 ou algoritmos degradados.
    pub fn validate_p256_spki_der(public_der: &[u8]) -> Result<(), CryptoSuiteError> {
        if public_der.is_empty() {
            return Err(CryptoSuiteError::InvalidEncoding);
        }
        // Exige conformidade absoluta com EC P-256 (SPKI)
        match p256::PublicKey::from_public_key_der(public_der) {
            Ok(_) => Ok(()),
            Err(_) => Err(CryptoSuiteError::UnsupportedAlgorithm),
        }
    }
}

/// Registro de dispositivo persistente/autoritativo.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DeviceRecord {
    pub id: DeviceId,
    pub name: String,
    pub public_der: Vec<u8>,
    pub status: DeviceStatus,
    pub added_at: u64,
}

/// Armazenamento e gerenciador autoritativo de dispositivos no MARK Link.
#[derive(Debug, Clone, Default)]
pub struct DeviceStore {
    devices: std::collections::HashMap<DeviceId, DeviceRecord>,
    permanently_revoked: HashSet<DeviceId>,
}

impl DeviceStore {
    pub fn new() -> Self {
        Self::default()
    }

    /// Registra um dispositivo com verificação de integridade e bloqueio de substituição silenciosa.
    pub fn register_device(&mut self, record: DeviceRecord) -> Result<(), IdentityError> {
        // 1. Valida se a chave pública está no algoritmo homologado EC P-256
        if CryptoSuiteValidator::validate_p256_spki_der(&record.public_der).is_err() {
            return Err(IdentityError::InvalidProof);
        }

        // 3. Prevenção de Substituição Silenciosa: se já existe para este ID, bloqueia substituição com chave diferente
        if let Some(existing) = self.devices.get(&record.id) {
            if existing.public_der != record.public_der {
                return Err(IdentityError::SilentReplacementBlocked);
            }
            if existing.status == DeviceStatus::Authorized
                && record.status != DeviceStatus::Authorized
            {
                return Err(IdentityError::SilentReplacementBlocked);
            }
        }

        // 4. Valida vinculação criptográfica do ID com o SHA-256 da chave pública
        let expected_id = DeviceId::from_public_der(&record.public_der);
        if expected_id != record.id {
            return Err(IdentityError::InvalidDeviceId);
        }

        self.devices.insert(record.id.clone(), record);
        Ok(())
    }

    pub fn get_status(&self, id: &DeviceId) -> DeviceStatus {
        if self.permanently_revoked.contains(id) {
            return DeviceStatus::Revoked;
        }
        self.devices
            .get(id)
            .map(|d| d.status)
            .unwrap_or(DeviceStatus::Unknown)
    }

    pub fn is_authorized(&self, id: &DeviceId) -> bool {
        self.get_status(id) == DeviceStatus::Authorized
    }

    pub fn revoke_device(&mut self, id: &DeviceId, permanent: bool) -> bool {
        if permanent {
            self.permanently_revoked.insert(id.clone());
        }
        if let Some(dev) = self.devices.get_mut(id) {
            dev.status = DeviceStatus::Revoked;
            true
        } else {
            false
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_device_id_derivation() {
        let dummy_der = b"dummy-public-key-der-bytes-for-p256";
        let dev_id = DeviceId::from_public_der(dummy_der);
        assert!(dev_id.is_valid());
        assert_eq!(dev_id.as_str().len(), 43);
    }

    #[test]
    fn test_access_policy_allowlist() {
        let policy = AccessPolicy::default();
        assert!(policy.is_route_allowed("/v1/chat/completions"));
        assert!(policy.is_route_allowed("/v1/voice/turn"));
        assert!(policy.is_route_allowed("/health"));
        assert!(policy.is_route_allowed("/v1/session"));

        // Bloqueio de rotas arbitrárias
        assert!(!policy.is_route_allowed("/admin"));
        assert!(!policy.is_route_allowed("/etc/passwd"));
        assert!(!policy.is_route_allowed("/api/arbitrary"));
        assert!(!policy.is_route_allowed("/metrics"));

        assert!(policy.is_method_allowed("GET"));
        assert!(policy.is_method_allowed("POST"));
        assert!(!policy.is_method_allowed("DELETE"));
        assert!(!policy.is_method_allowed("PUT"));
    }

    #[test]
    fn test_replay_filter_sequence_and_nonce() {
        let mut filter = ReplayFilter::default();

        // Nonce de uso único
        assert!(filter.register_nonce("nonce-1"));
        assert!(!filter.register_nonce("nonce-1")); // Replay rejeitado
        assert!(filter.register_nonce("nonce-2"));

        // Janela deslizante de sequências
        assert!(filter.validate_sequence(1));
        assert!(filter.validate_sequence(2));
        assert!(!filter.validate_sequence(2)); // Replay imediato
        assert!(filter.validate_sequence(5)); // Salto à frente
        assert!(filter.validate_sequence(3)); // Fora de ordem, mas dentro da janela
        assert!(filter.validate_sequence(4));
        assert!(!filter.validate_sequence(3)); // Já visto
        assert!(!filter.validate_sequence(1)); // Fora da janela se muito antigo
    }

    #[test]
    fn test_handshake_canonical_and_signature_verification() {
        use p256::ecdsa::signature::Signer;
        use p256::ecdsa::SigningKey;
        use p256::pkcs8::EncodePublicKey;

        let signing_key = SigningKey::random(&mut rand::rngs::OsRng);
        let verifying_key = signing_key.verifying_key();
        let public_der = verifying_key.to_public_key_der().unwrap();

        let device_id = DeviceId::from_public_der(public_der.as_bytes());
        let eph_pub = "dummy_eph_pub_b64";
        let nonce = "dummy_nonce_b64";
        let timestamp = 1700000000;

        let canonical = canonical_handshake_init(device_id.as_str(), eph_pub, nonce, timestamp);
        let sig: p256::ecdsa::Signature = signing_key.sign(&canonical);

        assert!(verify_p256_der_signature(
            public_der.as_bytes(),
            &canonical,
            sig.to_der().as_bytes()
        ));

        // Assinatura inválida para mensagem adulterada
        assert!(!verify_p256_der_signature(
            public_der.as_bytes(),
            b"mensagem_adulterada",
            sig.to_der().as_bytes()
        ));
    }

    // =========================================================================
    // VETORES OBRIGATÓRIOS DO MARCO A (FASE RED)
    // =========================================================================

    #[test]
    fn test_mitm_ephemeral_key_substitution_rejected_by_transcript_signature() {
        use p256::ecdsa::signature::Signer;
        use p256::ecdsa::SigningKey;
        use p256::pkcs8::EncodePublicKey;

        let client_key = SigningKey::random(&mut rand::rngs::OsRng);
        let client_pub_der = client_key.verifying_key().to_public_key_der().unwrap();
        let dev_id = DeviceId::from_public_der(client_pub_der.as_bytes());

        let legit_eph_b64 = "legitimate_eph_key_der_b64";
        let attacker_eph_b64 = "attacker_injected_eph_key_der_b64";
        let nonce = "nonce12345";
        let ts = 1700000000;

        // Cliente assina com a chave efêmera legítima
        let canonical_legit = canonical_handshake_init(dev_id.as_str(), legit_eph_b64, nonce, ts);
        let sig: p256::ecdsa::Signature = client_key.sign(&canonical_legit);

        // MITM substitui a chave efêmera na mensagem que chega ao servidor
        let canonical_tampered =
            canonical_handshake_init(dev_id.as_str(), attacker_eph_b64, nonce, ts);

        // A verificação pelo servidor DEVE falhar
        assert!(!verify_p256_der_signature(
            client_pub_der.as_bytes(),
            &canonical_tampered,
            sig.to_der().as_bytes()
        ));
    }

    #[test]
    fn test_replay_authentication_message_blocked() {
        let mut filter = ReplayFilter::default();
        let nonce = "replay_target_nonce_999";

        // Primeiro registro é válido
        assert!(filter.register_nonce(nonce));
        // Replay imediato DEVE ser bloqueado
        assert!(!filter.register_nonce(nonce));

        // Timestamp expirado (> 60s)
        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_secs();
        assert!(!ReplayFilter::check_timestamp(now - 120));
        assert!(!ReplayFilter::check_timestamp(now + 120));
    }

    #[test]
    fn test_algorithm_downgrade_rejected() {
        // Tentativa de usar uma chave RSA simulada (bytes não-P256)
        let fake_rsa_spki = vec![
            0x30, 0x82, 0x01, 0x22, 0x30, 0x0d, 0x06, 0x09, 0x2a, 0x86, 0x48, 0x86, 0xf7, 0x0d,
            0x01, 0x01, 0x01,
        ];
        let result = CryptoSuiteValidator::validate_p256_spki_der(&fake_rsa_spki);
        // Na Fase RED, o stub aceitou a chave RSA: o assert falhará!
        assert_eq!(result, Err(CryptoSuiteError::UnsupportedAlgorithm));
    }

    #[test]
    fn test_silent_identity_replacement_blocked() {
        use p256::ecdsa::SigningKey;
        use p256::pkcs8::EncodePublicKey;

        let mut store = DeviceStore::new();

        let key_orig = SigningKey::random(&mut rand::rngs::OsRng);
        let pub_der_orig = key_orig
            .verifying_key()
            .to_public_key_der()
            .unwrap()
            .as_bytes()
            .to_vec();
        let dev_id = DeviceId::from_public_der(&pub_der_orig);

        let original_record = DeviceRecord {
            id: dev_id.clone(),
            name: "Dispositivo Legítimo".to_string(),
            public_der: pub_der_orig,
            status: DeviceStatus::Authorized,
            added_at: 1000,
        };

        // Registra dispositivo legítimo inicial
        assert!(store.register_device(original_record).is_ok());

        // Atacante tenta substituir silenciosamente a chave pública do mesmo dispositivo
        let key_attack = SigningKey::random(&mut rand::rngs::OsRng);
        let pub_der_attack = key_attack
            .verifying_key()
            .to_public_key_der()
            .unwrap()
            .as_bytes()
            .to_vec();

        let attack_record = DeviceRecord {
            id: dev_id.clone(), // Mesmo ID do legítimo!
            name: "Dispositivo Falso Sobrescrito".to_string(),
            public_der: pub_der_attack, // Chave pública divergente!
            status: DeviceStatus::Authorized,
            added_at: 2000,
        };

        let result = store.register_device(attack_record);
        assert_eq!(result, Err(IdentityError::SilentReplacementBlocked));
    }

    #[test]
    fn test_pairing_challenge_expiration_and_single_use() {
        let mut challenge = PairingChallenge::new("abc123pin", 120, 1000);
        assert!(!challenge.used);

        // Uso com código incorreto falha
        assert_eq!(
            challenge.validate_and_consume("codigo_errado", 1010),
            Err(PairingError::CodeMismatch)
        );

        // Uso após expiração falha
        assert_eq!(
            challenge.validate_and_consume(&challenge.code.clone(), 1200),
            Err(PairingError::Expired)
        );

        // Uso correto consome com sucesso
        let valid_code = challenge.code.clone();
        assert!(challenge.validate_and_consume(&valid_code, 1050).is_ok());
        assert!(challenge.used);

        // Segundo uso falha (uso único)
        assert_eq!(
            challenge.validate_and_consume(&valid_code, 1055),
            Err(PairingError::AlreadyUsed)
        );
    }

    #[test]
    fn test_cross_language_cryptographic_golden_vectors() {
        let dev_id = "test-device-id-golden-vector-0123456789abc";
        let eph = "eph-pk-golden-123";
        let nonce = "client-nonce-golden-456";
        let ts = 1700000000;

        let canonical_init = canonical_handshake_init(dev_id, eph, nonce, ts);
        let init_hash = Sha256::digest(&canonical_init);
        let init_hash_b64 = URL_SAFE_NO_PAD.encode(init_hash);
        assert_eq!(init_hash_b64, "NxHvtBn_uTbjLJIRNlnMm1ZIpyQFOfZzxTKHi9W_qh0");

        let srv_eph = "server-eph-golden-789";
        let srv_nonce = "server-nonce-golden-999";
        let canonical_resp = canonical_handshake_response(
            "mark-pc-core",
            srv_eph,
            nonce,
            srv_nonce,
            ts,
            &init_hash_b64,
        );

        let transcript_hash = compute_transcript_hash(&canonical_init, &canonical_resp);
        let transcript_hash_b64 = URL_SAFE_NO_PAD.encode(transcript_hash);
        assert_eq!(
            transcript_hash_b64,
            "Y4g5Z3Q11gzZiOKNV3aoH_OM9o7b_DDBondKVsU79Hs"
        );
    }

    #[test]
    fn test_api_allowlist_enforces_readonly_vs_mutate_separation() {
        let policy = AccessPolicy::default();

        // 1. Endpoints de leitura (ReadOnly) NÃO devem permitir POST nem corpo/mutação
        assert!(
            !policy.is_request_allowed("POST", "/health", false),
            "FALHA RED: POST não deve ser permitido em endpoint ReadOnly /health!"
        );
        assert!(
            !policy.is_request_allowed("GET", "/health", true),
            "FALHA RED: Requisição com body não deve ser permitida em ReadOnly!"
        );
        assert!(
            !policy.is_request_allowed("GET", "/v1/history?mutate=true", false),
            "FALHA RED: Query string com mutação não deve ser permitida em ReadOnly!"
        );

        // 2. Endpoints de mutação exigem POST
        assert!(
            !policy.is_request_allowed("GET", "/v1/chat/completions", false),
            "FALHA RED: GET não deve ser permitido em endpoint de mutação /v1/chat/completions!"
        );

        // 3. Caminhos de sistema e traversal devem ser sumariamente bloqueados
        assert!(!policy.is_route_allowed("/admin"));
        assert!(!policy.is_route_allowed("/etc/shadow"));
        assert!(!policy.is_route_allowed("../../etc/passwd"));
        assert!(!policy.is_route_allowed("/v1/history/../../admin"));
    }
}

//! Daemon de Conectividade do MARK Link em Rust.
//!
//! Executa listener TCP local autenticado, gerencia sessões criptográficas com
//! chaves efêmeras e separação direcional (TX/RX), e impõe a política de acesso (AccessPolicy).

use crate::identity::{
    canonical_handshake_init, canonical_handshake_response, compute_transcript_hash,
    derive_resumption_token, verify_p256_der_signature, AccessPolicy, HandshakeInitPayload,
    HandshakeResponsePayload, ReplayFilter, RouteCategory, SessionResumeOkPayload,
    SessionResumePayload,
};
use crate::telemetry::TelemetryCollector;
use crate::transport::{
    DirectionalChannel, IdempotencyCache, PacketHeader, PacketType, TransportError, HEADER_SIZE,
    MAX_PAYLOAD_SIZE,
};
use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine as _};
use p256::ecdh::EphemeralSecret;
use p256::ecdsa::signature::Signer;
use p256::ecdsa::SigningKey;
use p256::pkcs8::{DecodePublicKey, EncodePublicKey};
use p256::PublicKey;
use rand::rngs::OsRng;
use sha2::{Digest, Sha256};
use std::collections::HashMap;
use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

pub struct DaemonConfig {
    pub listen_addr: String,
    pub target_api_url: String,
    pub max_connections: usize,
    pub server_signing_key: Option<SigningKey>,
}

impl Default for DaemonConfig {
    fn default() -> Self {
        Self {
            listen_addr: "127.0.0.1:8766".to_string(),
            target_api_url: "http://127.0.0.1:8765".to_string(),
            max_connections: 16,
            server_signing_key: None,
        }
    }
}

/// Registro de sessão autenticada ativa para failover transparente (sem re-pareamento).
#[derive(Clone)]
pub struct ActiveSession {
    pub session_id: String,
    pub device_id: String,
    pub resumption_token: String,
    pub channel: DirectionalChannel,
    pub cancel_signal: Arc<AtomicBool>,
    pub stream_handle: Option<Arc<TcpStream>>,
}

pub struct DaemonServer {
    pub config: DaemonConfig,
    local_addr: String,
    listener: Option<TcpListener>,
    running: Arc<AtomicBool>,
    policy: AccessPolicy,
    telemetry: Arc<TelemetryCollector>,
    authorized_keys: Arc<Mutex<HashMap<String, Vec<u8>>>>, // device_id -> public_der
    revoked_devices: Arc<Mutex<std::collections::HashSet<String>>>, // dispositivos revogados
    pub active_sessions: Arc<Mutex<HashMap<String, ActiveSession>>>, // device_id -> session
    pub idempotency_cache: Arc<Mutex<IdempotencyCache>>,
    server_signing_key: Arc<SigningKey>,
}

impl DaemonServer {
    pub fn bind(config: DaemonConfig) -> Result<Self, std::io::Error> {
        let listener = TcpListener::bind(&config.listen_addr)?;
        let local_addr = listener.local_addr()?.to_string();
        listener.set_nonblocking(true)?;

        let server_signing_key = match config.server_signing_key.clone() {
            Some(k) => Arc::new(k),
            None => Arc::new(SigningKey::random(&mut OsRng)),
        };

        Ok(Self {
            config,
            local_addr,
            listener: Some(listener),
            running: Arc::new(AtomicBool::new(false)),
            policy: AccessPolicy::default(),
            telemetry: Arc::new(TelemetryCollector::new()),
            authorized_keys: Arc::new(Mutex::new(HashMap::new())),
            revoked_devices: Arc::new(Mutex::new(std::collections::HashSet::new())),
            active_sessions: Arc::new(Mutex::new(HashMap::new())),
            idempotency_cache: Arc::new(Mutex::new(IdempotencyCache::new(1024, 600))),
            server_signing_key,
        })
    }

    pub fn local_addr(&self) -> &str {
        &self.local_addr
    }

    pub fn server_public_der(&self) -> Vec<u8> {
        self.server_signing_key
            .verifying_key()
            .to_public_key_der()
            .unwrap()
            .as_bytes()
            .to_vec()
    }

    pub fn register_authorized_device(&self, device_id: &str, public_der: &[u8]) {
        let mut map = self
            .authorized_keys
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        map.insert(device_id.to_string(), public_der.to_vec());
    }

    /// Revoga imediatamente um dispositivo autorizado, derrubando a sessão ativa em memória e o socket.
    pub fn revoke_device(&self, device_id: &str) -> bool {
        self.revoked_devices
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .insert(device_id.to_string());
        let _ = self
            .authorized_keys
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .remove(device_id);

        let mut sessions = self
            .active_sessions
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        if let Some(session) = sessions.remove(device_id) {
            session.cancel_signal.store(false, Ordering::SeqCst);
            if let Some(ref sock) = session.stream_handle {
                let _ = sock.shutdown(std::net::Shutdown::Both);
            }
            self.telemetry.record_drop();
            true
        } else {
            true
        }
    }

    pub fn telemetry(&self) -> Arc<TelemetryCollector> {
        Arc::clone(&self.telemetry)
    }

    pub fn start(&mut self) -> Result<(), std::io::Error> {
        let listener = self.listener.take().ok_or_else(|| {
            std::io::Error::new(
                std::io::ErrorKind::Other,
                "DaemonServer já iniciado ou listener indisponível",
            )
        })?;
        self.running.store(true, Ordering::SeqCst);

        let running = Arc::clone(&self.running);
        let policy = self.policy.clone();
        let telemetry = Arc::clone(&self.telemetry);
        let authorized_keys = Arc::clone(&self.authorized_keys);
        let revoked_devices = Arc::clone(&self.revoked_devices);
        let active_sessions = Arc::clone(&self.active_sessions);
        let idempotency_cache = Arc::clone(&self.idempotency_cache);
        let server_signing_key = Arc::clone(&self.server_signing_key);

        thread::spawn(move || {
            while running.load(Ordering::SeqCst) {
                match listener.accept() {
                    Ok((stream, _addr)) => {
                        let pol = policy.clone();
                        let telem = Arc::clone(&telemetry);
                        let auth_keys = Arc::clone(&authorized_keys);
                        let rev_devs = Arc::clone(&revoked_devices);
                        let act_sessions = Arc::clone(&active_sessions);
                        let idemp = Arc::clone(&idempotency_cache);
                        let srv_key = Arc::clone(&server_signing_key);
                        let srv_running = Arc::clone(&running);

                        thread::spawn(move || {
                            Self::handle_connection(
                                stream,
                                pol,
                                telem,
                                auth_keys,
                                rev_devs,
                                act_sessions,
                                srv_key,
                                srv_running,
                                idemp,
                            );
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

    fn read_frame<R: Read>(reader: &mut R) -> Result<(PacketHeader, Vec<u8>), TransportError> {
        let mut hdr_bytes = [0u8; HEADER_SIZE];
        reader
            .read_exact(&mut hdr_bytes)
            .map_err(|_| TransportError::BufferUnderflow)?;
        let header = PacketHeader::decode(&hdr_bytes)?;
        if header.length as usize > MAX_PAYLOAD_SIZE {
            return Err(TransportError::PayloadTooLarge(header.length as usize));
        }
        let mut payload = vec![0u8; header.length as usize];
        reader
            .read_exact(&mut payload)
            .map_err(|_| TransportError::BufferUnderflow)?;

        let mut full_frame = Vec::with_capacity(HEADER_SIZE + payload.len());
        full_frame.extend_from_slice(&hdr_bytes);
        full_frame.extend_from_slice(&payload);
        Ok((header, full_frame))
    }

    fn handle_connection(
        mut stream: TcpStream,
        policy: AccessPolicy,
        telemetry: Arc<TelemetryCollector>,
        authorized_keys: Arc<Mutex<HashMap<String, Vec<u8>>>>,
        revoked_devices: Arc<Mutex<std::collections::HashSet<String>>>,
        active_sessions: Arc<Mutex<HashMap<String, ActiveSession>>>,
        server_signing_key: Arc<SigningKey>,
        server_running: Arc<AtomicBool>,
        idempotency_cache: Arc<Mutex<IdempotencyCache>>,
    ) {
        let _ = stream.set_read_timeout(Some(Duration::from_secs(30)));
        let _ = stream.set_write_timeout(Some(Duration::from_secs(30)));

        // 1. Lê primeiro frame do socket
        let (hdr, frame_bytes) = match Self::read_frame(&mut stream) {
            Ok(res) => res,
            Err(_) => return,
        };

        let now_sec = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs();

        // CASO A: Retomada de Sessão (Failover rápido sem exigir novo pareamento)
        if hdr.packet_type == PacketType::SessionResume {
            let resume_json = match std::str::from_utf8(&frame_bytes[HEADER_SIZE..]) {
                Ok(s) => s,
                Err(_) => return,
            };
            let resume_payload: SessionResumePayload = match serde_json::from_str(resume_json) {
                Ok(p) => p,
                Err(_) => {
                    telemetry.record_drop();
                    telemetry.record_error("JSON de SessionResumePayload inválido");
                    return;
                }
            };

            if revoked_devices
                .lock()
                .unwrap_or_else(|e| e.into_inner())
                .contains(&resume_payload.device_id)
            {
                telemetry.record_drop();
                telemetry.record_error("Dispositivo revogado tentou retomar sessão");
                return;
            }

            if !ReplayFilter::check_timestamp(resume_payload.timestamp) {
                telemetry.record_drop();
                telemetry.record_error("Timestamp de retomada expirado");
                return;
            }

            let (mut channel, connection_active) = {
                let mut sessions = active_sessions.lock().unwrap_or_else(|e| e.into_inner());
                let session = match sessions.get_mut(&resume_payload.device_id) {
                    Some(s) => s,
                    None => {
                        telemetry.record_drop();
                        telemetry.record_error("Sessão não encontrada para retomada");
                        return;
                    }
                };

                if session.resumption_token != resume_payload.resumption_token {
                    telemetry.record_drop();
                    telemetry.record_error("Token de retomada inválido");
                    return;
                }

                // Anti-split-brain: encerra imediatamente a conexão zumbi anterior
                session.cancel_signal.store(false, Ordering::SeqCst);

                let connection_active = Arc::new(AtomicBool::new(true));
                session.cancel_signal = Arc::clone(&connection_active);
                (session.channel.clone(), connection_active)
            };

            let ok_payload = SessionResumeOkPayload {
                status: "resumed".to_string(),
                session_id: format!(
                    "sess-resumed-{}",
                    &resume_payload.device_id[..8.min(resume_payload.device_id.len())]
                ),
                timestamp: now_sec,
            };
            let ok_json = match serde_json::to_string(&ok_payload) {
                Ok(j) => j,
                Err(_) => return,
            };
            let mut ok_hdr = [0u8; HEADER_SIZE];
            let header = PacketHeader {
                packet_type: PacketType::SessionResumeOk,
                flags: 0,
                sequence: 0,
                length: ok_json.len() as u32,
            };
            header.encode(&mut ok_hdr);
            if stream.write_all(&ok_hdr).is_err()
                || stream.write_all(ok_json.as_bytes()).is_err()
                || stream.flush().is_err()
            {
                return;
            }

            telemetry.set_peer(&resume_payload.device_id);
            telemetry.record_reconnect();
            telemetry.record_failover();

            Self::run_session_loop(
                stream,
                &mut channel,
                &policy,
                &telemetry,
                &resume_payload.device_id,
                &server_running,
                &connection_active,
                &idempotency_cache,
            );
            return;
        }

        // CASO B: HandshakeInit normal
        if hdr.packet_type != PacketType::HandshakeInit {
            telemetry.record_drop();
            telemetry.record_error("Primeiro frame recebido não é HandshakeInit nem SessionResume");
            return;
        }

        // O payload do frame inicial vem em texto plano (JSON de HandshakeInitPayload)
        // para troca de chaves efêmeras ECDH e prova de identidade criptográfica.
        let init_json = match std::str::from_utf8(&frame_bytes[HEADER_SIZE..]) {
            Ok(s) => s,
            Err(_) => return,
        };
        let init_payload: HandshakeInitPayload = match serde_json::from_str(init_json) {
            Ok(p) => p,
            Err(_) => {
                telemetry.record_drop();
                telemetry.record_error("JSON de HandshakeInitPayload inválido");
                return;
            }
        };

        // Validação anti-replay do timestamp (|now - timestamp| <= 60s)
        if !ReplayFilter::check_timestamp(init_payload.timestamp) {
            telemetry.record_drop();
            telemetry
                .record_error("Timestamp de handshake expirado ou fora do limite de clock skew");
            return;
        }

        // Se o dispositivo foi revogado, rejeita categoricamente
        if revoked_devices
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .contains(&init_payload.device_id)
        {
            telemetry.record_drop();
            telemetry.record_error(&format!(
                "Dispositivo revogado tentou autenticar: {}",
                init_payload.device_id
            ));
            return;
        }

        // Verificação da chave pública do dispositivo
        let client_pub_der = {
            let map = authorized_keys.lock().unwrap_or_else(|e| e.into_inner());
            map.get(&init_payload.device_id).cloned()
        };
        let client_pub_der = match client_pub_der {
            Some(der) => der,
            None => {
                telemetry.record_drop();
                telemetry.record_error(&format!(
                    "Dispositivo não autorizado: {}",
                    init_payload.device_id
                ));
                return;
            }
        };

        // Validação contra downgrade de algoritmos (exige estritamente EC P-256)
        if crate::identity::CryptoSuiteValidator::validate_p256_spki_der(&client_pub_der).is_err() {
            telemetry.record_drop();
            telemetry
                .record_error("Algoritmo de chave pública não suportado ou vulnerável a downgrade");
            return;
        }

        // Verificação da assinatura do HandshakeInit
        let canonical_init = canonical_handshake_init(
            &init_payload.device_id,
            &init_payload.ephemeral_pk_der_b64,
            &init_payload.nonce_b64,
            init_payload.timestamp,
        );
        let sig_bytes = match URL_SAFE_NO_PAD.decode(&init_payload.signature_b64) {
            Ok(b) => b,
            Err(_) => return,
        };
        if !verify_p256_der_signature(&client_pub_der, &canonical_init, &sig_bytes) {
            telemetry.record_drop();
            telemetry.record_error("Assinatura de HandshakeInit inválida");
            return;
        }

        let init_hash = Sha256::digest(&canonical_init);
        let init_hash_b64 = URL_SAFE_NO_PAD.encode(init_hash);

        // Carrega a chave efêmera do cliente
        let client_eph_der = match URL_SAFE_NO_PAD.decode(&init_payload.ephemeral_pk_der_b64) {
            Ok(b) => b,
            Err(_) => return,
        };
        let client_eph_pub = match PublicKey::from_public_key_der(&client_eph_der) {
            Ok(pk) => pk,
            Err(_) => return,
        };

        // Servidor gera seu par efêmero ECDH P-256
        let server_eph_secret = EphemeralSecret::random(&mut OsRng);
        let server_eph_pub = PublicKey::from(&server_eph_secret);
        let server_eph_pub_der = match server_eph_pub.to_public_key_der() {
            Ok(der) => der,
            Err(_) => return,
        };
        let server_eph_pub_b64 = URL_SAFE_NO_PAD.encode(server_eph_pub_der.as_bytes());

        // Deriva segredo compartilhado ECDH
        let shared_secret = server_eph_secret.diffie_hellman(&client_eph_pub);

        // Gera nonce do servidor
        let mut server_nonce_bytes = [0u8; 32];
        rand::Rng::fill(&mut rand::thread_rng(), &mut server_nonce_bytes[..]);
        let server_nonce_b64 = URL_SAFE_NO_PAD.encode(server_nonce_bytes);

        let client_nonce_bytes = match URL_SAFE_NO_PAD.decode(&init_payload.nonce_b64) {
            Ok(b) => b,
            Err(_) => return,
        };

        // Servidor assina a resposta canônica vinculando parâmetros e hash da mensagem Init
        let canonical_resp = canonical_handshake_response(
            "mark-pc-core",
            &server_eph_pub_b64,
            &init_payload.nonce_b64,
            &server_nonce_b64,
            now_sec,
            &init_hash_b64,
        );
        let server_sig: p256::ecdsa::Signature = server_signing_key.sign(&canonical_resp);
        let server_sig_b64 = URL_SAFE_NO_PAD.encode(server_sig.to_der().as_bytes());

        // Session Binding: injeta transcript completo no HKDF
        let transcript_hash = compute_transcript_hash(&canonical_init, &canonical_resp);

        // Deriva canal bidirecional vinculado ao transcript (is_server = true)
        let mut channel = match DirectionalChannel::derive_session_keys_bound(
            shared_secret.raw_secret_bytes(),
            &client_nonce_bytes,
            &server_nonce_bytes,
            Some(&transcript_hash),
            true,
        ) {
            Ok(c) => c,
            Err(_) => return,
        };

        // Deriva token criptográfico de retomada de sessão para failover
        let resumption_token =
            derive_resumption_token(shared_secret.raw_secret_bytes(), &transcript_hash);
        let session_id = format!("sess-{}", &resumption_token[..12]);

        // Envia resposta de handshake com prova criptográfica e resumption_token
        let resp_payload = HandshakeResponsePayload {
            server_id: "mark-pc-core".to_string(),
            ephemeral_pk_der_b64: server_eph_pub_b64,
            nonce_b64: server_nonce_b64,
            timestamp: now_sec,
            status: "authorized".to_string(),
            signature_b64: server_sig_b64,
            resumption_token: Some(resumption_token.clone()),
        };
        let resp_json = match serde_json::to_string(&resp_payload) {
            Ok(j) => j,
            Err(_) => return,
        };

        let mut resp_hdr = [0u8; HEADER_SIZE];
        let resp_header = PacketHeader {
            packet_type: PacketType::HandshakeResponse,
            flags: 0,
            sequence: 0,
            length: resp_json.len() as u32,
        };
        resp_header.encode(&mut resp_hdr);
        if stream.write_all(&resp_hdr).is_err()
            || stream.write_all(resp_json.as_bytes()).is_err()
            || stream.flush().is_err()
        {
            return;
        }

        telemetry.set_peer(&init_payload.device_id);
        telemetry.set_mode(Some(crate::transport::ConnectionMode::DirectLocal));

        // Armazena sessão ativa para suportar reconexão rápida e prevenir split-brain
        let connection_active = Arc::new(AtomicBool::new(true));
        let stream_handle = stream.try_clone().ok().map(Arc::new);
        {
            let mut sessions = active_sessions.lock().unwrap_or_else(|e| e.into_inner());
            if let Some(old) = sessions.get(&init_payload.device_id) {
                old.cancel_signal.store(false, Ordering::SeqCst);
                if let Some(ref s) = old.stream_handle {
                    let _ = s.shutdown(std::net::Shutdown::Both);
                }
            }
            sessions.insert(
                init_payload.device_id.clone(),
                ActiveSession {
                    session_id,
                    device_id: init_payload.device_id.clone(),
                    resumption_token,
                    channel: channel.clone(),
                    cancel_signal: Arc::clone(&connection_active),
                    stream_handle,
                },
            );
        }

        // 2. Loop de processamento de pacotes protegidos
        Self::run_session_loop(
            stream,
            &mut channel,
            &policy,
            &telemetry,
            &init_payload.device_id,
            &server_running,
            &connection_active,
            &idempotency_cache,
        );
    }

    fn run_session_loop(
        mut stream: TcpStream,
        channel: &mut DirectionalChannel,
        policy: &AccessPolicy,
        telemetry: &Arc<TelemetryCollector>,
        device_id: &str,
        server_running: &Arc<AtomicBool>,
        connection_active: &Arc<AtomicBool>,
        idempotency_cache: &Arc<Mutex<IdempotencyCache>>,
    ) {
        while server_running.load(Ordering::SeqCst) && connection_active.load(Ordering::SeqCst) {
            let (_, frame) = match Self::read_frame(&mut stream) {
                Ok(res) => res,
                Err(_) => break,
            };

            telemetry.record_bytes_received(frame.len() as u64);

            let (header, plaintext) = match channel.unseal_packet(&frame) {
                Ok(res) => res,
                Err(err) => {
                    telemetry.record_drop();
                    telemetry.record_error(&format!("Falha ao decifrar frame: {}", err));
                    break;
                }
            };

            match header.packet_type {
                PacketType::Ping => {
                    if let Ok(pong_frame) = channel.seal_packet(PacketType::Pong, b"pong") {
                        telemetry.record_bytes_sent(pong_frame.len() as u64);
                        let _ = stream.write_all(&pong_frame);
                        let _ = stream.flush();
                    }
                }
                PacketType::Data => {
                    if let Ok(req_val) = serde_json::from_slice::<serde_json::Value>(&plaintext) {
                        let method = req_val
                            .get("method")
                            .and_then(|v| v.as_str())
                            .unwrap_or("GET");
                        let path = req_val
                            .get("path")
                            .and_then(|v| v.as_str())
                            .unwrap_or("/health");

                        let has_body = req_val.get("body").is_some_and(|b| {
                            if let Some(s) = b.as_str() {
                                !s.trim().is_empty()
                            } else if let Some(o) = b.as_object() {
                                !o.is_empty()
                            } else {
                                !b.is_null()
                            }
                        });

                        if !policy.is_request_allowed(method, path, has_body) {
                            telemetry.record_drop();
                            telemetry.record_error(&format!(
                                "Tentativa de acesso a rota/método/mutação proibida: {} {}",
                                method, path
                            ));
                            let err_resp = serde_json::json!({
                                "status": 403,
                                "error": "Acesso negado: rota, método ou mutação não autorizado pelo MARK Link"
                            });
                            let resp_bytes = serde_json::to_vec(&err_resp).unwrap_or_default();
                            if let Ok(resp_frame) =
                                channel.seal_packet(PacketType::Error, &resp_bytes)
                            {
                                let _ = stream.write_all(&resp_frame);
                                let _ = stream.flush();
                            }
                            continue;
                        }

                        // Idempotência: para mutações com request_id, retornar cache se já executado
                        let request_id = req_val
                            .get("request_id")
                            .and_then(|v| v.as_str())
                            .or_else(|| {
                                req_val
                                    .get("headers")
                                    .and_then(|h| {
                                        h.get("x-request-id").or_else(|| h.get("X-Request-ID"))
                                    })
                                    .and_then(|v| v.as_str())
                            });

                        let category = policy.get_route_category(path);
                        let is_mutation = category == Some(RouteCategory::Mutate)
                            || method.eq_ignore_ascii_case("POST");

                        if is_mutation {
                            if let Some(req_id) = request_id {
                                let cached = {
                                    let mut cache =
                                        idempotency_cache.lock().unwrap_or_else(|e| e.into_inner());
                                    cache.get(req_id)
                                };
                                if let Some(cached_resp) = cached {
                                    let mut val: serde_json::Value = serde_json::from_slice(
                                        &cached_resp.body,
                                    )
                                    .unwrap_or_else(|_| {
                                        serde_json::json!({
                                            "status": cached_resp.status,
                                            "mark_link": "ok"
                                        })
                                    });
                                    if let Some(obj) = val.as_object_mut() {
                                        obj.insert(
                                            "idempotent_replay".to_string(),
                                            serde_json::Value::Bool(true),
                                        );
                                    }
                                    let resp_bytes = serde_json::to_vec(&val).unwrap_or_default();
                                    if let Ok(resp_frame) =
                                        channel.seal_packet(PacketType::Data, &resp_bytes)
                                    {
                                        telemetry.record_bytes_sent(resp_frame.len() as u64);
                                        let _ = stream.write_all(&resp_frame);
                                        let _ = stream.flush();
                                    }
                                    continue;
                                }
                            }
                        }

                        let ok_resp = serde_json::json!({
                            "status": 200,
                            "path": path,
                            "mark_link": "ok",
                            "authorized_device": device_id,
                            "idempotent_replay": false
                        });
                        let resp_bytes = serde_json::to_vec(&ok_resp).unwrap_or_default();

                        if is_mutation {
                            if let Some(req_id) = request_id {
                                let mut cache =
                                    idempotency_cache.lock().unwrap_or_else(|e| e.into_inner());
                                cache.put(req_id.to_string(), 200, resp_bytes.clone());
                            }
                        }

                        if let Ok(resp_frame) = channel.seal_packet(PacketType::Data, &resp_bytes) {
                            telemetry.record_bytes_sent(resp_frame.len() as u64);
                            let _ = stream.write_all(&resp_frame);
                            let _ = stream.flush();
                        }
                    }
                }
                PacketType::Close => {
                    break;
                }
                _ => {}
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::identity::DeviceId;
    use p256::ecdsa::signature::Signer;
    use p256::ecdsa::SigningKey;

    #[test]
    fn test_daemon_server_full_handshake_and_isolation() {
        let signing_key = SigningKey::random(&mut OsRng);
        let verifying_key = signing_key.verifying_key();
        let pub_der = verifying_key.to_public_key_der().unwrap();
        let dev_id = DeviceId::from_public_der(pub_der.as_bytes());

        let cfg = DaemonConfig {
            listen_addr: "127.0.0.1:0".to_string(),
            ..Default::default()
        };
        let mut server = DaemonServer::bind(cfg).expect("Bind falhou");
        server.register_authorized_device(dev_id.as_str(), pub_der.as_bytes());
        let addr = server.local_addr().to_string();
        server.start().expect("Start falhou");

        // Cliente conecta via socket TCP
        let mut client_stream = TcpStream::connect(&addr).expect("Connect falhou");

        // 1. Gera par efêmero do cliente
        let client_eph_secret = EphemeralSecret::random(&mut OsRng);
        let client_eph_pub = PublicKey::from(&client_eph_secret);
        let client_eph_der = client_eph_pub.to_public_key_der().unwrap();
        let client_eph_b64 = URL_SAFE_NO_PAD.encode(client_eph_der.as_bytes());

        let mut client_nonce = [0u8; 32];
        rand::Rng::fill(&mut rand::thread_rng(), &mut client_nonce[..]);
        let client_nonce_b64 = URL_SAFE_NO_PAD.encode(client_nonce);
        let timestamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_secs();

        // 2. Assina o HandshakeInit
        let canonical = canonical_handshake_init(
            dev_id.as_str(),
            &client_eph_b64,
            &client_nonce_b64,
            timestamp,
        );
        let sig: p256::ecdsa::Signature = signing_key.sign(&canonical);
        let sig_b64 = URL_SAFE_NO_PAD.encode(sig.to_der().as_bytes());

        let init_payload = HandshakeInitPayload {
            device_id: dev_id.as_str().to_string(),
            ephemeral_pk_der_b64: client_eph_b64,
            nonce_b64: client_nonce_b64.clone(),
            timestamp,
            signature_b64: sig_b64,
        };
        let init_json = serde_json::to_string(&init_payload).unwrap();

        // Envia frame de HandshakeInit
        let mut init_hdr = [0u8; HEADER_SIZE];
        let header = PacketHeader {
            packet_type: PacketType::HandshakeInit,
            flags: 0,
            sequence: 0,
            length: init_json.len() as u32,
        };
        header.encode(&mut init_hdr);
        client_stream.write_all(&init_hdr).unwrap();
        client_stream.write_all(init_json.as_bytes()).unwrap();
        client_stream.flush().unwrap();

        // Recebe HandshakeResponse do servidor
        let (resp_hdr, resp_frame) = DaemonServer::read_frame(&mut client_stream).unwrap();
        assert_eq!(resp_hdr.packet_type, PacketType::HandshakeResponse);
        let resp_payload: HandshakeResponsePayload =
            serde_json::from_slice(&resp_frame[HEADER_SIZE..]).unwrap();
        assert_eq!(resp_payload.status, "authorized");
        assert!(
            !resp_payload.signature_b64.is_empty(),
            "Assinatura do servidor ausente!"
        );

        // Cliente valida assinatura criptográfica do servidor
        let init_hash = Sha256::digest(&canonical);
        let init_hash_b64 = URL_SAFE_NO_PAD.encode(init_hash);
        let canonical_resp = canonical_handshake_response(
            &resp_payload.server_id,
            &resp_payload.ephemeral_pk_der_b64,
            &client_nonce_b64,
            &resp_payload.nonce_b64,
            resp_payload.timestamp,
            &init_hash_b64,
        );
        let sig_bytes = URL_SAFE_NO_PAD.decode(&resp_payload.signature_b64).unwrap();
        assert!(
            verify_p256_der_signature(&server.server_public_der(), &canonical_resp, &sig_bytes),
            "Assinatura do servidor inválida no cliente!"
        );

        // Cliente deriva chaves com a chave efêmera do servidor e Session Binding
        let srv_eph_der = URL_SAFE_NO_PAD
            .decode(&resp_payload.ephemeral_pk_der_b64)
            .unwrap();
        let srv_eph_pub = PublicKey::from_public_key_der(&srv_eph_der).unwrap();
        let shared_secret = client_eph_secret.diffie_hellman(&srv_eph_pub);
        let server_nonce_bytes = URL_SAFE_NO_PAD.decode(&resp_payload.nonce_b64).unwrap();

        let transcript_hash = compute_transcript_hash(&canonical, &canonical_resp);
        let mut client_channel = DirectionalChannel::derive_session_keys_bound(
            shared_secret.raw_secret_bytes(),
            &client_nonce,
            &server_nonce_bytes,
            Some(&transcript_hash),
            false,
        )
        .unwrap();

        // 3. Testa envio de Ping cifrado
        let ping_frame = client_channel
            .seal_packet(PacketType::Ping, b"ping")
            .unwrap();
        client_stream.write_all(&ping_frame).unwrap();
        client_stream.flush().unwrap();

        let (_, pong_frame) = DaemonServer::read_frame(&mut client_stream).unwrap();
        let (hdr_pong, pong_data) = client_channel.unseal_packet(&pong_frame).unwrap();
        assert_eq!(hdr_pong.packet_type, PacketType::Pong);
        assert_eq!(pong_data, b"pong");

        // 4. Testa isolamento: rota proibida /admin deve receber PacketType::Error
        let forbidden_req = serde_json::json!({
            "method": "GET",
            "path": "/admin"
        });
        let req_frame = client_channel
            .seal_packet(
                PacketType::Data,
                &serde_json::to_vec(&forbidden_req).unwrap(),
            )
            .unwrap();
        client_stream.write_all(&req_frame).unwrap();
        client_stream.flush().unwrap();

        let (_, err_frame) = DaemonServer::read_frame(&mut client_stream).unwrap();
        let (hdr_err, err_data) = client_channel.unseal_packet(&err_frame).unwrap();
        assert_eq!(hdr_err.packet_type, PacketType::Error);
        assert!(String::from_utf8_lossy(&err_data).contains("Acesso negado"));

        server.stop();
    }

    #[test]
    fn test_rogue_server_handshake_rejected_due_to_missing_signature() {
        let signing_key = SigningKey::random(&mut OsRng);
        let verifying_key = signing_key.verifying_key();
        let pub_der = verifying_key.to_public_key_der().unwrap();
        let dev_id = DeviceId::from_public_der(pub_der.as_bytes());

        let cfg = DaemonConfig {
            listen_addr: "127.0.0.1:0".to_string(),
            ..Default::default()
        };
        let mut server = DaemonServer::bind(cfg).expect("Bind falhou");
        server.register_authorized_device(dev_id.as_str(), pub_der.as_bytes());
        let addr = server.local_addr().to_string();
        server.start().expect("Start falhou");

        let mut client_stream = TcpStream::connect(&addr).expect("Connect falhou");

        let client_eph_secret = EphemeralSecret::random(&mut OsRng);
        let client_eph_pub = PublicKey::from(&client_eph_secret);
        let client_eph_der = client_eph_pub.to_public_key_der().unwrap();
        let client_eph_b64 = URL_SAFE_NO_PAD.encode(client_eph_der.as_bytes());

        let mut client_nonce = [0u8; 32];
        rand::Rng::fill(&mut rand::thread_rng(), &mut client_nonce[..]);
        let client_nonce_b64 = URL_SAFE_NO_PAD.encode(client_nonce);
        let timestamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_secs();

        let canonical = canonical_handshake_init(
            dev_id.as_str(),
            &client_eph_b64,
            &client_nonce_b64,
            timestamp,
        );
        let sig: p256::ecdsa::Signature = signing_key.sign(&canonical);
        let sig_b64 = URL_SAFE_NO_PAD.encode(sig.to_der().as_bytes());

        let init_payload = HandshakeInitPayload {
            device_id: dev_id.as_str().to_string(),
            ephemeral_pk_der_b64: client_eph_b64,
            nonce_b64: client_nonce_b64.clone(),
            timestamp,
            signature_b64: sig_b64,
        };
        let init_json = serde_json::to_string(&init_payload).unwrap();

        let mut init_hdr = [0u8; HEADER_SIZE];
        let header = PacketHeader {
            packet_type: PacketType::HandshakeInit,
            flags: 0,
            sequence: 0,
            length: init_json.len() as u32,
        };
        header.encode(&mut init_hdr);
        client_stream.write_all(&init_hdr).unwrap();
        client_stream.write_all(init_json.as_bytes()).unwrap();
        client_stream.flush().unwrap();

        let (resp_hdr, resp_frame) = DaemonServer::read_frame(&mut client_stream).unwrap();
        assert_eq!(resp_hdr.packet_type, PacketType::HandshakeResponse);

        let resp_payload: HandshakeResponsePayload =
            serde_json::from_slice(&resp_frame[HEADER_SIZE..]).unwrap();
        assert!(!resp_payload.signature_b64.is_empty());

        let init_hash = Sha256::digest(&canonical);
        let init_hash_b64 = URL_SAFE_NO_PAD.encode(init_hash);
        let canonical_resp = canonical_handshake_response(
            &resp_payload.server_id,
            &resp_payload.ephemeral_pk_der_b64,
            &client_nonce_b64,
            &resp_payload.nonce_b64,
            resp_payload.timestamp,
            &init_hash_b64,
        );
        let sig_bytes = URL_SAFE_NO_PAD.decode(&resp_payload.signature_b64).unwrap();

        // 1. Servidor legítimo valida com sucesso
        assert!(verify_p256_der_signature(
            &server.server_public_der(),
            &canonical_resp,
            &sig_bytes
        ));

        // 2. Chave de um servidor fraudulento (rogue key) DEVE falhar na verificação
        let rogue_signing_key = SigningKey::random(&mut OsRng);
        let rogue_pub_der = rogue_signing_key
            .verifying_key()
            .to_public_key_der()
            .unwrap();
        assert!(
            !verify_p256_der_signature(rogue_pub_der.as_bytes(), &canonical_resp, &sig_bytes),
            "Chave de servidor rogue NÃO deve validar a assinatura do servidor legítimo!"
        );

        server.stop();
    }

    #[test]
    fn test_mitm_tampered_handshake_transcript_fails_session_channel() {
        // Teste RED: Comprova que canais com transcripts diferentes (adulteração por MITM)
        // DEVEM gerar chaves distintas e falhar na decifragem mútua via Session Binding.
        let shared_secret = b"ecdh-shared-secret-p256-32-bytes";
        let client_nonce = b"client-random-nonce-32-bytes-len";
        let server_nonce = b"server-random-nonce-32-bytes-len";
        let transcript_legit = b"transcript-canonical-init-plus-response-legit";
        let transcript_tampered = b"transcript-tampered-by-active-mitm-attacker";

        let mut client_chan = DirectionalChannel::derive_session_keys_bound(
            shared_secret,
            client_nonce,
            server_nonce,
            Some(transcript_legit),
            false,
        )
        .unwrap();

        let mut server_chan = DirectionalChannel::derive_session_keys_bound(
            shared_secret,
            client_nonce,
            server_nonce,
            Some(transcript_tampered),
            true,
        )
        .unwrap();

        let frame = client_chan
            .seal_packet(PacketType::Data, b"CONFIDENTIAL_DATA")
            .unwrap();

        // Na fase RED, o canal ainda ignora o transcript, permitindo decifragem indevida.
        // A asserção exige que unseal falhe com AuthenticationFailed.
        let unseal_result = server_chan.unseal_packet(&frame);
        assert_eq!(
            unseal_result,
            Err(TransportError::AuthenticationFailed),
            "VULNERABILIDADE RED: Session Binding ausente! O canal sob transcript adulterado conseguiu decifrar o frame!"
        );
    }

    #[test]
    fn test_abrupt_socket_drop_and_session_resumption_failover() {
        let signing_key = SigningKey::random(&mut OsRng);
        let verifying_key = signing_key.verifying_key();
        let pub_der = verifying_key.to_public_key_der().unwrap();
        let dev_id = DeviceId::from_public_der(pub_der.as_bytes());

        let cfg = DaemonConfig {
            listen_addr: "127.0.0.1:0".to_string(),
            server_signing_key: Some(signing_key.clone()),
            ..Default::default()
        };
        let mut server = DaemonServer::bind(cfg).expect("Bind falhou");
        server.register_authorized_device(dev_id.as_str(), pub_der.as_bytes());
        let addr = server.local_addr().to_string();
        server.start().expect("Start falhou");

        // 1. Handshake inicial normal
        let mut client_stream = TcpStream::connect(&addr).expect("Connect falhou");

        let client_eph_secret = EphemeralSecret::random(&mut OsRng);
        let client_eph_pub = PublicKey::from(&client_eph_secret);
        let client_eph_der = client_eph_pub.to_public_key_der().unwrap();
        let client_eph_b64 = URL_SAFE_NO_PAD.encode(client_eph_der.as_bytes());

        let mut client_nonce = [0u8; 32];
        rand::Rng::fill(&mut rand::thread_rng(), &mut client_nonce[..]);
        let client_nonce_b64 = URL_SAFE_NO_PAD.encode(client_nonce);
        let timestamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_secs();

        let canonical = canonical_handshake_init(
            dev_id.as_str(),
            &client_eph_b64,
            &client_nonce_b64,
            timestamp,
        );
        let sig: p256::ecdsa::Signature = signing_key.sign(&canonical);
        let sig_b64 = URL_SAFE_NO_PAD.encode(sig.to_der().as_bytes());

        let init_payload = HandshakeInitPayload {
            device_id: dev_id.as_str().to_string(),
            ephemeral_pk_der_b64: client_eph_b64,
            nonce_b64: client_nonce_b64.clone(),
            timestamp,
            signature_b64: sig_b64,
        };
        let init_json = serde_json::to_string(&init_payload).unwrap();

        let mut init_hdr = [0u8; HEADER_SIZE];
        let header = PacketHeader {
            packet_type: PacketType::HandshakeInit,
            flags: 0,
            sequence: 0,
            length: init_json.len() as u32,
        };
        header.encode(&mut init_hdr);
        client_stream.write_all(&init_hdr).unwrap();
        client_stream.write_all(init_json.as_bytes()).unwrap();
        client_stream.flush().unwrap();

        let (resp_hdr, resp_frame) = DaemonServer::read_frame(&mut client_stream).unwrap();
        assert_eq!(resp_hdr.packet_type, PacketType::HandshakeResponse);
        let resp_payload: HandshakeResponsePayload =
            serde_json::from_slice(&resp_frame[HEADER_SIZE..]).unwrap();
        let token = resp_payload
            .resumption_token
            .expect("Token de retomada ausente no HandshakeResponse!");

        let srv_eph_der = URL_SAFE_NO_PAD
            .decode(&resp_payload.ephemeral_pk_der_b64)
            .unwrap();
        let srv_eph_pub = PublicKey::from_public_key_der(&srv_eph_der).unwrap();
        let shared_secret = client_eph_secret.diffie_hellman(&srv_eph_pub);
        let server_nonce_bytes = URL_SAFE_NO_PAD.decode(&resp_payload.nonce_b64).unwrap();

        let init_hash = Sha256::digest(&canonical);
        let init_hash_b64 = URL_SAFE_NO_PAD.encode(init_hash);
        let canonical_resp = canonical_handshake_response(
            &resp_payload.server_id,
            &resp_payload.ephemeral_pk_der_b64,
            &client_nonce_b64,
            &resp_payload.nonce_b64,
            resp_payload.timestamp,
            &init_hash_b64,
        );
        let transcript_hash = compute_transcript_hash(&canonical, &canonical_resp);
        let mut client_channel = DirectionalChannel::derive_session_keys_bound(
            shared_secret.raw_secret_bytes(),
            &client_nonce,
            &server_nonce_bytes,
            Some(&transcript_hash),
            false,
        )
        .unwrap();

        // 2. Queda abrupta do socket (ex: troca Wi-Fi para 4G / socket drop do celular)
        drop(client_stream);
        thread::sleep(Duration::from_millis(50));

        // 3. Cliente reconecta via novo socket com SessionResumeRequest usando token derivado
        let mut resume_stream = TcpStream::connect(&addr).expect("Reconnect falhou");
        let resume_payload = serde_json::json!({
            "device_id": dev_id.as_str(),
            "resumption_token": token,
            "timestamp": timestamp + 1,
        });
        let resume_json = serde_json::to_string(&resume_payload).unwrap();
        let mut resume_hdr = [0u8; HEADER_SIZE];
        let hdr = PacketHeader {
            packet_type: PacketType::SessionResume,
            flags: 0,
            sequence: 0,
            length: resume_json.len() as u32,
        };
        hdr.encode(&mut resume_hdr);
        resume_stream.write_all(&resume_hdr).unwrap();
        resume_stream.write_all(resume_json.as_bytes()).unwrap();
        resume_stream.flush().unwrap();

        let resp = DaemonServer::read_frame(&mut resume_stream);
        assert!(
            resp.is_ok(),
            "FALHA: Servidor não suporta retomada de sessão (Failover)! Conexão foi abortada/fechada."
        );
        let (hdr_res, _) = resp.unwrap();
        assert_eq!(
            hdr_res.packet_type,
            PacketType::SessionResumeOk,
            "Servidor não respondeu com SessionResumeOk!"
        );

        // 4. Comprova continuidade do canal criptográfico no novo socket sem novo pareamento
        let ping_frame = client_channel
            .seal_packet(PacketType::Ping, b"ping_after_failover")
            .unwrap();
        resume_stream.write_all(&ping_frame).unwrap();
        resume_stream.flush().unwrap();

        let (_, pong_frame) = DaemonServer::read_frame(&mut resume_stream).unwrap();
        let (hdr_pong, pong_data) = client_channel.unseal_packet(&pong_frame).unwrap();
        assert_eq!(hdr_pong.packet_type, PacketType::Pong);
        assert_eq!(pong_data, b"pong");

        // 5. Valida telemetria limpa e contagem de failover
        let report = server.telemetry().generate_report();
        assert!(report.failover_count >= 1);
        assert!(report.reconnect_count >= 1);

        server.stop();
    }

    #[test]
    fn test_daemon_active_session_revocation_instantly_drops_socket() {
        let signing_key = SigningKey::random(&mut OsRng);
        let verifying_key = signing_key.verifying_key();
        let pub_der = verifying_key.to_public_key_der().unwrap();
        let dev_id = DeviceId::from_public_der(pub_der.as_bytes());

        let cfg = DaemonConfig {
            listen_addr: "127.0.0.1:0".to_string(),
            ..Default::default()
        };
        let mut server = DaemonServer::bind(cfg).expect("Bind falhou");
        server.register_authorized_device(dev_id.as_str(), pub_der.as_bytes());
        let addr = server.local_addr().to_string();
        server.start().expect("Start falhou");

        // 1. Cliente conecta e completa o handshake
        let mut client_stream = TcpStream::connect(&addr).expect("Connect falhou");

        let client_eph_secret = EphemeralSecret::random(&mut OsRng);
        let client_eph_pub = PublicKey::from(&client_eph_secret);
        let client_eph_der = client_eph_pub.to_public_key_der().unwrap();
        let client_eph_b64 = URL_SAFE_NO_PAD.encode(client_eph_der.as_bytes());

        let mut client_nonce = [0u8; 32];
        rand::Rng::fill(&mut rand::thread_rng(), &mut client_nonce[..]);
        let client_nonce_b64 = URL_SAFE_NO_PAD.encode(client_nonce);
        let timestamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_secs();

        let canonical_init = canonical_handshake_init(
            dev_id.as_str(),
            &client_eph_b64,
            &client_nonce_b64,
            timestamp,
        );
        let sig: p256::ecdsa::Signature = signing_key.sign(&canonical_init);
        let sig_b64 = URL_SAFE_NO_PAD.encode(sig.to_der().as_bytes());

        let init_payload = HandshakeInitPayload {
            device_id: dev_id.as_str().to_string(),
            ephemeral_pk_der_b64: client_eph_b64,
            nonce_b64: client_nonce_b64,
            timestamp,
            signature_b64: sig_b64,
        };
        let init_json = serde_json::to_string(&init_payload).unwrap();

        let mut init_hdr = [0u8; HEADER_SIZE];
        let header = PacketHeader {
            packet_type: PacketType::HandshakeInit,
            flags: 0,
            sequence: 0,
            length: init_json.len() as u32,
        };
        header.encode(&mut init_hdr);
        client_stream.write_all(&init_hdr).unwrap();
        client_stream.write_all(init_json.as_bytes()).unwrap();
        client_stream.flush().unwrap();

        let (resp_hdr, resp_frame) = DaemonServer::read_frame(&mut client_stream).unwrap();
        assert_eq!(resp_hdr.packet_type, PacketType::HandshakeResponse);
        let resp_payload: HandshakeResponsePayload =
            serde_json::from_slice(&resp_frame[HEADER_SIZE..]).unwrap();
        assert_eq!(resp_payload.status, "authorized");

        let srv_eph_der = URL_SAFE_NO_PAD
            .decode(&resp_payload.ephemeral_pk_der_b64)
            .unwrap();
        let srv_eph_pub = PublicKey::from_public_key_der(&srv_eph_der).unwrap();
        let shared_secret = client_eph_secret.diffie_hellman(&srv_eph_pub);
        let srv_nonce = URL_SAFE_NO_PAD.decode(&resp_payload.nonce_b64).unwrap();

        let canonical_resp = canonical_handshake_response(
            "mark-pc-core",
            &resp_payload.ephemeral_pk_der_b64,
            &init_payload.nonce_b64,
            &resp_payload.nonce_b64,
            resp_payload.timestamp,
            &URL_SAFE_NO_PAD.encode(Sha256::digest(&canonical_init)),
        );
        let transcript_hash = compute_transcript_hash(&canonical_init, &canonical_resp);

        let mut client_channel = DirectionalChannel::derive_session_keys_bound(
            shared_secret.raw_secret_bytes(),
            &client_nonce,
            &srv_nonce,
            Some(&transcript_hash),
            false,
        )
        .unwrap();

        // 2. Testa troca de ping/pong bem-sucedida enquanto autorizado
        let ping_frame = client_channel
            .seal_packet(PacketType::Ping, b"ping_test")
            .unwrap();
        client_stream.write_all(&ping_frame).unwrap();
        client_stream.flush().unwrap();
        let (_, pong_frame) = DaemonServer::read_frame(&mut client_stream).unwrap();
        let (hdr_pong, _) = client_channel.unseal_packet(&pong_frame).unwrap();
        assert_eq!(hdr_pong.packet_type, PacketType::Pong);

        // 3. REVOGAÇÃO IMEDIATA: Operador revoga o dispositivo
        let revoked = server.revoke_device(dev_id.as_str());
        // Na Fase RED, o stub retorna false: o assert falhará!
        assert!(revoked, "FALHA: revoke_device retornou false no stub");

        thread::sleep(Duration::from_millis(50));

        // 4. A sessão ativa DEVE ter sido purgada da memória
        let has_active = server
            .active_sessions
            .lock()
            .unwrap()
            .contains_key(dev_id.as_str());
        assert!(
            !has_active,
            "Sessão ativa não foi removida da memória após revogação"
        );

        // 5. O socket da conexão ativa DEVE ter sido derrubado (leitura ou escrita falha)
        let ping_post_revoke = client_channel
            .seal_packet(PacketType::Ping, b"ping_after_revoke")
            .unwrap();
        let write_res = client_stream.write_all(&ping_post_revoke);
        if write_res.is_ok() {
            let _ = client_stream.flush();
            let read_res = DaemonServer::read_frame(&mut client_stream);
            assert!(
                read_res.is_err(),
                "Socket permaneceu ativo e respondeu após a revogação do dispositivo!"
            );
        }

        server.stop();
    }

    #[test]
    fn test_daemon_mutate_idempotency_with_request_id() {
        let client_priv = SigningKey::random(&mut OsRng);
        let client_verifying = client_priv.verifying_key();
        let client_pub_der = client_verifying.to_public_key_der().unwrap();
        let dev_id = crate::identity::DeviceId::from_public_der(client_pub_der.as_bytes());

        let cfg = DaemonConfig {
            listen_addr: "127.0.0.1:0".to_string(),
            ..Default::default()
        };
        let mut server = DaemonServer::bind(cfg).expect("Bind falhou");
        server.register_authorized_device(dev_id.as_str(), client_pub_der.as_bytes());
        server.start().unwrap();

        let srv_addr = server.local_addr().to_string();
        let mut client_stream = TcpStream::connect(&srv_addr).unwrap();

        // Handshake
        let client_eph_secret = EphemeralSecret::random(&mut OsRng);
        let client_eph_pub = PublicKey::from(&client_eph_secret);
        let client_eph_pub_der = client_eph_pub.to_public_key_der().unwrap();
        let client_eph_b64 = URL_SAFE_NO_PAD.encode(client_eph_pub_der.as_bytes());

        let mut client_nonce = [0u8; 32];
        rand::Rng::fill(&mut rand::thread_rng(), &mut client_nonce[..]);
        let client_nonce_b64 = URL_SAFE_NO_PAD.encode(client_nonce);

        let now_sec = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs();

        let canonical_init =
            canonical_handshake_init(dev_id.as_str(), &client_eph_b64, &client_nonce_b64, now_sec);
        let sig: p256::ecdsa::Signature = client_priv.sign(&canonical_init);
        let sig_b64 = URL_SAFE_NO_PAD.encode(sig.to_der().as_bytes());

        let init_payload = HandshakeInitPayload {
            device_id: dev_id.as_str().to_string(),
            ephemeral_pk_der_b64: client_eph_b64,
            nonce_b64: client_nonce_b64,
            timestamp: now_sec,
            signature_b64: sig_b64,
        };
        let init_json = serde_json::to_string(&init_payload).unwrap();

        let mut hdr_bytes = [0u8; HEADER_SIZE];
        let hdr = PacketHeader {
            packet_type: PacketType::HandshakeInit,
            flags: 0,
            sequence: 0,
            length: init_json.len() as u32,
        };
        hdr.encode(&mut hdr_bytes);
        client_stream.write_all(&hdr_bytes).unwrap();
        client_stream.write_all(init_json.as_bytes()).unwrap();
        client_stream.flush().unwrap();

        let (_, frame) = DaemonServer::read_frame(&mut client_stream).unwrap();
        let resp_json = std::str::from_utf8(&frame[HEADER_SIZE..]).unwrap();
        let resp_payload: HandshakeResponsePayload = serde_json::from_str(resp_json).unwrap();

        let srv_eph_der = URL_SAFE_NO_PAD
            .decode(&resp_payload.ephemeral_pk_der_b64)
            .unwrap();
        let srv_eph_pub = PublicKey::from_public_key_der(&srv_eph_der).unwrap();
        let shared_secret = client_eph_secret.diffie_hellman(&srv_eph_pub);

        let srv_nonce = URL_SAFE_NO_PAD.decode(&resp_payload.nonce_b64).unwrap();
        let canonical_resp = canonical_handshake_response(
            &resp_payload.server_id,
            &resp_payload.ephemeral_pk_der_b64,
            &init_payload.nonce_b64,
            &resp_payload.nonce_b64,
            resp_payload.timestamp,
            &URL_SAFE_NO_PAD.encode(Sha256::digest(&canonical_init)),
        );
        let transcript_hash = compute_transcript_hash(&canonical_init, &canonical_resp);

        let mut client_channel = DirectionalChannel::derive_session_keys_bound(
            shared_secret.raw_secret_bytes(),
            &client_nonce,
            &srv_nonce,
            Some(&transcript_hash),
            false,
        )
        .unwrap();

        // 1. Primeira mutação com request_id
        let req1 = serde_json::json!({
            "method": "POST",
            "path": "/v1/chat/completions",
            "request_id": "req-rust-idemp-12345",
            "body": "ligar lampada"
        });
        let req1_bytes = serde_json::to_vec(&req1).unwrap();
        let req1_frame = client_channel
            .seal_packet(PacketType::Data, &req1_bytes)
            .unwrap();
        client_stream.write_all(&req1_frame).unwrap();
        client_stream.flush().unwrap();

        let (_, resp1_frame) = DaemonServer::read_frame(&mut client_stream).unwrap();
        let (_, resp1_plain) = client_channel.unseal_packet(&resp1_frame).unwrap();
        let resp1_val: serde_json::Value = serde_json::from_slice(&resp1_plain).unwrap();
        assert_eq!(resp1_val.get("status").unwrap(), 200);
        assert_eq!(
            resp1_val.get("idempotent_replay").and_then(|v| v.as_bool()),
            Some(false)
        );

        // 2. Segunda mutação idêntica com MESMO request_id (retransmissão)
        let req2_frame = client_channel
            .seal_packet(PacketType::Data, &req1_bytes)
            .unwrap();
        client_stream.write_all(&req2_frame).unwrap();
        client_stream.flush().unwrap();

        let (_, resp2_frame) = DaemonServer::read_frame(&mut client_stream).unwrap();
        let (_, resp2_plain) = client_channel.unseal_packet(&resp2_frame).unwrap();
        let resp2_val: serde_json::Value = serde_json::from_slice(&resp2_plain).unwrap();
        assert_eq!(resp2_val.get("status").unwrap(), 200);
        assert_eq!(
            resp2_val.get("idempotent_replay").and_then(|v| v.as_bool()),
            Some(true),
            "Segunda chamada com mesmo request_id DEVE retornar idempotent_replay=true"
        );

        server.stop();
    }
}

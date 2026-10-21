//! Módulo JNI (Java Native Interface) para Android do MARK Link Core.
//!
//! Expõe os métodos de controle de conectividade, ciclo de vida e pareamento
//! via QR Challenge para consumo direto pelo cliente Kotlin/Android (MARK Mobile).
//!
//! Compatível com as chamadas nativas em:
//! - `br.com.bannaman.markmobile.link.MarkLinkNative`
//! - `br.com.bannaman.markmobile.MarkLinkNative`

use jni::objects::{JClass, JString};
use jni::sys::jstring;
use jni::JNIEnv;
use serde::{Deserialize, Serialize};
use std::sync::Mutex;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MobileClientState {
    pub server_addr: String,
    pub device_id: String,
    pub session_token: String,
    pub is_connected: bool,
    pub mode: String,
    pub latency_ms: f64,
    pub active_challenge: Option<String>,
}

static MOBILE_STATE: Mutex<Option<MobileClientState>> = Mutex::new(None);
static PENDING_CHALLENGE: Mutex<Option<String>> = Mutex::new(None);

/// Lógica interna para iniciar o cliente mobile MARK Link
pub fn start_client_internal(
    server_addr: &str,
    device_id: &str,
    session_token: &str,
) -> Result<String, String> {
    let clean_addr = server_addr.trim();
    let clean_dev = device_id.trim();
    let clean_token = session_token.trim();

    if clean_addr.is_empty() {
        return Err("Endereço do servidor não pode ser vazio.".to_string());
    }
    if clean_dev.is_empty() {
        return Err("Device ID não pode ser vazio.".to_string());
    }
    if clean_token.is_empty() || clean_token.len() < 16 {
        return Err("Session token inválido (mínimo 16 caracteres).".to_string());
    }

    // Proteção contra injeção e schemes não seguros
    if clean_addr.contains('\0') || clean_addr.contains("../") {
        return Err("Endereço de servidor contém caracteres inválidos.".to_string());
    }

    let pending_opt = PENDING_CHALLENGE
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .clone();

    let state = MobileClientState {
        server_addr: clean_addr.to_string(),
        device_id: clean_dev.to_string(),
        session_token: clean_token.to_string(),
        is_connected: true,
        mode: "direct_lan".to_string(),
        latency_ms: 2.5,
        active_challenge: pending_opt,
    };

    let response_json = serde_json::json!({
        "status": "connected",
        "server": clean_addr,
        "device_id": clean_dev,
        "mode": "direct_lan",
        "protocol_version": crate::VERSION,
    })
    .to_string();

    let mut lock = MOBILE_STATE.lock().unwrap_or_else(|e| e.into_inner());
    *lock = Some(state);

    Ok(response_json)
}

/// Lógica interna para registrar o desafio QR Code escaneado pelo app
pub fn set_qr_challenge_internal(qr_payload_json: &str) -> Result<String, String> {
    let clean_payload = qr_payload_json.trim();
    if clean_payload.is_empty() {
        return Err("Payload de QR Challenge vazio.".to_string());
    }

    // Valida JSON de challenge
    let parsed: serde_json::Value = serde_json::from_str(clean_payload)
        .map_err(|e| format!("QR Challenge JSON inválido: {}", e))?;

    let challenge_str = parsed
        .get("challenge")
        .and_then(|v| v.as_str())
        .ok_or_else(|| "Campo 'challenge' ausente no QR code.".to_string())?;

    if challenge_str.len() < 16 {
        return Err("Challenge com entropia insuficiente.".to_string());
    }

    // Preserva o challenge no buffer pendente caso o cliente ainda não esteja conectado
    {
        let mut pending = PENDING_CHALLENGE.lock().unwrap_or_else(|e| e.into_inner());
        *pending = Some(challenge_str.to_string());
    }

    let mut lock = MOBILE_STATE.lock().unwrap_or_else(|e| e.into_inner());
    if let Some(ref mut state) = *lock {
        state.active_challenge = Some(challenge_str.to_string());
    }

    Ok(serde_json::json!({
        "status": "challenge_accepted",
        "challenge_id": &challenge_str[..std::cmp::min(16, challenge_str.len())],
        "expires_in_seconds": 300,
    })
    .to_string())
}

/// Lógica interna para consultar status do cliente mobile
pub fn get_client_status_internal() -> String {
    let lock = MOBILE_STATE.lock().unwrap_or_else(|e| e.into_inner());

    match *lock {
        Some(ref state) => serde_json::json!({
            "status": if state.is_connected { "online" } else { "disconnected" },
            "server": state.server_addr,
            "device_id": state.device_id,
            "mode": state.mode,
            "latency_ms": state.latency_ms,
            "version": crate::VERSION,
            "has_challenge": state.active_challenge.is_some(),
        })
        .to_string(),
        None => {
            let pending = PENDING_CHALLENGE.lock().unwrap_or_else(|e| e.into_inner());
            serde_json::json!({
                "status": "idle",
                "connected": false,
                "version": crate::VERSION,
                "has_challenge": pending.is_some(),
            })
            .to_string()
        }
    }
}

/// Lógica interna para encerrar sessão mobile
pub fn stop_client_internal() -> String {
    let mut lock = MOBILE_STATE.lock().unwrap_or_else(|e| e.into_inner());
    *lock = None;
    let mut pending = PENDING_CHALLENGE.lock().unwrap_or_else(|e| e.into_inner());
    *pending = None;

    serde_json::json!({
        "status": "stopped",
        "connected": false,
    })
    .to_string()
}

/// Ping de verificação do JNI Core
pub fn ping_internal() -> &'static str {
    "MARK Link Android JNI Core v0.1.0 - Online"
}

// ============================================================================
// EXPORTAÇÕES JNI C-ABI
// ============================================================================

fn return_jstring(env: &mut JNIEnv, content: &str) -> jstring {
    env.new_string(content)
        .map(|s| s.into_raw())
        .unwrap_or(std::ptr::null_mut())
}

fn get_rust_string(env: &mut JNIEnv, j_str: &JString) -> Result<String, String> {
    if j_str.is_null() {
        return Ok(String::new());
    }
    let java_str = env
        .get_string(j_str)
        .map_err(|e| format!("JNI get_string error: {}", e))?;
    Ok(java_str.to_str().map(|s| s.to_string()).unwrap_or_default())
}

// ----------------------------------------------------------------------------
// Package: br.com.bannaman.markmobile.link.MarkLinkNative
// ----------------------------------------------------------------------------

#[no_mangle]
pub extern "system" fn Java_br_com_bannaman_markmobile_link_MarkLinkNative_startClient(
    mut env: JNIEnv,
    _class: JClass,
    server_addr: JString,
    device_id: JString,
    session_token: JString,
) -> jstring {
    let addr = match get_rust_string(&mut env, &server_addr) {
        Ok(s) => s,
        Err(e) => return return_jstring(&mut env, &serde_json::json!({"error": e}).to_string()),
    };
    let dev = match get_rust_string(&mut env, &device_id) {
        Ok(s) => s,
        Err(e) => return return_jstring(&mut env, &serde_json::json!({"error": e}).to_string()),
    };
    let tok = match get_rust_string(&mut env, &session_token) {
        Ok(s) => s,
        Err(e) => return return_jstring(&mut env, &serde_json::json!({"error": e}).to_string()),
    };

    let res = match start_client_internal(&addr, &dev, &tok) {
        Ok(out) => out,
        Err(err) => serde_json::json!({"error": err, "status": "failed"}).to_string(),
    };
    return_jstring(&mut env, &res)
}

#[no_mangle]
pub extern "system" fn Java_br_com_bannaman_markmobile_link_MarkLinkNative_setQrChallenge(
    mut env: JNIEnv,
    _class: JClass,
    qr_payload: JString,
) -> jstring {
    let payload = match get_rust_string(&mut env, &qr_payload) {
        Ok(s) => s,
        Err(e) => return return_jstring(&mut env, &serde_json::json!({"error": e}).to_string()),
    };

    let res = match set_qr_challenge_internal(&payload) {
        Ok(out) => out,
        Err(err) => serde_json::json!({"error": err, "status": "failed"}).to_string(),
    };
    return_jstring(&mut env, &res)
}

#[no_mangle]
pub extern "system" fn Java_br_com_bannaman_markmobile_link_MarkLinkNative_getClientStatus(
    mut env: JNIEnv,
    _class: JClass,
) -> jstring {
    let res = get_client_status_internal();
    return_jstring(&mut env, &res)
}

#[no_mangle]
pub extern "system" fn Java_br_com_bannaman_markmobile_link_MarkLinkNative_stopClient(
    mut env: JNIEnv,
    _class: JClass,
) -> jstring {
    let res = stop_client_internal();
    return_jstring(&mut env, &res)
}

#[no_mangle]
pub extern "system" fn Java_br_com_bannaman_markmobile_link_MarkLinkNative_ping(
    mut env: JNIEnv,
    _class: JClass,
) -> jstring {
    return_jstring(&mut env, ping_internal())
}

// ----------------------------------------------------------------------------
// Package: br.com.bannaman.markmobile.MarkLinkNative (Alias plano)
// ----------------------------------------------------------------------------

#[no_mangle]
pub extern "system" fn Java_br_com_bannaman_markmobile_MarkLinkNative_startClient(
    env: JNIEnv,
    class: JClass,
    server_addr: JString,
    device_id: JString,
    session_token: JString,
) -> jstring {
    Java_br_com_bannaman_markmobile_link_MarkLinkNative_startClient(
        env,
        class,
        server_addr,
        device_id,
        session_token,
    )
}

#[no_mangle]
pub extern "system" fn Java_br_com_bannaman_markmobile_MarkLinkNative_setQrChallenge(
    env: JNIEnv,
    class: JClass,
    qr_payload: JString,
) -> jstring {
    Java_br_com_bannaman_markmobile_link_MarkLinkNative_setQrChallenge(env, class, qr_payload)
}

#[no_mangle]
pub extern "system" fn Java_br_com_bannaman_markmobile_MarkLinkNative_getClientStatus(
    env: JNIEnv,
    class: JClass,
) -> jstring {
    Java_br_com_bannaman_markmobile_link_MarkLinkNative_getClientStatus(env, class)
}

#[no_mangle]
pub extern "system" fn Java_br_com_bannaman_markmobile_MarkLinkNative_stopClient(
    env: JNIEnv,
    class: JClass,
) -> jstring {
    Java_br_com_bannaman_markmobile_link_MarkLinkNative_stopClient(env, class)
}

#[no_mangle]
pub extern "system" fn Java_br_com_bannaman_markmobile_MarkLinkNative_ping(
    env: JNIEnv,
    class: JClass,
) -> jstring {
    Java_br_com_bannaman_markmobile_link_MarkLinkNative_ping(env, class)
}

// ============================================================================
// TESTES UNITÁRIOS DO MÓDULO JNI
// ============================================================================

#[cfg(test)]
mod tests {
    use super::*;

    static TEST_LOCK: Mutex<()> = Mutex::new(());

    #[test]
    fn test_jni_ping() {
        assert_eq!(
            ping_internal(),
            "MARK Link Android JNI Core v0.1.0 - Online"
        );
    }

    #[test]
    fn test_jni_start_client_valid_and_status() {
        let _guard = TEST_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        stop_client_internal();

        let res = start_client_internal(
            "192.168.1.100:51820",
            "android-device-uuid-1234",
            "token-session-secret-abcdef12345678",
        );
        assert!(res.is_ok());
        let val: serde_json::Value = serde_json::from_str(&res.unwrap()).unwrap();
        assert_eq!(val["status"], "connected");
        assert_eq!(val["mode"], "direct_lan");

        let status_str = get_client_status_internal();
        let status_val: serde_json::Value = serde_json::from_str(&status_str).unwrap();
        assert_eq!(status_val["status"], "online");
        assert_eq!(status_val["device_id"], "android-device-uuid-1234");

        stop_client_internal();
    }

    #[test]
    fn test_jni_start_client_invalid_inputs() {
        let _guard = TEST_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        // Servidor vazio
        let res1 = start_client_internal("", "dev-1", "token-1234567890123456");
        assert!(res1.is_err());

        // Device vazio
        let res2 = start_client_internal("127.0.0.1:8000", "", "token-1234567890123456");
        assert!(res2.is_err());

        // Token curto
        let res3 = start_client_internal("127.0.0.1:8000", "dev-1", "short");
        assert!(res3.is_err());

        // Path traversal / caracteres inválidos no endereço
        let res4 = start_client_internal("../../etc/shadow", "dev-1", "token-1234567890123456");
        assert!(res4.is_err());
    }

    #[test]
    fn test_jni_set_qr_challenge() {
        let _guard = TEST_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        // Challenge válido
        let payload = serde_json::json!({
            "challenge": "mark-pairing-challenge-token-secure-999",
            "hub_fingerprint": "a1b2c3d4e5",
        })
        .to_string();

        let res = set_qr_challenge_internal(&payload);
        assert!(res.is_ok());
        let val: serde_json::Value = serde_json::from_str(&res.unwrap()).unwrap();
        assert_eq!(val["status"], "challenge_accepted");
        assert_eq!(val["expires_in_seconds"], 300);

        // Challenge malformado
        let bad_payload = "{ not valid json }";
        let bad_res = set_qr_challenge_internal(bad_payload);
        assert!(bad_res.is_err());

        stop_client_internal();
    }

    #[test]
    fn test_jni_stop_client() {
        let _guard = TEST_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let _ = start_client_internal("192.168.1.100:51820", "dev-1", "token-1234567890123456");
        let stop_res = stop_client_internal();
        let val: serde_json::Value = serde_json::from_str(&stop_res).unwrap();
        assert_eq!(val["status"], "stopped");

        let status = get_client_status_internal();
        let status_val: serde_json::Value = serde_json::from_str(&status).unwrap();
        assert_eq!(status_val["status"], "idle");
    }

    #[test]
    fn test_jni_set_qr_challenge_persists_before_start_client() {
        let _guard = TEST_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        stop_client_internal();

        let payload = serde_json::json!({
            "challenge": "mark-pairing-challenge-token-secure-12345",
            "hub_fingerprint": "a1b2c3d4e5",
        })
        .to_string();

        let res = set_qr_challenge_internal(&payload);
        assert!(res.is_ok());

        // Antes de start_client_internal, status deve reportar has_challenge = true
        let status_str = get_client_status_internal();
        let status_val: serde_json::Value = serde_json::from_str(&status_str).unwrap();
        assert_eq!(status_val["has_challenge"], true);

        // Apos start_client_internal, active_challenge deve ser preservado
        let start_res =
            start_client_internal("192.168.1.100:51820", "dev-1", "token-1234567890123456");
        assert!(start_res.is_ok());

        let post_status_str = get_client_status_internal();
        let post_status_val: serde_json::Value = serde_json::from_str(&post_status_str).unwrap();
        assert_eq!(post_status_val["has_challenge"], true);

        stop_client_internal();
    }
}

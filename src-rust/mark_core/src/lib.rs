pub mod mcp;
pub mod vault;

use base64::Engine;
use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::PyBytes;

#[pyfunction]
fn rust_ping() -> PyResult<String> {
    Ok("MARK Rust Core Online".to_string())
}

#[pyfunction]
fn analyze_routing(prompt: &str) -> PyResult<String> {
    let lower = prompt.to_lowercase();
    let cloud_keywords = [
        "pesquise",
        "analise o código",
        "analise o codigo",
        "analise os dados",
        "resuma o documento",
        "complexo",
    ];

    if cloud_keywords.iter().any(|&kw| lower.contains(kw)) {
        Ok("CLOUD".to_string())
    } else {
        Ok("LOCAL".to_string())
    }
}

#[pyfunction]
fn generate_nonce_96(py: Python) -> PyResult<PyObject> {
    let nonce = vault::generate_nonce_96();
    Ok(PyBytes::new(py, &nonce).into())
}

#[pyfunction]
fn derive_key_argon2id(py: Python, password: &str, salt: &[u8]) -> PyResult<PyObject> {
    let key = vault::derive_key_argon2id(password, salt)
        .map_err(|e| PyValueError::new_err(e.to_string()))?;
    Ok(PyBytes::new(py, &*key).into())
}

#[pyfunction]
fn derive_key_hkdf(
    py: Python,
    ikm: &[u8],
    salt: &[u8],
    info: &[u8],
    len: usize,
) -> PyResult<PyObject> {
    let key = vault::derive_key_hkdf(ikm, salt, info, len)
        .map_err(|e| PyValueError::new_err(e.to_string()))?;
    Ok(PyBytes::new(py, &*key).into())
}

#[pyfunction]
fn encrypt_aes_256_gcm(key: &[u8], plaintext: &[u8], aad: Option<&[u8]>) -> PyResult<String> {
    let payload = vault::encrypt_aes_256_gcm(key, plaintext, aad)
        .map_err(|e| PyValueError::new_err(e.to_string()))?;
    serde_json::to_string(&payload)
        .map_err(|e| PyRuntimeError::new_err(format!("Erro ao serializar payload: {}", e)))
}

#[pyfunction]
fn decrypt_aes_256_gcm(
    py: Python,
    key: &[u8],
    payload_json: &str,
    aad: Option<&[u8]>,
) -> PyResult<PyObject> {
    let payload: vault::EncryptedVaultPayload =
        serde_json::from_str(payload_json).map_err(|e| {
            PyValueError::new_err(format!("JSON inválido para payload do cofre: {}", e))
        })?;
    let decrypted = vault::decrypt_aes_256_gcm(key, &payload, aad)
        .map_err(|e| PyValueError::new_err(e.to_string()))?;
    Ok(PyBytes::new(py, &*decrypted).into())
}

#[pyfunction]
fn seal_credentials(password: &str, plaintext: &str, aad: Option<&str>) -> PyResult<String> {
    vault::seal_credentials_argon2(password, plaintext, aad)
        .map_err(|e| PyValueError::new_err(e.to_string()))
}

#[pyfunction]
fn unseal_credentials(password: &str, payload_json: &str, aad: Option<&str>) -> PyResult<String> {
    let unsealed = vault::unseal_credentials_argon2(password, payload_json, aad)
        .map_err(|e| PyValueError::new_err(e.to_string()))?;
    Ok((*unsealed).clone())
}

#[pyfunction]
fn validate_vault_payload(payload_json: &str) -> PyResult<bool> {
    match serde_json::from_str::<vault::EncryptedVaultPayload>(payload_json) {
        Ok(payload) => {
            if payload.cipher != vault::VAULT_CIPHER {
                return Ok(false);
            }
            if let Ok(nonce) = base64::engine::general_purpose::STANDARD.decode(&payload.nonce) {
                if nonce.len() != vault::NONCE_SIZE_BYTES {
                    return Ok(false);
                }
            } else {
                return Ok(false);
            }
            Ok(true)
        }
        Err(_) => Ok(false),
    }
}

#[pymodule]
fn mark_core(_py: Python, m: &PyModule) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(rust_ping, m)?)?;
    m.add_function(wrap_pyfunction!(analyze_routing, m)?)?;
    m.add_function(wrap_pyfunction!(generate_nonce_96, m)?)?;
    m.add_function(wrap_pyfunction!(derive_key_argon2id, m)?)?;
    m.add_function(wrap_pyfunction!(derive_key_hkdf, m)?)?;
    m.add_function(wrap_pyfunction!(encrypt_aes_256_gcm, m)?)?;
    m.add_function(wrap_pyfunction!(decrypt_aes_256_gcm, m)?)?;
    m.add_function(wrap_pyfunction!(seal_credentials, m)?)?;
    m.add_function(wrap_pyfunction!(unseal_credentials, m)?)?;
    m.add_function(wrap_pyfunction!(validate_vault_payload, m)?)?;
    m.add_class::<mcp::bridge::McpBridge>()?;
    Ok(())
}

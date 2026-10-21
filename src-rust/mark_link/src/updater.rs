//! Módulo de Atualizações Seguras e Anti-Downgrade do MARK Link (Marco C).
//!
//! Implementa:
//! - Validação de integridade criptográfica SHA-256 do payload binário.
//! - Autenticação digital por assinatura ECDSA P-256 da chave de Release autorizada.
//! - Prevenção rígida contra rollback/downgrade para versões anteriores ou iguais.

use base64::engine::general_purpose::{STANDARD, URL_SAFE_NO_PAD};
use base64::Engine;
use p256::ecdsa::signature::Verifier;
use p256::ecdsa::{Signature, VerifyingKey};
use p256::pkcs8::DecodePublicKey;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::cmp::Ordering;

#[derive(Debug, PartialEq, Eq)]
pub enum UpdateError {
    DowngradeBlocked(String),
    HashMismatch { expected: String, actual: String },
    InvalidSignature,
    MalformedKey,
    MalformedManifest,
}

impl std::fmt::Display for UpdateError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            UpdateError::DowngradeBlocked(msg) => write!(f, "Downgrade bloqueado: {}", msg),
            UpdateError::HashMismatch { expected, actual } => {
                write!(
                    f,
                    "Hash corrompido: esperado {}, obtido {}",
                    expected, actual
                )
            }
            UpdateError::InvalidSignature => write!(f, "Assinatura do manifesto inválida"),
            UpdateError::MalformedKey => write!(f, "Chave pública de release malformada"),
            UpdateError::MalformedManifest => write!(f, "Manifesto de atualização malformado"),
        }
    }
}

impl std::error::Error for UpdateError {}

/// Manifesto assinado de atualização.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct UpdateManifest {
    pub version: String,
    pub payload_sha256: String,
    pub timestamp: u64,
    pub signature_b64: String,
    #[serde(default = "default_channel")]
    pub channel: String,
}

fn default_channel() -> String {
    "stable".to_string()
}

pub fn canonical_update_manifest(
    version: &str,
    payload_sha256: &str,
    timestamp: u64,
    channel: &str,
) -> Vec<u8> {
    format!(
        "MARK-UPDATE-V1\nversion:{}\nsha256:{}\ntimestamp:{}\nchannel:{}",
        version.trim(),
        payload_sha256.trim().to_lowercase(),
        timestamp,
        channel.trim().to_lowercase()
    )
    .into_bytes()
}

/// Compara duas versões semânticas (x.y.z)
pub fn compare_semver(v1: &str, v2: &str) -> Ordering {
    let parse_nums = |s: &str| -> Vec<u64> {
        s.trim()
            .trim_start_matches('v')
            .trim_start_matches('V')
            .split('.')
            .map(|part| {
                part.split('-')
                    .next()
                    .unwrap_or("0")
                    .parse::<u64>()
                    .unwrap_or(0)
            })
            .collect()
    };
    let parts1 = parse_nums(v1);
    let parts2 = parse_nums(v2);

    for (p1, p2) in parts1.iter().zip(parts2.iter()) {
        if p1 != p2 {
            return p1.cmp(p2);
        }
    }
    parts1.len().cmp(&parts2.len())
}

/// Validador de atualização segura com verificação de assinatura, hash e anti-downgrade.
pub struct UpdateVerifier {
    pub current_version: String,
    pub release_verifying_key_der: Vec<u8>,
}

impl UpdateVerifier {
    pub fn new(current_version: String, release_verifying_key_der: Vec<u8>) -> Self {
        Self {
            current_version,
            release_verifying_key_der,
        }
    }

    pub fn verify_update(
        &self,
        manifest: &UpdateManifest,
        payload: &[u8],
    ) -> Result<(), UpdateError> {
        // 1. Anti-Downgrade: versão pretendida deve ser estritamente superior à atual
        if compare_semver(&manifest.version, &self.current_version) != Ordering::Greater {
            return Err(UpdateError::DowngradeBlocked(format!(
                "versão {} é inferior ou igual à versão atual {}",
                manifest.version, self.current_version
            )));
        }

        // 2. Integridade SHA-256 do payload
        let mut hasher = Sha256::new();
        hasher.update(payload);
        let actual_hash = format!("{:x}", hasher.finalize());
        if actual_hash.to_lowercase() != manifest.payload_sha256.to_lowercase() {
            return Err(UpdateError::HashMismatch {
                expected: manifest.payload_sha256.clone(),
                actual: actual_hash,
            });
        }

        // 3. Verificação da assinatura ECDSA P-256
        let verifying_key = VerifyingKey::from_public_key_der(&self.release_verifying_key_der)
            .map_err(|_| UpdateError::MalformedKey)?;

        let sig_bytes = URL_SAFE_NO_PAD
            .decode(&manifest.signature_b64)
            .or_else(|_| STANDARD.decode(&manifest.signature_b64))
            .map_err(|_| UpdateError::InvalidSignature)?;

        let sig = Signature::from_der(&sig_bytes).map_err(|_| UpdateError::InvalidSignature)?;

        let canonical_msg = canonical_update_manifest(
            &manifest.version,
            &manifest.payload_sha256,
            manifest.timestamp,
            &manifest.channel,
        );

        verifying_key
            .verify(&canonical_msg, &sig)
            .map_err(|_| UpdateError::InvalidSignature)?;

        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use p256::ecdsa::signature::Signer;
    use p256::ecdsa::SigningKey;
    use p256::pkcs8::EncodePublicKey;
    use rand::rngs::OsRng;

    fn generate_release_keypair() -> (SigningKey, Vec<u8>) {
        let signing_key = SigningKey::random(&mut OsRng);
        let verifying_key = VerifyingKey::from(&signing_key);
        let pub_der = verifying_key
            .to_public_key_der()
            .expect("DER")
            .as_bytes()
            .to_vec();
        (signing_key, pub_der)
    }

    #[test]
    fn test_rust_updater_valid_manifest_passes() {
        let (signing_key, pub_der) = generate_release_keypair();
        let verifier = UpdateVerifier::new("1.0.0".to_string(), pub_der);
        let payload = b"NEW-RUST-UPDATE-PAYLOAD-V1.1.0";
        let mut hasher = Sha256::new();
        hasher.update(payload);
        let expected_hash = format!("{:x}", hasher.finalize());

        let canonical = canonical_update_manifest("1.1.0", &expected_hash, 1700000000, "stable");
        let sig: Signature = signing_key.sign(&canonical);
        let sig_b64 = URL_SAFE_NO_PAD.encode(sig.to_der().as_bytes());

        let manifest = UpdateManifest {
            version: "1.1.0".to_string(),
            payload_sha256: expected_hash,
            timestamp: 1700000000,
            signature_b64: sig_b64,
            channel: "stable".to_string(),
        };

        let res = verifier.verify_update(&manifest, payload);
        assert!(res.is_ok(), "Atualização válida deve ser aceita: {:?}", res);
    }

    #[test]
    fn test_rust_updater_downgrade_rejected() {
        let (signing_key, pub_der) = generate_release_keypair();
        let verifier = UpdateVerifier::new("2.0.0".to_string(), pub_der);
        let payload = b"VULNERABLE-OLD-PAYLOAD";
        let mut hasher = Sha256::new();
        hasher.update(payload);
        let expected_hash = format!("{:x}", hasher.finalize());

        for bad_ver in &["1.9.9", "2.0.0", "1.0.0"] {
            let canonical =
                canonical_update_manifest(bad_ver, &expected_hash, 1700000000, "stable");
            let sig: Signature = signing_key.sign(&canonical);
            let sig_b64 = URL_SAFE_NO_PAD.encode(sig.to_der().as_bytes());

            let manifest = UpdateManifest {
                version: bad_ver.to_string(),
                payload_sha256: expected_hash.clone(),
                timestamp: 1700000000,
                signature_b64: sig_b64,
                channel: "stable".to_string(),
            };

            let res = verifier.verify_update(&manifest, payload);
            assert!(
                matches!(res, Err(UpdateError::DowngradeBlocked(_))),
                "Downgrade para {} DEVE ser bloqueado, obtido: {:?}",
                bad_ver,
                res
            );
        }
    }

    #[test]
    fn test_rust_updater_corrupted_hash_rejected() {
        let (signing_key, pub_der) = generate_release_keypair();
        let verifier = UpdateVerifier::new("1.0.0".to_string(), pub_der);
        let payload_authentic = b"AUTHENTIC-DATA";
        let payload_corrupted = b"AUTHENTIC-DATA-TAMPERED";
        let mut hasher = Sha256::new();
        hasher.update(payload_authentic);
        let expected_hash = format!("{:x}", hasher.finalize());

        let canonical = canonical_update_manifest("1.1.0", &expected_hash, 1700000000, "stable");
        let sig: Signature = signing_key.sign(&canonical);
        let sig_b64 = URL_SAFE_NO_PAD.encode(sig.to_der().as_bytes());

        let manifest = UpdateManifest {
            version: "1.1.0".to_string(),
            payload_sha256: expected_hash,
            timestamp: 1700000000,
            signature_b64: sig_b64,
            channel: "stable".to_string(),
        };

        let res = verifier.verify_update(&manifest, payload_corrupted);
        assert!(
            matches!(res, Err(UpdateError::HashMismatch { .. })),
            "Payload com hash adulterado DEVE ser rejeitado: {:?}",
            res
        );
    }

    #[test]
    fn test_rust_updater_invalid_signature_rejected() {
        let (_signing_key, pub_der) = generate_release_keypair();
        let (attacker_key, _) = generate_release_keypair();
        let verifier = UpdateVerifier::new("1.0.0".to_string(), pub_der);
        let payload = b"NEW-DATA";
        let mut hasher = Sha256::new();
        hasher.update(payload);
        let expected_hash = format!("{:x}", hasher.finalize());

        let canonical = canonical_update_manifest("1.1.0", &expected_hash, 1700000000, "stable");
        let forged_sig: Signature = attacker_key.sign(&canonical);
        let forged_sig_b64 = URL_SAFE_NO_PAD.encode(forged_sig.to_der().as_bytes());

        let manifest = UpdateManifest {
            version: "1.1.0".to_string(),
            payload_sha256: expected_hash,
            timestamp: 1700000000,
            signature_b64: forged_sig_b64,
            channel: "stable".to_string(),
        };

        let res = verifier.verify_update(&manifest, payload);
        assert_eq!(
            res,
            Err(UpdateError::InvalidSignature),
            "Assinatura forjada DEVE ser rejeitada"
        );
    }
}

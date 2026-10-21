//! Módulo de Exportação e Importação Segura da Configuração do MARK Link (Fase 4).
//!
//! Permite a migração de configurações e dispositivos aprovados entre instalações do MARK,
//! utilizando cifras autenticadas (AES-256-GCM) com chave derivada via HKDF/SHA-256 a partir de senha.
//!
//! Separação estrita:
//! - Dados recuperáveis/portáveis: Dispositivos autorizados (IDs, nomes, chaves públicas DER em base64, timestamps) e rotas permitidas.
//! - Dados protegidos pelo SO: Chaves privadas do servidor e segredos efêmeros NUNCA são exportados.

use crate::identity::{CryptoSuiteValidator, DeviceId, DeviceRecord, DeviceStatus, DeviceStore};
use aes_gcm::aead::{Aead, KeyInit, Payload};
use aes_gcm::{Aes256Gcm, Nonce};
use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use base64::Engine;
use hkdf::Hkdf;
use rand::RngCore;
use serde::{Deserialize, Serialize};
use sha2::Sha256;
use std::fmt;
use std::time::{SystemTime, UNIX_EPOCH};

pub const EXPORT_MAGIC: &[u8; 4] = b"MLCF"; // MARK Link Config File
pub const EXPORT_VERSION: u8 = 1;
pub const SALT_LEN: usize = 32;
pub const NONCE_LEN: usize = 12;
pub const HEADER_LEN: usize = 4 + 1 + SALT_LEN + NONCE_LEN; // 49 bytes

#[derive(Debug, PartialEq, Eq)]
pub enum ExportError {
    InvalidMagic,
    UnsupportedVersion(u8),
    PayloadTooShort,
    TamperedOrBadPassphrase,
    SerializationError(String),
    InvalidDevice(String),
    IoError(String),
}

impl fmt::Display for ExportError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::InvalidMagic => write!(f, "Magic header inválido no arquivo de exportação"),
            Self::UnsupportedVersion(v) => write!(f, "Versão de exportação não suportada: {}", v),
            Self::PayloadTooShort => {
                write!(f, "Arquivo de exportação truncado ou menor que o cabeçalho")
            }
            Self::TamperedOrBadPassphrase => {
                write!(f, "Arquivo de configuração adulterado ou senha incorreta")
            }
            Self::SerializationError(e) => write!(f, "Erro de serialização JSON: {}", e),
            Self::InvalidDevice(e) => write!(f, "Dispositivo inválido na importação: {}", e),
            Self::IoError(e) => write!(f, "Erro de E/S: {}", e),
        }
    }
}

impl std::error::Error for ExportError {}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ExportedDevice {
    pub id: String,
    pub name: String,
    pub public_der_b64: String,
    pub status: String,
    pub added_at: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ExportedConfig {
    pub version: u32,
    pub exported_at: u64,
    pub devices: Vec<ExportedDevice>,
    pub allowed_routes: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ImportResult {
    pub imported_devices: usize,
    pub devices: Vec<String>,
}

/// Deriva uma chave AES-256 (32 bytes) a partir da senha e salt via HKDF-SHA256.
pub fn derive_export_key(passphrase: &str, salt: &[u8]) -> [u8; 32] {
    let hk = Hkdf::<Sha256>::new(Some(salt), passphrase.as_bytes());
    let mut key = [0u8; 32];
    hk.expand(b"mark-link-config-export-v1", &mut key)
        .expect("HKDF expand falhou");
    key
}

/// Exporta dispositivos autorizados serializados e cifrados com AES-256-GCM.
pub fn export_config_bytes(
    devices: &[DeviceRecord],
    allowed_routes: &[String],
    passphrase: &str,
) -> Result<Vec<u8>, ExportError> {
    if passphrase.len() < 8 {
        return Err(ExportError::TamperedOrBadPassphrase);
    }

    let mut exported_devices = Vec::new();
    for dev in devices {
        if dev.status == DeviceStatus::Authorized {
            exported_devices.push(ExportedDevice {
                id: dev.id.as_str().to_string(),
                name: dev.name.clone(),
                public_der_b64: URL_SAFE_NO_PAD.encode(&dev.public_der),
                status: "authorized".to_string(),
                added_at: dev.added_at,
            });
        }
    }

    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();

    let config = ExportedConfig {
        version: 1,
        exported_at: now,
        devices: exported_devices,
        allowed_routes: allowed_routes.to_vec(),
    };

    let json_bytes =
        serde_json::to_vec(&config).map_err(|e| ExportError::SerializationError(e.to_string()))?;

    let mut salt = [0u8; SALT_LEN];
    rand::thread_rng().fill_bytes(&mut salt);

    let mut nonce_bytes = [0u8; NONCE_LEN];
    rand::thread_rng().fill_bytes(&mut nonce_bytes);

    let key = derive_export_key(passphrase, &salt);
    let cipher = Aes256Gcm::new_from_slice(&key)
        .map_err(|e| ExportError::SerializationError(e.to_string()))?;
    let nonce = Nonce::from_slice(&nonce_bytes);

    let mut aad = Vec::with_capacity(5);
    aad.extend_from_slice(EXPORT_MAGIC);
    aad.push(EXPORT_VERSION);

    let ciphertext = cipher
        .encrypt(
            nonce,
            Payload {
                msg: &json_bytes,
                aad: &aad,
            },
        )
        .map_err(|_| ExportError::TamperedOrBadPassphrase)?;

    let mut out = Vec::with_capacity(aad.len() + SALT_LEN + NONCE_LEN + ciphertext.len());
    out.extend_from_slice(&aad);
    out.extend_from_slice(&salt);
    out.extend_from_slice(&nonce_bytes);
    out.extend_from_slice(&ciphertext);

    Ok(out)
}

/// Importa dispositivos a partir de bytes cifrados autenticados.
pub fn import_config_bytes(
    data: &[u8],
    passphrase: &str,
    store: &mut DeviceStore,
) -> Result<ImportResult, ExportError> {
    if data.len() < HEADER_LEN + 16 {
        return Err(ExportError::PayloadTooShort);
    }

    if &data[0..4] != EXPORT_MAGIC {
        return Err(ExportError::InvalidMagic);
    }

    let version = data[4];
    if version != EXPORT_VERSION {
        return Err(ExportError::UnsupportedVersion(version));
    }

    let mut offset = 5;
    let salt = &data[offset..offset + SALT_LEN];
    offset += SALT_LEN;
    let nonce_bytes = &data[offset..offset + NONCE_LEN];
    offset += NONCE_LEN;
    let ciphertext = &data[offset..];

    let key = derive_export_key(passphrase, salt);
    let cipher = Aes256Gcm::new_from_slice(&key)
        .map_err(|e| ExportError::SerializationError(e.to_string()))?;
    let nonce = Nonce::from_slice(nonce_bytes);

    let mut aad = Vec::with_capacity(5);
    aad.extend_from_slice(EXPORT_MAGIC);
    aad.push(EXPORT_VERSION);

    let plaintext = cipher
        .decrypt(
            nonce,
            Payload {
                msg: ciphertext,
                aad: &aad,
            },
        )
        .map_err(|_| ExportError::TamperedOrBadPassphrase)?;

    let config: ExportedConfig = serde_json::from_slice(&plaintext)
        .map_err(|e| ExportError::SerializationError(e.to_string()))?;

    let mut imported_ids = Vec::new();
    for dev in config.devices {
        let der = match URL_SAFE_NO_PAD.decode(&dev.public_der_b64) {
            Ok(d) => d,
            Err(e) => return Err(ExportError::InvalidDevice(e.to_string())),
        };

        if CryptoSuiteValidator::validate_p256_spki_der(&der).is_err() {
            continue;
        }

        let dev_id = DeviceId::from_public_der(&der);
        if dev_id.as_str() != dev.id {
            continue;
        }

        let record = DeviceRecord {
            id: dev_id.clone(),
            name: dev.name,
            public_der: der,
            status: DeviceStatus::Authorized,
            added_at: dev.added_at,
        };

        if store.register_device(record).is_ok() {
            imported_ids.push(dev_id.as_str().to_string());
        }
    }

    Ok(ImportResult {
        imported_devices: imported_ids.len(),
        devices: imported_ids,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample_authorized_device(_id_str: &str, name: &str) -> DeviceRecord {
        let secret = p256::SecretKey::random(&mut rand::thread_rng());
        let public_key = secret.public_key();
        use p256::pkcs8::EncodePublicKey;
        let doc = public_key.to_public_key_der().unwrap();
        let der = doc.as_bytes().to_vec();
        let dev_id = DeviceId::from_public_der(&der);

        DeviceRecord {
            id: dev_id,
            name: name.to_string(),
            public_der: der,
            status: DeviceStatus::Authorized,
            added_at: 1729500000,
        }
    }

    #[test]
    fn test_export_config_encryption_and_zero_private_key_leakage() {
        let dev1 = sample_authorized_device("dev1", "MARK Mobile 1");
        let dev2 = sample_authorized_device("dev2", "MARK Mobile 2");
        let devices = vec![dev1, dev2];
        let routes = vec!["/health".to_string(), "/v1/chat/completions".to_string()];

        let exported = export_config_bytes(&devices, &routes, "StrongPassphrase2026!").unwrap();

        assert!(exported.starts_with(EXPORT_MAGIC));
        assert!(exported.len() > 64);

        let as_str = String::from_utf8_lossy(&exported);
        assert!(!as_str.contains("private_key"));
        assert!(!as_str.contains("PRIVATE KEY"));
        assert!(!as_str.contains("\"devices\"")); // Deve estar cifrado
    }

    #[test]
    fn test_import_tampered_config_rejected() {
        let dev1 = sample_authorized_device("dev1", "MARK Mobile 1");
        let devices = vec![dev1];
        let routes = vec!["/health".to_string()];

        let mut exported = export_config_bytes(&devices, &routes, "MySecretPass!").unwrap();
        let mut store = DeviceStore::new();

        // 1. Senha errada
        assert_eq!(
            import_config_bytes(&exported, "WrongPass!", &mut store).unwrap_err(),
            ExportError::TamperedOrBadPassphrase
        );

        // 2. Adulteração do ciphertext
        let last_idx = exported.len() - 1;
        exported[last_idx] ^= 0xFF;
        assert_eq!(
            import_config_bytes(&exported, "MySecretPass!", &mut store).unwrap_err(),
            ExportError::TamperedOrBadPassphrase
        );
    }

    #[test]
    fn test_export_import_roundtrip_restores_devices() {
        let dev1 = sample_authorized_device("dev1", "MARK Mobile 1");
        let id1 = dev1.id.clone();
        let devices = vec![dev1];
        let routes = vec!["/health".to_string()];

        let exported = export_config_bytes(&devices, &routes, "RoundtripPass!").unwrap();
        let mut new_store = DeviceStore::new();

        let res = import_config_bytes(&exported, "RoundtripPass!", &mut new_store).unwrap();
        assert_eq!(res.imported_devices, 1);
        assert!(new_store.is_authorized(&id1));
    }
}

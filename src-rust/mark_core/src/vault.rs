//! Módulo de Cofre de Credenciais Criptografado (AES-256-GCM + Argon2id / HKDF + Zeroize).
//!
//! Garante conformidade e interoperabilidade entre MARK Desktop (Rust/Python),
//! Android (Kotlin/Java AES/GCM/NoPadding) e Wear OS.

use aes_gcm::{
    aead::{Aead, KeyInit, Payload},
    Aes256Gcm, Nonce,
};
use argon2::{Algorithm, Argon2, Params, Version};
use base64::{engine::general_purpose::STANDARD as B64, Engine};
use hkdf::Hkdf;
use rand::RngCore;
use serde::{Deserialize, Serialize};
use sha2::Sha256;
use zeroize::Zeroizing;

pub const VAULT_CIPHER: &str = "AES-256-GCM";
pub const KDF_ARGON2ID: &str = "Argon2id";
pub const KDF_HKDF: &str = "HKDF-SHA256";
pub const NONCE_SIZE_BYTES: usize = 12; // 96 bits NIST SP 800-38D
pub const TAG_SIZE_BYTES: usize = 16; // 128 bits
pub const KEY_SIZE_BYTES: usize = 32; // 256 bits

/// Estrutura padronizada de payload criptografado JSON/BSON interoperável com Desktop, Android e Wear OS.
#[derive(Serialize, Deserialize, Clone, Debug, PartialEq, Eq)]
pub struct EncryptedVaultPayload {
    pub version: u32,
    pub cipher: String,
    pub kdf: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub salt: Option<String>,
    pub nonce: String,
    pub ciphertext: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub tag: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub aad: Option<String>,
}

#[derive(Debug)]
pub enum VaultError {
    InvalidKeyLength(usize),
    InvalidNonceLength(usize),
    InvalidTagLength(usize),
    KeyDerivationFailed(String),
    EncryptionFailed(String),
    AuthenticationFailed(String),
    SerializationFailed(String),
    DeserializationFailed(String),
    CorruptedPayload(String),
}

impl std::fmt::Display for VaultError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            VaultError::InvalidKeyLength(len) => {
                write!(
                    f,
                    "Chave AES com tamanho inválido: {} bytes (esperado: 32 bytes / 256 bits)",
                    len
                )
            }
            VaultError::InvalidNonceLength(len) => {
                write!(
                    f,
                    "Nonce inválido: {} bytes (esperado: 12 bytes / 96 bits)",
                    len
                )
            }
            VaultError::InvalidTagLength(len) => {
                write!(
                    f,
                    "Tag de autenticação inválida: {} bytes (esperado: 16 bytes / 128 bits)",
                    len
                )
            }
            VaultError::KeyDerivationFailed(msg) => {
                write!(f, "Falha na derivação de chave: {}", msg)
            }
            VaultError::EncryptionFailed(msg) => {
                write!(f, "Falha na cifragem AES-256-GCM: {}", msg)
            }
            VaultError::AuthenticationFailed(msg) => {
                write!(f, "Falha de autenticação: dados adulterados, tag corrompida ou chave incorreta ({})", msg)
            }
            VaultError::SerializationFailed(msg) => {
                write!(f, "Falha na serialização do payload: {}", msg)
            }
            VaultError::DeserializationFailed(msg) => {
                write!(f, "Falha na desserialização do payload: {}", msg)
            }
            VaultError::CorruptedPayload(msg) => {
                write!(f, "Estrutura do payload corrompida: {}", msg)
            }
        }
    }
}

impl std::error::Error for VaultError {}

/// Gera um Nonce aleatório criptograficamente seguro de exatamente 96 bits (12 bytes).
pub fn generate_nonce_96() -> [u8; NONCE_SIZE_BYTES] {
    let mut nonce = [0u8; NONCE_SIZE_BYTES];
    rand::rngs::OsRng.fill_bytes(&mut nonce);
    nonce
}

/// Deriva uma chave de 256 bits usando Argon2id com parâmetros seguros.
pub fn derive_key_argon2id(
    password: &str,
    salt: &[u8],
) -> Result<Zeroizing<[u8; KEY_SIZE_BYTES]>, VaultError> {
    if salt.len() < 16 {
        return Err(VaultError::KeyDerivationFailed(
            "Salt para Argon2id deve possuir no mínimo 16 bytes.".to_string(),
        ));
    }

    // Configuração recomendada pelo RFC 9106 e OWASP para Argon2id
    let params = Params::new(19456, 2, 1, Some(KEY_SIZE_BYTES))
        .map_err(|e| VaultError::KeyDerivationFailed(e.to_string()))?;
    let argon2 = Argon2::new(Algorithm::Argon2id, Version::V0x13, params);

    let mut key = Zeroizing::new([0u8; KEY_SIZE_BYTES]);
    argon2
        .hash_password_into(password.as_bytes(), salt, &mut *key)
        .map_err(|e| VaultError::KeyDerivationFailed(e.to_string()))?;

    Ok(key)
}

/// Deriva subchaves usando HKDF-SHA256 a partir de um segredo mestre.
pub fn derive_key_hkdf(
    ikm: &[u8],
    salt: &[u8],
    info: &[u8],
    out_len: usize,
) -> Result<Zeroizing<Vec<u8>>, VaultError> {
    let hk = Hkdf::<Sha256>::new(Some(salt), ikm);
    let mut okm = Zeroizing::new(vec![0u8; out_len]);
    hk.expand(info, &mut okm)
        .map_err(|e| VaultError::KeyDerivationFailed(e.to_string()))?;
    Ok(okm)
}

/// Cifra um bloco de texto plano com AES-256-GCM, retornando o schema padronizado de payload.
pub fn encrypt_aes_256_gcm(
    key: &[u8],
    plaintext: &[u8],
    aad: Option<&[u8]>,
) -> Result<EncryptedVaultPayload, VaultError> {
    if key.len() != KEY_SIZE_BYTES {
        return Err(VaultError::InvalidKeyLength(key.len()));
    }

    let nonce_bytes = generate_nonce_96();
    let nonce = Nonce::from_slice(&nonce_bytes);

    let cipher =
        Aes256Gcm::new_from_slice(key).map_err(|e| VaultError::EncryptionFailed(e.to_string()))?;

    let payload = Payload {
        msg: plaintext,
        aad: aad.unwrap_or(b""),
    };

    let ciphertext_with_tag = cipher
        .encrypt(nonce, payload)
        .map_err(|e| VaultError::EncryptionFailed(e.to_string()))?;

    if ciphertext_with_tag.len() < TAG_SIZE_BYTES {
        return Err(VaultError::EncryptionFailed(
            "Cifra menor que o tamanho da tag de autenticação".to_string(),
        ));
    }

    let tag_offset = ciphertext_with_tag.len() - TAG_SIZE_BYTES;
    let tag_bytes = &ciphertext_with_tag[tag_offset..];

    Ok(EncryptedVaultPayload {
        version: 1,
        cipher: VAULT_CIPHER.to_string(),
        kdf: "NONE".to_string(),
        salt: None,
        nonce: B64.encode(nonce_bytes),
        ciphertext: B64.encode(&ciphertext_with_tag),
        tag: Some(B64.encode(tag_bytes)),
        aad: aad.map(|a| B64.encode(a)),
    })
}

/// Decifra e valida a integridade/autenticidade do payload com AES-256-GCM.
/// Garante que qualquer adulteração na tag, nos dados ou no Nonce rejeite o pacote imediatamente.
pub fn decrypt_aes_256_gcm(
    key: &[u8],
    payload: &EncryptedVaultPayload,
    aad_override: Option<&[u8]>,
) -> Result<Zeroizing<Vec<u8>>, VaultError> {
    if key.len() != KEY_SIZE_BYTES {
        return Err(VaultError::InvalidKeyLength(key.len()));
    }

    if payload.cipher != VAULT_CIPHER {
        return Err(VaultError::CorruptedPayload(format!(
            "Cifra '{}' incompatível (esperada: {})",
            payload.cipher, VAULT_CIPHER
        )));
    }

    let nonce_bytes = B64
        .decode(&payload.nonce)
        .map_err(|_| VaultError::InvalidNonceLength(0))?;
    if nonce_bytes.len() != NONCE_SIZE_BYTES {
        return Err(VaultError::InvalidNonceLength(nonce_bytes.len()));
    }
    let nonce = Nonce::from_slice(&nonce_bytes);

    let ciphertext_with_tag = B64
        .decode(&payload.ciphertext)
        .map_err(|e| VaultError::CorruptedPayload(format!("Base64 ciphertext inválido: {}", e)))?;

    if ciphertext_with_tag.len() < TAG_SIZE_BYTES {
        return Err(VaultError::CorruptedPayload(
            "Ciphertext truncado".to_string(),
        ));
    }

    // Se a tag foi especificada separadamente no payload, verifica se coincide com os últimos 16 bytes
    if let Some(ref tag_str) = payload.tag {
        let tag_bytes = B64
            .decode(tag_str)
            .map_err(|_| VaultError::InvalidTagLength(0))?;
        if tag_bytes.len() != TAG_SIZE_BYTES {
            return Err(VaultError::InvalidTagLength(tag_bytes.len()));
        }
        let embedded_tag = &ciphertext_with_tag[ciphertext_with_tag.len() - TAG_SIZE_BYTES..];
        if tag_bytes != embedded_tag {
            return Err(VaultError::AuthenticationFailed(
                "Tag de autenticação não confere com o payload".to_string(),
            ));
        }
    }

    // Resolve AAD
    let resolved_aad_vec = match aad_override {
        Some(a) => a.to_vec(),
        None => match &payload.aad {
            Some(aad_str) => B64
                .decode(aad_str)
                .unwrap_or_else(|_| aad_str.as_bytes().to_vec()),
            None => Vec::new(),
        },
    };

    let cipher = Aes256Gcm::new_from_slice(key)
        .map_err(|e| VaultError::AuthenticationFailed(e.to_string()))?;

    let payload_spec = Payload {
        msg: &ciphertext_with_tag,
        aad: &resolved_aad_vec,
    };

    let decrypted = cipher
        .decrypt(nonce, payload_spec)
        .map_err(|e| VaultError::AuthenticationFailed(format!("Falha na validação AEAD: {}", e)))?;

    // Envolve em Zeroizing para limpeza automática de memória RAM após uso
    Ok(Zeroizing::new(decrypted))
}

/// Função de alto nível para selar credenciais com derivação Argon2id e AES-256-GCM.
pub fn seal_credentials_argon2(
    passphrase: &str,
    plaintext: &str,
    aad: Option<&str>,
) -> Result<String, VaultError> {
    let mut salt = [0u8; 16];
    rand::rngs::OsRng.fill_bytes(&mut salt);

    let derived_key = derive_key_argon2id(passphrase, &salt)?;

    let aad_bytes = aad.map(|s| s.as_bytes());
    let mut payload = encrypt_aes_256_gcm(&*derived_key, plaintext.as_bytes(), aad_bytes)?;
    payload.kdf = KDF_ARGON2ID.to_string();
    payload.salt = Some(B64.encode(salt));

    serde_json::to_string(&payload).map_err(|e| VaultError::SerializationFailed(e.to_string()))
}

/// Função de alto nível para desselar credenciais com derivação Argon2id e validação completa.
pub fn unseal_credentials_argon2(
    passphrase: &str,
    payload_json: &str,
    aad_override: Option<&str>,
) -> Result<Zeroizing<String>, VaultError> {
    let payload: EncryptedVaultPayload = serde_json::from_str(payload_json)
        .map_err(|e| VaultError::DeserializationFailed(e.to_string()))?;

    let salt_str = payload.salt.as_ref().ok_or_else(|| {
        VaultError::CorruptedPayload("Salt ausente no payload com KDF Argon2id".to_string())
    })?;

    let salt_bytes = B64
        .decode(salt_str)
        .map_err(|e| VaultError::CorruptedPayload(format!("Salt Base64 inválido: {}", e)))?;

    let derived_key = derive_key_argon2id(passphrase, &salt_bytes)?;
    let aad_bytes = aad_override.map(|s| s.as_bytes());

    let decrypted_bytes = decrypt_aes_256_gcm(&*derived_key, &payload, aad_bytes)?;
    let decrypted_str = String::from_utf8(decrypted_bytes.to_vec()).map_err(|e| {
        VaultError::CorruptedPayload(format!("Texto plano decifrado não é UTF-8 válido: {}", e))
    })?;

    Ok(Zeroizing::new(decrypted_str))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_encryption_decryption_roundtrip() {
        let key = [0x42u8; KEY_SIZE_BYTES];
        let message = b"MARK-TOP-SECRET-CREDENTIAL-2026";
        let aad = b"MARK-DESKTOP-AUTH";

        let payload =
            encrypt_aes_256_gcm(&key, message, Some(aad)).expect("Cifragem deve ter sucesso");
        assert_eq!(payload.version, 1);
        assert_eq!(payload.cipher, "AES-256-GCM");
        assert!(payload.tag.is_some());

        let decrypted =
            decrypt_aes_256_gcm(&key, &payload, Some(aad)).expect("Decifragem deve ter sucesso");
        assert_eq!(&*decrypted, message);
    }

    #[test]
    fn test_corrupted_auth_tag_fails_authentication() {
        let key = [0x21u8; KEY_SIZE_BYTES];
        let message = b"Chave secreta para teste de adulteracao";
        let payload = encrypt_aes_256_gcm(&key, message, None).expect("Cifragem ok");

        // Adultera a tag
        let mut tampered_payload = payload.clone();
        let mut tag_bytes = B64.decode(payload.tag.unwrap()).unwrap();
        tag_bytes[0] ^= 0xFF; // Inverte 1 byte da tag
        tampered_payload.tag = Some(B64.encode(&tag_bytes));

        let res = decrypt_aes_256_gcm(&key, &tampered_payload, None);
        assert!(res.is_err(), "Decifragem com tag corrompida DEVE falhar");
        match res.unwrap_err() {
            VaultError::AuthenticationFailed(_) => (),
            other => panic!("Esperado AuthenticationFailed, obtido: {:?}", other),
        }
    }

    #[test]
    fn test_tampered_ciphertext_fails_authentication() {
        let key = [0x55u8; KEY_SIZE_BYTES];
        let message = b"Dados bancarios e chaves privadas";
        let payload = encrypt_aes_256_gcm(&key, message, None).expect("Cifragem ok");

        // Adultera o ciphertext
        let mut tampered = payload.clone();
        let mut ct_bytes = B64.decode(&payload.ciphertext).unwrap();
        ct_bytes[0] ^= 0xAA;
        tampered.ciphertext = B64.encode(&ct_bytes);

        let res = decrypt_aes_256_gcm(&key, &tampered, None);
        assert!(
            res.is_err(),
            "Decifragem com ciphertext alterado DEVE falhar"
        );
    }

    #[test]
    fn test_altered_key_fails_authentication() {
        let key1 = [0x11u8; KEY_SIZE_BYTES];
        let key2 = [0x22u8; KEY_SIZE_BYTES];
        let message = b"Confidencial";

        let payload = encrypt_aes_256_gcm(&key1, message, None).expect("Cifragem ok");
        let res = decrypt_aes_256_gcm(&key2, &payload, None);
        assert!(res.is_err(), "Chave incorreta DEVE falhar na autenticação");
    }

    #[test]
    fn test_invalid_nonce_length_rejected() {
        let key = [0x99u8; KEY_SIZE_BYTES];
        let message = b"Teste nonce";
        let mut payload = encrypt_aes_256_gcm(&key, message, None).expect("Cifragem ok");

        // Nonce com tamanho incorreto (8 bytes em vez de 12)
        payload.nonce = B64.encode([1u8; 8]);
        let res = decrypt_aes_256_gcm(&key, &payload, None);
        assert!(res.is_err());
        match res.unwrap_err() {
            VaultError::InvalidNonceLength(8) => (),
            other => panic!("Esperado InvalidNonceLength(8), obtido: {:?}", other),
        }
    }

    #[test]
    fn test_argon2id_derivation_and_roundtrip_seal() {
        let password = "senha_mestra_ultra_segura_mark_2026!";
        let secret = "{\"deepseek_api_key\": \"sk-test-12345\"}";
        let aad = "MARK-LLM-VAULT-V1";

        let sealed_json =
            seal_credentials_argon2(password, secret, Some(aad)).expect("Selagem com sucesso");
        assert!(sealed_json.contains("Argon2id"));
        assert!(sealed_json.contains("AES-256-GCM"));

        let unsealed =
            unseal_credentials_argon2(password, &sealed_json, Some(aad)).expect("Desselagem ok");
        assert_eq!(&*unsealed, secret);

        // Senha errada deve falhar
        let err = unseal_credentials_argon2("senha_incorreta", &sealed_json, Some(aad));
        assert!(err.is_err(), "Senha incorreta deve ser rejeitada");
    }

    #[test]
    fn test_hkdf_derivation() {
        let master = b"master_ikm_key_material_mark";
        let salt = b"salt_for_hkdf";
        let info = b"mark_sub_key_v1";

        let key1 = derive_key_hkdf(master, salt, info, 32).expect("HKDF ok");
        let key2 = derive_key_hkdf(master, salt, info, 32).expect("HKDF ok");
        assert_eq!(key1, key2);
        assert_eq!(key1.len(), 32);

        let key_different_info = derive_key_hkdf(master, salt, b"other_info", 32).expect("HKDF ok");
        assert_ne!(key1, key_different_info);
    }
}

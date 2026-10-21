package br.com.bannaman.markmobile

import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.security.KeyPairGenerator
import java.security.KeyStore
import java.security.MessageDigest
import java.security.Signature
import java.security.spec.ECGenParameterSpec

/** One non-exportable ECDSA key per Android installation. */
class DeviceIdentity {
    private val alias = "mark-mobile-device-v1"
    private fun pair(): java.security.KeyPair {
        val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        val existing = store.getEntry(alias, null) as? KeyStore.PrivateKeyEntry
        if (existing != null) return java.security.KeyPair(existing.certificate.publicKey, existing.privateKey)
        val generator = KeyPairGenerator.getInstance(KeyProperties.KEY_ALGORITHM_EC, "AndroidKeyStore")
        generator.initialize(
            KeyGenParameterSpec.Builder(alias, KeyProperties.PURPOSE_SIGN or KeyProperties.PURPOSE_VERIFY)
                .setAlgorithmParameterSpec(ECGenParameterSpec("secp256r1"))
                .setDigests(KeyProperties.DIGEST_SHA256)
                .build(),
        )
        return generator.generateKeyPair()
    }

    fun publicKey(): String = encoded(pair().public.encoded)
    fun id(): String = encoded(MessageDigest.getInstance("SHA-256").digest(pair().public.encoded))
    fun sign(message: ByteArray): String {
        val signer = Signature.getInstance("SHA256withECDSA")
        signer.initSign(pair().private)
        signer.update(message)
        return encoded(signer.sign())
    }

    private fun encoded(data: ByteArray): String =
        Base64.encodeToString(data, Base64.URL_SAFE or Base64.NO_PADDING or Base64.NO_WRAP)
}

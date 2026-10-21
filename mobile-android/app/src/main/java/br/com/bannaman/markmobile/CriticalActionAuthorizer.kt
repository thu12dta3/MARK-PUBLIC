package br.com.bannaman.markmobile

import android.hardware.biometrics.BiometricManager
import android.hardware.biometrics.BiometricPrompt
import android.os.CancellationSignal
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import java.security.KeyPair
import java.security.KeyPairGenerator
import java.security.KeyStore
import java.security.MessageDigest
import java.security.SecureRandom
import java.security.Signature
import java.security.spec.ECGenParameterSpec

/** A distinct, per-use Keystore key gates each local change to MARK's trust settings. */
internal class CriticalActionAuthorizer(private val activity: MainActivity) {
    private val alias = "mark-mobile-critical-v1"
    private var cancellation: CancellationSignal? = null

    fun cancel() {
        cancellation?.cancel()
        cancellation = null
    }

    fun authorize(action: String, context: String, onAuthorized: () -> Unit, onDenied: (String) -> Unit) {
        if (cancellation != null) {
            onDenied("Já existe uma confirmação de segurança em andamento.")
            return
        }
        val manager = activity.getSystemService(BiometricManager::class.java)
        if (manager.canAuthenticate(BiometricManager.Authenticators.BIOMETRIC_STRONG) !=
            BiometricManager.BIOMETRIC_SUCCESS) {
            onDenied("Esta ação exige biometria forte configurada no Android.")
            return
        }
        val keyPair: KeyPair
        val signer: Signature
        try {
            keyPair = keyPair()
            signer = Signature.getInstance("SHA256withECDSA").apply { initSign(keyPair.private) }
        } catch (_: Exception) {
            // In particular, do not silently replace a key invalidated by a biometric change.
            onDenied("A chave biométrica não está disponível. Confirme a recuperação no PC.")
            return
        }

        val nonce = ByteArray(32).also { SecureRandom().nextBytes(it) }
        val contextDigest = MessageDigest.getInstance("SHA-256")
            .digest(context.toByteArray(Charsets.UTF_8)).joinToString("") { "%02x".format(it) }
        val challenge = ("MARK-LOCAL-CRITICAL-V1\n$action\n$contextDigest\n" +
            nonce.joinToString("") { "%02x".format(it) }).toByteArray(Charsets.UTF_8)
        val signal = CancellationSignal()
        cancellation = signal
        try {
            BiometricPrompt.Builder(activity)
                .setTitle("Confirmar ação de segurança")
                .setSubtitle(action)
                .setDescription("A biometria autoriza somente esta alteração.")
                .setAllowedAuthenticators(BiometricManager.Authenticators.BIOMETRIC_STRONG)
                .setNegativeButton("Cancelar", activity.mainExecutor) { _, _ -> }
                .build()
                .authenticate(BiometricPrompt.CryptoObject(signer), signal, activity.mainExecutor,
                    object : BiometricPrompt.AuthenticationCallback() {
                        override fun onAuthenticationSucceeded(result: BiometricPrompt.AuthenticationResult) {
                            if (cancellation !== signal) return
                            cancellation = null
                            try {
                                val authorized = result.cryptoObject?.signature
                                    ?: error("Confirmação criptográfica ausente")
                                val proof = authorized.run { update(challenge); sign() }
                                val verifier = Signature.getInstance("SHA256withECDSA")
                                verifier.initVerify(keyPair.public)
                                verifier.update(challenge)
                                check(verifier.verify(proof)) { "Prova biométrica inválida" }
                                onAuthorized()
                            } catch (_: Exception) {
                                onDenied("Não foi possível confirmar esta ação com a chave biométrica.")
                            }
                        }

                        override fun onAuthenticationError(errorCode: Int, errString: CharSequence) {
                            if (cancellation !== signal) return
                            cancellation = null
                            onDenied("Confirmação biométrica cancelada ou indisponível.")
                        }
                    })
        } catch (_: Exception) {
            cancellation = null
            onDenied("Não foi possível iniciar a confirmação biométrica.")
        }
    }

    private fun keyPair(): KeyPair {
        val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        val existing = store.getEntry(alias, null) as? KeyStore.PrivateKeyEntry
        if (existing != null) return KeyPair(existing.certificate.publicKey, existing.privateKey)
        val generator = KeyPairGenerator.getInstance(KeyProperties.KEY_ALGORITHM_EC, "AndroidKeyStore")
        generator.initialize(KeyGenParameterSpec.Builder(alias, KeyProperties.PURPOSE_SIGN)
            .setAlgorithmParameterSpec(ECGenParameterSpec("secp256r1"))
            .setDigests(KeyProperties.DIGEST_SHA256)
            .setUserAuthenticationRequired(true)
            .setUserAuthenticationParameters(0, KeyProperties.AUTH_BIOMETRIC_STRONG)
            .setInvalidatedByBiometricEnrollment(true)
            .build())
        return generator.generateKeyPair()
    }
}

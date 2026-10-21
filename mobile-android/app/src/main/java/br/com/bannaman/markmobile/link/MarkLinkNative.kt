package br.com.bannaman.markmobile.link

import org.json.JSONObject

/**
 * Interface JNI para o núcleo nativo em Rust do MARK Link (libmark_link.so).
 * Fornece métodos seguros de alto nível para inicialização de cliente P2P/Tailscale-free,
 * processamento de QR challenge e telemetria de conectividade.
 */
object MarkLinkNative {
    init {
        try {
            System.loadLibrary("mark_link")
        } catch (e: UnsatisfiedLinkError) {
            System.err.println("MARK Link native library not loaded: ${e.message}")
        }
    }

    @JvmStatic
    external fun startClient(serverAddr: String, deviceId: String, sessionToken: String): String

    @JvmStatic
    external fun setQrChallenge(qrPayload: String): String

    @JvmStatic
    external fun getClientStatus(): String

    @JvmStatic
    external fun stopClient(): String

    @JvmStatic
    external fun ping(): String

    /**
     * Inicia o cliente MARK Link e retorna a resposta estruturada em JSON.
     */
    fun startClientJson(serverAddr: String, deviceId: String, sessionToken: String): JSONObject {
        val raw = startClient(serverAddr, deviceId, sessionToken)
        return JSONObject(raw)
    }

    /**
     * Registra desafio QR Code e retorna a resposta estruturada em JSON.
     */
    fun setQrChallengeJson(qrPayload: String): JSONObject {
        val raw = setQrChallenge(qrPayload)
        return JSONObject(raw)
    }

    /**
     * Obtém o status do cliente nativo em formato JSON.
     */
    fun getClientStatusJson(): JSONObject {
        val raw = getClientStatus()
        return JSONObject(raw)
    }
}

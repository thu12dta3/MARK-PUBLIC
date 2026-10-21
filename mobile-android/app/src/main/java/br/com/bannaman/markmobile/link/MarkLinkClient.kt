package br.com.bannaman.markmobile.link

import org.json.JSONObject

/**
 * Cliente de alto nível MARK Link para o aplicativo Android.
 * Substitui túneis externos e VPNs por conectividade direta P2P / NAT traversal via Rust.
 */
class MarkLinkClient(
    val serverAddress: String,
    val deviceId: String,
    val sessionToken: String
) {
    var isConnected: Boolean = false
        private set

    /**
     * Estabelece canal seguro com o Hub PC do MARK.
     */
    fun connect(): JSONObject {
        val res = MarkLinkNative.startClientJson(serverAddress, deviceId, sessionToken)
        isConnected = res.optString("status") == "connected"
        return res
    }

    /**
     * Envia o desafio de pareamento lido via QR Code da tela do PC.
     */
    fun submitQrChallenge(challengePayload: String): JSONObject {
        return MarkLinkNative.setQrChallengeJson(challengePayload)
    }

    /**
     * Consulta telemetria de conexão, latência e camada de transporte ativa.
     */
    fun status(): JSONObject {
        return MarkLinkNative.getClientStatusJson()
    }

    /**
     * Encerra a sessão ativa com o Hub PC.
     */
    fun disconnect(): JSONObject {
        val res = JSONObject(MarkLinkNative.stopClient())
        isConnected = false
        return res
    }
}

package br.com.bannaman.markmobile

import org.json.JSONArray
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URI
import java.security.MessageDigest
import java.util.UUID
import javax.net.ssl.HttpsURLConnection

class MarkApi(private val base: String, private val pin: String, private val identity: DeviceIdentity) {
    data class AgendaItem(val title: String, val due: String?, val completed: Boolean)
    private data class Session(val server: String, val pin: String, val device: String,
                               val token: String, val expiry: Long)
    companion object {
        private val sessionLock = Any()
        private var currentSession: Session? = null
        fun clearSession() = synchronized(sessionLock) { currentSession = null }

        fun normalizeServerAddress(raw: String): String {
            val uri = URI(raw.trim())
            require(uri.scheme == "https" && uri.host != null && uri.host.isNotBlank() &&
                uri.userInfo == null && uri.query == null && uri.fragment == null &&
                (uri.rawPath.isNullOrEmpty() || uri.rawPath == "/") &&
                (uri.port == -1 || uri.port in 1..65535)) {
                "Use apenas o endereço HTTPS do MARK no Tailscale, sem caminho ou parâmetros."
            }
            return uri.toASCIIString().trimEnd('/')
        }
    }

    private fun sessionToken(): String = synchronized(sessionLock) {
        val now = System.currentTimeMillis() / 1000
        val existing = currentSession
        if (existing != null && existing.server == base && existing.pin == pin &&
            existing.device == identity.id() && existing.expiry > now + 30) return@synchronized existing.token
        val result = response(open("/v1/session", "POST", signed = true, bootstrap = true))
        val token = result.getString("session_token")
        val expiry = result.getLong("expires_at")
        require(token.length in 32..128 && expiry > now && expiry <= now + 600) {
            "O MARK retornou uma sessão inválida."
        }
        currentSession = Session(base, pin, identity.id(), token, expiry)
        token
    }

    private fun forgetSession() = synchronized(sessionLock) { currentSession = null }
    private fun endpoint(path: String): java.net.URL {
        require(path.startsWith('/') && !path.startsWith("//") && '?' !in path && '#' !in path)
        return URI(normalizeServerAddress(base) + path).toURL()
    }

    private fun open(path: String, method: String, body: ByteArray = byteArrayOf(), signed: Boolean = true,
                     contentType: String = "application/json; charset=utf-8", bootstrap: Boolean = false,
                     extraHeaders: Map<String, String> = emptyMap()): HttpURLConnection {
        require(Regex("[a-f0-9]{64}").matches(pin)) { "Leia o QR de pareamento para verificar o PC." }
        val session = if (signed && !bootstrap) sessionToken() else null
        val connection = (endpoint(path).openConnection() as HttpsURLConnection).apply {
            sslSocketFactory = Tls13SocketFactory(pin)
            instanceFollowRedirects = false
            requestMethod = method
            connectTimeout = 15_000
            readTimeout = 240_000
            setRequestProperty("Accept", "application/json")
            setRequestProperty("Cache-Control", "no-store")
            extraHeaders.forEach { (name, value) -> setRequestProperty(name, value) }
            if (method == "POST") {
                doOutput = true
                setRequestProperty("Content-Type", contentType)
                setFixedLengthStreamingMode(body.size)
            }
        }
        if (signed) {
            val timestamp = (System.currentTimeMillis() / 1000).toString()
            val nonce = UUID.randomUUID().toString()
            val digest = MessageDigest.getInstance("SHA-256").digest(body).joinToString("") { "%02x".format(it) }
            val requestId = if (session != null) UUID.randomUUID().toString() else null
            val sessionDigest = session?.let {
                MessageDigest.getInstance("SHA-256").digest(it.toByteArray(Charsets.US_ASCII))
                    .joinToString("") { octet -> "%02x".format(octet) }
            }
            val canonical = if (session != null)
                "MARK-MOBILE-V2\n$method\n$path\n$digest\n$timestamp\n$nonce\n$requestId\n$sessionDigest"
            else "MARK-MOBILE-V1\n$method\n$path\n$digest\n$timestamp\n$nonce"
            connection.setRequestProperty("X-Mark-Device", identity.id())
            connection.setRequestProperty("X-Mark-Time", timestamp)
            connection.setRequestProperty("X-Mark-Nonce", nonce)
            if (session != null) {
                connection.setRequestProperty("X-Mark-Session", session)
                connection.setRequestProperty("X-Mark-Request-Id", requestId!!)
            }
            connection.setRequestProperty("X-Mark-Signature", identity.sign(canonical.toByteArray(Charsets.US_ASCII)))
        }
        connection.connect()
        val certificate = connection.serverCertificates.first().encoded
        val actual = MessageDigest.getInstance("SHA-256").digest(certificate).joinToString("") { "%02x".format(it) }
        if (actual != pin) {
            connection.disconnect()
            error("Identidade do servidor MARK mudou. Conexão bloqueada; confirme no computador e faça novo pareamento.")
        }
        return connection
    }

    private fun response(connection: HttpURLConnection, body: ByteArray = byteArrayOf()): JSONObject {
        try {
            if (connection.requestMethod == "POST") connection.outputStream.use { it.write(body) }
            val code = connection.responseCode
            val stream = if (code in 200..299) connection.inputStream else connection.errorStream
            val text = stream?.use { BoundedInput.read(it, 2_000_000).toString(Charsets.UTF_8) }.orEmpty()
            if (code == 401) {
                forgetSession()
                error("Sessão expirada ou aparelho não autorizado. Tente novamente; se persistir, confira o pareamento no computador.")
            }
            if (code !in 200..299) {
                val detail = runCatching { JSONObject(text).getJSONObject("error").getString("message") }.getOrNull()
                error(detail ?: "O MARK respondeu com erro HTTP $code.")
            }
            return JSONObject(text)
        } finally {
            connection.disconnect()
        }
    }

    fun requestPair(code: String, name: String = "MARK Mobile"): String {
        val proof = identity.sign("MARK-PAIR-V1\n$code".toByteArray(Charsets.UTF_8))
        val bytes = JSONObject().put("code", code).put("name", name)
            .put("public_key", identity.publicKey()).put("proof", proof).toString().toByteArray(Charsets.UTF_8)
        return response(open("/v1/pair/enroll", "POST", bytes, signed = false), bytes).getString("device_id")
    }

    fun pairStatus(): String {
        val connection = open("/v1/pair/status", "GET", signed = false,
                              extraHeaders = mapOf("X-Mark-Device" to identity.id()))
        return response(connection).getString("status")
    }

    fun check(): String {
        response(open("/health", "GET"))
        return "Conectado ao MARK"
    }

    data class HistoryTurn(val user: String, val assistant: String, val source: String)

    fun history(): List<HistoryTurn> {
        val items = response(open("/v1/history", "GET")).getJSONArray("items")
        return (0 until items.length()).mapNotNull { index ->
            val item = items.optJSONObject(index) ?: return@mapNotNull null
            val user = item.optString("user").takeIf(String::isNotBlank) ?: return@mapNotNull null
            val assistant = item.optString("assistant").takeIf(String::isNotBlank) ?: return@mapNotNull null
            HistoryTurn(user, assistant, item.optString("source"))
        }
    }

    fun agenda(): List<AgendaItem> {
        val items = response(open("/v1/agenda", "GET")).getJSONArray("items")
        return (0 until items.length()).mapNotNull { index ->
            val item = items.optJSONObject(index) ?: return@mapNotNull null
            AgendaItem(item.optString("title"), item.optString("due_at").takeIf(String::isNotBlank),
                item.optBoolean("completed"))
        }
    }

    fun ask(question: String, quick: Boolean = false, attachment: ChatAttachment? = null, origin: String = "mobile"): String {
        require(origin in setOf("mobile", "wear"))
        val content: Any = if (attachment?.imageDataUrl != null) {
            JSONArray().put(JSONObject().put("type", "text").put("text", question))
                .put(JSONObject().put("type", "image_url")
                    .put("image_url", JSONObject().put("url", attachment.imageDataUrl)))
        } else question
        val message = JSONObject().put("role", "user").put("content", content)
        val payload = JSONObject().put("model", if (quick) "mark-call" else "mark")
            .put("stream", false).put("messages", JSONArray().put(message))
        if (origin == "wear") payload.put("mark_origin", "wear")
        if (attachment?.text != null) {
            payload.put("mark_attachments", JSONArray().put(JSONObject()
                .put("name", attachment.name).put("text", attachment.text)))
        }
        val bytes = payload.toString().toByteArray(Charsets.UTF_8)
        return response(open("/v1/chat/completions", "POST", bytes), bytes)
            .getJSONArray("choices").getJSONObject(0).getJSONObject("message").getString("content")
    }

    fun wearSummary(): JSONObject = response(open("/v1/wear/summary", "GET"))

    fun wearTelemetry(payload: JSONObject): JSONObject {
        val bytes = payload.toString().toByteArray(Charsets.UTF_8)
        require(bytes.size <= 8192)
        return response(open("/v1/wear/telemetry", "POST", bytes), bytes)
    }

    fun voiceTurn(pcm: ByteArray): Pair<ByteArray, Boolean> {
        val connection = open("/v1/voice/turn", "POST", pcm, contentType = "application/octet-stream")
        try {
            connection.outputStream.use { it.write(pcm) }
            val code = connection.responseCode
            if (code !in 200..299) {
                if (code == 401) forgetSession()
                val message = connection.errorStream?.use { BoundedInput.read(it, 64_000).toString(Charsets.UTF_8) }.orEmpty()
                val detail = runCatching { JSONObject(message).getJSONObject("error").getString("message") }.getOrNull()
                error(detail ?: "Chamada falhou (HTTP $code).")
            }
            require(connection.contentType?.substringBefore(';')?.trim() in setOf("audio/wav", "audio/x-wav")) {
                "O MARK retornou um formato de áudio inesperado."
            }
            val audio = connection.inputStream.use { BoundedInput.read(it, 16_000_000) }
            require(audio.size >= 44 && audio.copyOfRange(0, 4).contentEquals("RIFF".toByteArray()) &&
                audio.copyOfRange(8, 12).contentEquals("WAVE".toByteArray())) { "Áudio inválido." }
            return audio to (connection.getHeaderField("X-Mark-Sleep") == "1")
        } finally {
            connection.disconnect()
        }
    }
}

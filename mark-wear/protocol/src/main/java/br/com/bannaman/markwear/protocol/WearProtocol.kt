package br.com.bannaman.markwear.protocol

import org.json.JSONObject
import java.util.UUID

/** Small, versioned transport; carries no server credentials or raw audio. */
object WearProtocol {
    const val REQUEST = "/mark/wear/v1/request"
    const val RESPONSE = "/mark/wear/v1/response"
    const val MAX_BYTES = 8192
    const val MAX_TEXT = 2000
    val kinds = setOf("status", "ask", "telemetry", "reply", "error", "manage")
    fun validId(id: String): Boolean = Regex("[a-f0-9]{32}").matches(id)
    fun validText(text: String): Boolean = text.isNotBlank() && text.length <= MAX_TEXT &&
        text.none { it.code < 32 && it != '\n' && it != '\t' }
    fun encode(kind: String, payload: JSONObject, id: String = UUID.randomUUID().toString().replace("-", "")): ByteArray {
        require(kind in kinds && validId(id))
        val bytes = JSONObject().put("v", 1).put("id", id).put("kind", kind)
            .put("at", System.currentTimeMillis()).put("payload", payload).toString().toByteArray(Charsets.UTF_8)
        require(bytes.size <= MAX_BYTES)
        return bytes
    }
    data class Packet(val id: String, val kind: String, val payload: JSONObject)
    fun decode(bytes: ByteArray): Packet {
        require(bytes.size in 2..MAX_BYTES)
        val text = Charsets.UTF_8.newDecoder().onMalformedInput(java.nio.charset.CodingErrorAction.REPORT)
            .decode(java.nio.ByteBuffer.wrap(bytes)).toString()
        val o = JSONObject(text)
        require(o.length() == 5 && o.get("v") == 1)
        val id = o.get("id") as? String ?: error("id inválido")
        val kind = o.get("kind") as? String ?: error("tipo inválido")
        require(validId(id) && kind in kinds)
        val stamp = o.get("at") as? Number ?: error("horário inválido")
        val now = System.currentTimeMillis()
        require(stamp is Long || stamp is Int)
        require(stamp.toLong() in (now-300_000)..(now+300_000))
        return Packet(id, kind, o.getJSONObject("payload"))
    }
}

/** Bounds and idempotency independent of Android lifecycle and network. */
class RequestGate(private val limit: Int = 64) {
    private val seen = LinkedHashMap<String, Long>()
    @Synchronized fun accept(id: String, now: Long): Boolean {
        require(WearProtocol.validId(id))
        seen.entries.removeAll { now - it.value > 300_000 }
        if (seen.containsKey(id) || seen.size >= limit) return false
        seen[id] = now; return true
    }
}

enum class PowerMode { SMART, ULTRA_SAVE, ACTIVE }
object WearPower {
    fun sensorTimeout(mode: PowerMode): Long = if (mode == PowerMode.ULTRA_SAVE) 8000 else 15000
    fun optionalSync(mode: PowerMode, battery: Int): Boolean = mode != PowerMode.ULTRA_SAVE && battery >= 15
    fun backoff(attempt: Int): Long = (2000L shl attempt.coerceIn(0, 5)).coerceAtMost(60000)
}

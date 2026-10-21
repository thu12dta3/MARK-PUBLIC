package br.com.bannaman.markwear.protocol
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
class WearPacketTest {
    private val id="a".repeat(32)
    @Test fun roundTripStatusAskAndError() {
        for(kind in listOf("status","ask","telemetry","reply","error")) {
            val packet=WearProtocol.decode(WearProtocol.encode(kind,JSONObject().put("text","Olá"),id))
            assertEquals(kind,packet.kind);assertEquals(id,packet.id);assertEquals("Olá",packet.payload.getString("text"))
        }
    }
    @Test fun malformedOversizedAndUnsupported() {
        for(bytes in listOf(byteArrayOf(0xc3.toByte(),0x28),ByteArray(8193),"{}".toByteArray(),
            WearProtocol.encode("status",JSONObject(),id).toString(Charsets.UTF_8).replace("\"v\":1","\"v\":2").toByteArray())) {
            assertTrue(runCatching {WearProtocol.decode(bytes)}.isFailure)
        }
        assertTrue(runCatching {WearProtocol.encode("admin",JSONObject(),id)}.isFailure)
        assertTrue(runCatching {WearProtocol.encode("ask",JSONObject().put("text","x".repeat(8192)),id)}.isFailure)
    }
    @Test fun staleAndOverflowDatesDenied() {
        for(stamp in listOf(0L,Long.MIN_VALUE,Long.MAX_VALUE)) {
            val o=JSONObject(WearProtocol.encode("status",JSONObject(),id).toString(Charsets.UTF_8)).put("at",stamp)
            assertTrue(runCatching {WearProtocol.decode(o.toString().toByteArray())}.isFailure)
        }
    }
}

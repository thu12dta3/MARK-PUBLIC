package br.com.bannaman.markwear.protocol
import org.junit.Assert.*
import org.junit.Test
class WearPolicyTest {
    @Test fun idTextBounds() {
        assertTrue(WearProtocol.validId("a".repeat(32)))
        assertFalse(WearProtocol.validId("../secret"))
        assertFalse(WearProtocol.validText("x".repeat(2001)))
        assertFalse(WearProtocol.validText("a\u0000b"))
        assertTrue(WearProtocol.validText("Olá MARK"))
    }
    @Test fun replayAndCapacity() {
        val g=RequestGate(2)
        assertTrue(g.accept("a".repeat(32),0)); assertFalse(g.accept("a".repeat(32),1))
        assertTrue(g.accept("b".repeat(32),1)); assertFalse(g.accept("c".repeat(32),2))
        assertTrue(g.accept("c".repeat(32),300002))
    }
    @Test fun powerIsBounded() {
        assertFalse(WearPower.optionalSync(PowerMode.ULTRA_SAVE,100))
        assertFalse(WearPower.optionalSync(PowerMode.SMART,10))
        assertTrue(WearPower.optionalSync(PowerMode.SMART,40))
        assertEquals(60000,WearPower.backoff(100).toInt())
        assertTrue(WearPower.sensorTimeout(PowerMode.ACTIVE)<=15000)
    }
}

package br.com.bannaman.markwear.protocol
import org.junit.Test
import org.junit.Assert.*
import java.io.*
import java.security.KeyPairGenerator
class SecureBluetoothFramesTest {
    private fun pair():Pair<SecureBluetoothFrames,SecureBluetoothFrames> {
        val a=SecureBluetoothFrames.newKey();val b=SecureBluetoothFrames.newKey()
        return SecureBluetoothFrames(true,a,b.public.encoded) to SecureBluetoothFrames(false,b,a.public.encoded)
    }
    @Test fun sameCodeBidirectionalAndReplayDenied() {
        val(a,b)=pair();assertEquals(a.code,b.code)
        val frame=a.seal("hello".toByteArray());assertEquals("hello",String(b.open(frame)))
        assertTrue(runCatching {b.open(frame)}.isFailure)
        assertEquals("reply",String(a.open(b.seal("reply".toByteArray()))))
    }
    @Test fun tamperingAndDirectionDeniedWithoutAdvancingCounter() {
        val(a,b)=pair();val frame=a.seal("hello".toByteArray());val bad=frame.copyOf();bad[bad.lastIndex]=(bad.last().toInt() xor 1).toByte()
        assertTrue(runCatching {b.open(bad)}.isFailure)
        assertTrue(runCatching {a.open(frame)}.isFailure)
        assertEquals("hello",String(b.open(frame)))
    }
    @Test fun peerKeyAndFrameBounds() {
        val a=SecureBluetoothFrames.newKey();val rsa=KeyPairGenerator.getInstance("RSA").apply {initialize(2048)}.generateKeyPair()
        assertTrue(runCatching {SecureBluetoothFrames(true,a,rsa.public.encoded)}.isFailure)
        val(aes,_)=pair();assertTrue(runCatching {aes.seal(ByteArray(8194))}.isFailure)
        val out=ByteArrayOutputStream();DataOutputStream(out).writeInt(Int.MAX_VALUE)
        assertTrue(runCatching {SecureBluetoothFrames.readPlain(ByteArrayInputStream(out.toByteArray()))}.isFailure)
    }
    @Test fun connectionIsolationAndTruncatedFrames() {
        val(a,b)=pair();val(_,other)=pair()
        assertTrue(runCatching {other.open(a.seal(byteArrayOf(1)))}.isFailure)
        assertTrue(runCatching {b.open(ByteArray(10))}.isFailure)
        assertTrue(runCatching {SecureBluetoothFrames.readPlain(ByteArrayInputStream(byteArrayOf(0,0,0,4,1)))}.isFailure)
    }
}

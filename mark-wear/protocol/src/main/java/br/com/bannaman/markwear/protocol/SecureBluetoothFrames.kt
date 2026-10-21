package br.com.bannaman.markwear.protocol

import java.io.*
import java.nio.ByteBuffer
import java.security.*
import java.security.interfaces.ECPublicKey
import java.security.spec.*
import javax.crypto.*
import javax.crypto.spec.*

/** Ephemeral P-256/AES-GCM channel. Owner must compare SAS on both screens. */
class SecureBluetoothFrames(private val phone: Boolean,local: KeyPair,peerDer: ByteArray) {
    companion object {
        const val MAX_FRAME=WearProtocol.MAX_BYTES+25
        fun newKey(): KeyPair=KeyPairGenerator.getInstance("EC").apply {initialize(ECGenParameterSpec("secp256r1"))}.generateKeyPair()
        fun writePlain(out: OutputStream,bytes: ByteArray,max: Int=MAX_FRAME) {
            require(bytes.size in 1..max);DataOutputStream(out).apply {writeInt(bytes.size);write(bytes);flush()}
        }
        fun readPlain(input: InputStream,max: Int=MAX_FRAME): ByteArray {
            val stream=DataInputStream(input);val size=stream.readInt();require(size in 1..max)
            return ByteArray(size).also {stream.readFully(it)}
        }
        private fun sha(vararg values: ByteArray): ByteArray=MessageDigest.getInstance("SHA-256").run {values.forEach {update(it)};digest()}
    }
    private val txKey: ByteArray
    private val rxKey: ByteArray
    private var destroyed=false
    private var txSeq=0L
    private var rxSeq=0L
    val code: String
    init {
        require(peerDer.size in 32..256)
        val peer=KeyFactory.getInstance("EC").generatePublic(X509EncodedKeySpec(peerDer)) as ECPublicKey
        val own=local.public as ECPublicKey
        require(peer.params.curve==own.params.curve && peer.params.generator==own.params.generator && peer.params.order==own.params.order && peer.params.cofactor==own.params.cofactor)
        val phonePub=if(phone)own.encoded else peer.encoded
        val watchPub=if(phone)peer.encoded else own.encoded
        val context=sha("MARK-BT-V1".toByteArray(),phonePub,watchPub)
        code="%06d".format((ByteBuffer.wrap(context).int.toLong() and 0xffffffffL)%1000000)
        val agreement=KeyAgreement.getInstance("ECDH").apply {init(local.private);doPhase(peer,true)}
        val secret=agreement.generateSecret()
        // HKDF-SHA256 with transcript hash as salt and directional info.
        val extract=Mac.getInstance("HmacSHA256").apply {init(SecretKeySpec(context,"HmacSHA256"))}.doFinal(secret)
        fun key(info: String)=Mac.getInstance("HmacSHA256").apply {init(SecretKeySpec(extract,"HmacSHA256"))}.doFinal(info.toByteArray()+byteArrayOf(1))
        txKey=key(if(phone)"MARK-BT-V1 PHONE TO WATCH" else "MARK-BT-V1 WATCH TO PHONE")
        rxKey=key(if(phone)"MARK-BT-V1 WATCH TO PHONE" else "MARK-BT-V1 PHONE TO WATCH")
        secret.fill(0);extract.fill(0)
    }
    private fun nonce(seq: Long)=ByteBuffer.allocate(12).putInt(0x4d425431).putLong(seq).array()
    @Synchronized fun seal(plain: ByteArray): ByteArray {
        require(!destroyed && plain.size in 1..(WearProtocol.MAX_BYTES+1) && txSeq<100000)
        val seq=txSeq++;val counter=ByteBuffer.allocate(8).putLong(seq).array()
        val cipher=Cipher.getInstance("AES/GCM/NoPadding").apply {init(Cipher.ENCRYPT_MODE,SecretKeySpec(txKey,"AES"),GCMParameterSpec(128,nonce(seq)));updateAAD(counter)}
        return counter+cipher.doFinal(plain)
    }
    @Synchronized fun open(frame: ByteArray): ByteArray {
        require(!destroyed && frame.size in 25..MAX_FRAME)
        val counter=frame.copyOfRange(0,8);val seq=ByteBuffer.wrap(counter).long
        require(seq==rxSeq && rxSeq<100000)
        val cipher=Cipher.getInstance("AES/GCM/NoPadding").apply {init(Cipher.DECRYPT_MODE,SecretKeySpec(rxKey,"AES"),GCMParameterSpec(128,nonce(seq)));updateAAD(counter)}
        val result=cipher.doFinal(frame.copyOfRange(8,frame.size));rxSeq++;return result
    }
    @Synchronized fun destroy() {destroyed=true;txKey.fill(0);rxKey.fill(0)}
}

package br.com.bannaman.markwear.protocol

import android.bluetooth.*
import java.util.UUID
import java.util.concurrent.*
import java.util.concurrent.atomic.AtomicBoolean

/** Official secure RFCOMM, bonded peer only; one foreground session, no polling. */
class BondedWearLink(private val phone: Boolean,
    onCode:(String)->Unit, onReady:()->Unit,
    onPacket:(ByteArray)->Unit,onClosed:()->Unit,
    private val serviceId:UUID=UUID_SERVICE) {
    @Volatile private var codeCallback=onCode
    @Volatile private var readyCallback=onReady
    @Volatile private var packetCallback=onPacket
    @Volatile private var closeCallback=onClosed
    @Synchronized fun adopt(onPacket:(ByteArray)->Unit,onClosed:()->Unit) {
        require(isReady());codeCallback={};readyCallback={};packetCallback=onPacket;closeCallback=onClosed
    }
    companion object {val UUID_SERVICE: UUID=UUID.fromString("d9a8cc23-9865-427e-8ec2-f3a91b7e26dd")}
    @Volatile private var socket:BluetoothSocket?=null
    @Volatile private var server:BluetoothServerSocket?=null
    @Volatile private var frames:SecureBluetoothFrames?=null
    private val sender=ThreadPoolExecutor(1,1,0,TimeUnit.SECONDS,ArrayBlockingQueue<Runnable>(4),{r->Thread(r,"mark-bt-write")})
    private val closed=AtomicBoolean(false)
    private val localConfirmed=AtomicBoolean(false)
    private val remoteConfirmed=AtomicBoolean(false)
    private val ready=AtomicBoolean(false)
    fun isReady()=ready.get() && !closed.get()
    @Suppress("MissingPermission") fun connect(device:BluetoothDevice) {
        require(!phone && device.bondState==BluetoothDevice.BOND_BONDED)
        Thread({try {
            val s=device.createRfcommSocketToServiceRecord(serviceId);socket=s
            if(closed.get()){s.close();return@Thread}
            s.connect();exchange(s)
        }catch(_:Exception){close()}},"mark-bt-connect").start()
    }
    @Suppress("MissingPermission") fun listen(adapter:BluetoothAdapter,address:String) {
        require(phone)
        Thread({try {
            val listener=adapter.listenUsingRfcommWithServiceRecord("MARK Wear",serviceId);server=listener
            if(closed.get()){listener.close();return@Thread}
            val s=listener.accept();socket=s;listener.close();server=null
            require(s.remoteDevice.address==address && s.remoteDevice.bondState==BluetoothDevice.BOND_BONDED)
            exchange(s)
        }catch(_:Exception){close()}},"mark-bt-listen").start()
    }
    private fun exchange(s:BluetoothSocket) {
        val key=SecureBluetoothFrames.newKey();val peer:ByteArray
        if(phone) {peer=SecureBluetoothFrames.readPlain(s.inputStream,256);SecureBluetoothFrames.writePlain(s.outputStream,key.public.encoded,256)}
        else {SecureBluetoothFrames.writePlain(s.outputStream,key.public.encoded,256);peer=SecureBluetoothFrames.readPlain(s.inputStream,256)}
        val channel=SecureBluetoothFrames(phone,key,peer);frames=channel
        if(closed.get()){channel.destroy();return}
        codeCallback(channel.code)
        while(!closed.get()) {
            val clear=channel.open(SecureBluetoothFrames.readPlain(s.inputStream))
            when(clear[0].toInt()) {
                1->{require(clear.size==1 && remoteConfirmed.compareAndSet(false,true));checkReady()}
                2->{require(isReady() && clear.size>1);deliverPacket(clear.copyOfRange(1,clear.size))}
                else->error("Unsupported frame")
            }
        }
    }
    @Synchronized private fun deliverPacket(bytes:ByteArray){packetCallback(bytes)}
    private fun checkReady() {if(localConfirmed.get() && remoteConfirmed.get() && ready.compareAndSet(false,true))readyCallback()}
    fun confirm() {
        require(frames!=null && !closed.get())
        if(localConfirmed.compareAndSet(false,true)){enqueue(byteArrayOf(1));checkReady()}
    }
    fun send(bytes:ByteArray) {require(isReady() && bytes.size<=WearProtocol.MAX_BYTES);enqueue(byteArrayOf(2)+bytes)}
    private fun enqueue(bytes:ByteArray) {
        require(!closed.get());val copy=bytes.copyOf()
        try {sender.execute {try {write(copy)}catch(_:Exception){close()}}}
        catch(_:RejectedExecutionException){close();throw IllegalStateException("Bluetooth busy or closed")}
    }
    @Synchronized private fun write(bytes:ByteArray) {
        require(!closed.get());val channel=frames?:error("Not connected");val s=socket?:error("Not connected")
        SecureBluetoothFrames.writePlain(s.outputStream,channel.seal(bytes))
    }
    fun close() {
        if(!closed.compareAndSet(false,true))return
        ready.set(false);sender.shutdownNow();runCatching {server?.close()};runCatching {socket?.close()};frames?.destroy();frames=null
        closeCallback()
    }
}

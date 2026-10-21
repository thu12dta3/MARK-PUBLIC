package br.com.bannaman.markmobile

import android.app.*
import android.content.*
import android.content.pm.ServiceInfo
import android.os.*
import br.com.bannaman.markwear.protocol.*
import org.json.JSONObject
import java.util.concurrent.*

/** Holds only the explicit, biometric-approved Bluetooth lease; never starts at boot. */
class WearPhoneForeground:Service() {
    companion object {
        private data class Grant(val link:BondedWearLink,val expiry:Long,val packets:ArrayBlockingQueue<ByteArray>,val started:()->Unit)
        private val guard=Any()
        private var pending:Grant?=null
        const val STOP="br.com.bannaman.markmobile.STOP_WEAR_LEASE"
        fun start(context:Context,link:BondedWearLink,expiry:Long,onStarted:()->Unit) {
            require(link.isReady() && expiry>SystemClock.elapsedRealtime() && expiry<=SystemClock.elapsedRealtime()+300000)
            val queue=ArrayBlockingQueue<ByteArray>(2)
            link.adopt({data->if(!queue.offer(data.copyOf()))link.close()},{})
            synchronized(guard) {pending?.link?.close();pending=Grant(link,expiry,queue,onStarted)}
            try {context.startForegroundService(Intent(context,WearPhoneForeground::class.java))}
            catch(e:Exception){synchronized(guard){pending?.link?.close();pending=null};throw e}
        }
        fun stop(context:Context) {context.stopService(Intent(context,WearPhoneForeground::class.java))}
    }
    private val handler=Handler(Looper.getMainLooper())
    private var link:BondedWearLink?=null
    @Volatile private var deadline=0L
    @Volatile private var generation=0L
    private val gate=RequestGate()
    private val work=ThreadPoolExecutor(1,1,0,TimeUnit.SECONDS,ArrayBlockingQueue<Runnable>(2))
    override fun onBind(intent:Intent?)=null
    override fun onStartCommand(intent:Intent?,flags:Int,startId:Int):Int {
        if(intent?.action==STOP){stopSelf();return START_NOT_STICKY}
        val grant=synchronized(guard) {pending.also {pending=null}}
        if(grant==null){stopSelf();return START_NOT_STICKY}
        if(!grant.link.isReady() || grant.expiry<=SystemClock.elapsedRealtime()){grant.link.close();stopSelf();return START_NOT_STICKY}
        generation++;link?.close();link=grant.link;deadline=grant.expiry
        val manager=getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(NotificationChannel("mark-wear-lease","Conexão MARK Wear",NotificationManager.IMPORTANCE_LOW))
        val stop=PendingIntent.getService(this,9,Intent(this,WearPhoneForeground::class.java).setAction(STOP),PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        val note=Notification.Builder(this,"mark-wear-lease").setSmallIcon(br.com.bannaman.markmobile.R.drawable.mark_icon)
            .setContentTitle("MARK Wear conectado").setContentText("Sessão autorizada por até cinco minutos. Microfone desligado.")
            .setOngoing(true).addAction(Notification.Action.Builder(null,"Encerrar",stop).build()).build()
        if(Build.VERSION.SDK_INT>=29)startForeground(81,note,ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE) else startForeground(81,note)
        val current=generation
        handler.removeCallbacksAndMessages(null)
        grant.link.adopt({bytes->dispatch(bytes,current)},{handler.post {if(generation==current)stopSelf()}})
        while(true){val bytes=grant.packets.poll()?:break;dispatch(bytes,current)}
        grant.started()
        handler.postDelayed({stopSelf()},(deadline-SystemClock.elapsedRealtime()).coerceAtLeast(1))
        return START_NOT_STICKY
    }
    private fun valid(generation:Long)=this.generation==generation && deadline>SystemClock.elapsedRealtime() && link?.isReady()==true
    private fun dispatch(bytes:ByteArray,generation:Long) {
        if(!valid(generation))return
        val packet=runCatching {WearProtocol.decode(bytes)}.getOrNull()?:return
        if(!gate.accept(packet.id,SystemClock.elapsedRealtime()))return
        try {work.execute {
            if(!valid(generation))return@execute
            val result=runCatching {
                val settings=SecureSettings(applicationContext)
                val api=MarkApi(settings.serverUrl(),settings.serverPin(),DeviceIdentity())
                when(packet.kind) {
                    "status"->{require(packet.payload.length()==0 || (packet.payload.length()==1 && packet.payload.opt("telemetry") is Boolean));api.check()
                        val b=registerReceiver(null,IntentFilter(Intent.ACTION_BATTERY_CHANGED))
                        JSONObject().put("pc","ONLINE").put("service","API disponível").put("phone_battery",b?.getIntExtra(BatteryManager.EXTRA_LEVEL,-1)?:-1)
                            .apply {if(packet.payload.optBoolean("telemetry",false))put("telemetry",api.wearSummary())}}
                    "ask"->{require(packet.payload.length()==1);val text=packet.payload.get("text") as? String?:error("Invalid text");require(WearProtocol.validText(text))
                        JSONObject().put("text",api.ask(text,quick=true,origin="wear").take(WearProtocol.MAX_TEXT))}
                    "telemetry"->{require(packet.payload.length()==1);api.wearTelemetry(packet.payload)}
                    else->error("Unsupported request")
                }
            }
            if(!valid(generation))return@execute
            val payload=if(result.isSuccess)WearProtocol.encode("reply",result.getOrThrow(),packet.id)
                else WearProtocol.encode("error",JSONObject().put("code","PC_UNAVAILABLE_OR_REQUEST_DENIED"),packet.id)
            runCatching {link?.send(payload)}
        }}catch(_:RejectedExecutionException){if(valid(generation))runCatching {link?.send(WearProtocol.encode("error",JSONObject().put("code","BUSY"),packet.id))}}
    }
    override fun onDestroy() {
        generation++;deadline=0;handler.removeCallbacksAndMessages(null);link?.close();link=null;work.shutdownNow();super.onDestroy()
    }
}

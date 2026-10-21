package br.com.bannaman.markwear

import android.app.*
import android.content.*
import android.content.pm.ServiceInfo
import android.os.*
import br.com.bannaman.markwear.protocol.*
import java.lang.ref.WeakReference

/** Owns only an already approved RFCOMM lease, never microphone/sensors or a boot grant. */
class WearLinkService:Service() {
    interface Listener {
        fun onWearPacket(link:BondedWearLink,bytes:ByteArray)
        fun onWearClosed(link:BondedWearLink)
    }
    companion object {
        private data class Grant(val link:BondedWearLink,val lease:WearLease,val owner:WeakReference<Listener>)
        private val guard=Any()
        private var pending:Grant?=null
        @Volatile private var current:WearLinkService?=null
        private const val STOP="br.com.bannaman.markwear.STOP_WEAR_LINK"
        fun retain(context:Context,link:BondedWearLink,owner:Listener) {
            require(link.isReady())
            val now=SystemClock.elapsedRealtime()
            synchronized(guard){check(pending==null && current?.link!==link);pending=Grant(link,WearLease.issue(now,now+WearLease.MAX_MS),WeakReference(owner))}
            try {
                val intent=Intent(context,WearLinkService::class.java)
                if(Build.VERSION.SDK_INT>=26)context.startForegroundService(intent)else context.startService(intent)
            }catch(e:Exception){synchronized(guard){if(pending?.link===link)pending=null};link.close();throw e}
        }
        fun holds(link:BondedWearLink?)=link!=null && synchronized(guard){pending?.link===link || current?.link===link}
        fun attach(owner:Listener):BondedWearLink?=synchronized(guard){
            val service=current
            if(service!=null && service.lease?.valid(SystemClock.elapsedRealtime())!=true){service.link?.close();service.stopSelf();return@synchronized null}
            if(service!=null && service.link?.isReady()==true && service.lease?.valid(SystemClock.elapsedRealtime())==true){service.owner=WeakReference(owner);service.handler.post {if(service.owner.get()===owner)service.drain()};service.link}
            else pending?.takeIf {it.link.isReady() && it.lease.valid(SystemClock.elapsedRealtime())}?.also {it.owner.clear();pending=it.copy(owner=WeakReference(owner))}?.link
        }
        fun detach(owner:Listener){synchronized(guard){if(pending?.owner?.get()===owner)pending?.owner?.clear();if(current?.owner?.get()===owner)current?.owner?.clear()}}
        fun revoke(context:Context){val grant=synchronized(guard){pending.also {pending=null}};grant?.link?.close();context.stopService(Intent(context,WearLinkService::class.java))}
    }
    private val handler=Handler(Looper.getMainLooper())
    @Volatile private var link:BondedWearLink?=null
    private var lease:WearLease?=null
    private var owner=WeakReference<Listener>(null)
    private val inbox=java.util.ArrayDeque<ByteArray>()
    private val queued=java.util.concurrent.atomic.AtomicInteger()
    private fun drain(){
        val receiver=owner.get()?:return
        val channel=link?:return
        if(lease?.valid(SystemClock.elapsedRealtime())!=true)return
        while(inbox.isNotEmpty()){val bytes=inbox.removeFirst();try{receiver.onWearPacket(channel,bytes)}finally{bytes.fill(0)}}
    }
    override fun onBind(intent:Intent?)=null
    private fun promote(authorized:Boolean){
        val manager=getSystemService(NotificationManager::class.java)
        if(Build.VERSION.SDK_INT>=26)manager.createNotificationChannel(NotificationChannel("mark-wear-link","Conexão MARK com celular",NotificationManager.IMPORTANCE_LOW))
        val builder=if(Build.VERSION.SDK_INT>=26)Notification.Builder(this,"mark-wear-link")else Notification.Builder(this)
        val stop=PendingIntent.getService(this,42,Intent(this,WearLinkService::class.java).setAction(STOP),PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        val note=builder.setSmallIcon(R.drawable.mark_icon).setContentTitle(if(authorized)"MARK · celular conectado" else "MARK · conexão encerrada")
            .setContentText(if(authorized)"Sessão temporária autorizada. O microfone só funciona no canal de voz." else "Nenhuma sessão autorizada está ativa.")
            .setOngoing(true).addAction(Notification.Action.Builder(null,"Desconectar",stop).build()).build()
        if(Build.VERSION.SDK_INT>=29)startForeground(82,note,ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE)else startForeground(82,note)
    }
    override fun onStartCommand(intent:Intent?,flags:Int,startId:Int):Int {
        // Android 9 still requires promotion when a foreground launch is rejected.
        // The notification never creates a grant or opens a socket.
        val grant=synchronized(guard){pending.also {pending=null}}
        val approved=grant!=null && grant.link.isReady() && grant.lease.valid(SystemClock.elapsedRealtime())
        promote(approved || link?.isReady()==true)
        if(intent?.action==STOP){grant?.link?.close();stopSelf();return START_NOT_STICKY}
        if(grant==null){if(link==null)stopSelf();return START_NOT_STICKY}
        if(!approved){grant.link.close();stopSelf();return START_NOT_STICKY}
        link?.close();link=grant.link;lease=grant.lease;owner=grant.owner;current=this
        grant.link.adopt({bytes->
            val count=queued.incrementAndGet()
            if(bytes.size>WearProtocol.MAX_BYTES || count>4){queued.decrementAndGet();grant.link.close()}
            else handler.post {try {
                if(current===this && link===grant.link && grant.lease.valid(SystemClock.elapsedRealtime())){
                    if(inbox.size>=2)inbox.removeFirst().fill(0)
                    inbox.addLast(bytes);drain()
                }else {bytes.fill(0);if(current===this && !grant.lease.valid(SystemClock.elapsedRealtime()))stopSelf()}
            }finally {queued.decrementAndGet()}}
        },
            {handler.post {if(current===this && link===grant.link)stopSelf()}})
        handler.removeCallbacks(expire)
        handler.postDelayed(expire,grant.lease.remaining(SystemClock.elapsedRealtime()))
        return START_NOT_STICKY
    }
    private val expire=Runnable {stopSelf()}
    override fun onDestroy(){
        handler.removeCallbacksAndMessages(null)
        while(inbox.isNotEmpty())inbox.removeFirst().fill(0)
        val closed=link;link=null;lease=null
        synchronized(guard){if(current===this)current=null}
        closed?.close();if(closed!=null)owner.get()?.onWearClosed(closed);owner.clear()
        super.onDestroy()
    }
}

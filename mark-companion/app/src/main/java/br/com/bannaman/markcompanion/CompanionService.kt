package br.com.bannaman.markcompanion

import android.app.*
import android.content.*
import android.content.pm.ServiceInfo
import android.os.*
import br.com.bannaman.markwear.protocol.*
import org.json.JSONObject
import java.util.concurrent.*

class CompanionService:Service(){
 companion object {
  @Volatile var current:CompanionService?=null;private set
  private val guard=Any()
  private var pending:BondedWearLink?=null
  fun retain(context:Context,link:BondedWearLink){require(link.isReady());synchronized(guard){pending?.close();pending=link};try{context.startForegroundService(Intent(context,CompanionService::class.java))}catch(e:Exception){synchronized(guard){if(pending===link)pending=null};link.close();throw e}}
  fun revoke(context:Context){val staged=synchronized(guard){pending.also {pending=null}};staged?.close();current?.let {it.expiry=0;it.link?.close()};context.stopService(Intent(context,CompanionService::class.java))}
 }
 @Volatile private var link:BondedWearLink?=null;@Volatile private var expiry=0L
 @Volatile private var generation=0L
 private val commands=ThreadPoolExecutor(1,1,0,TimeUnit.SECONDS,ArrayBlockingQueue<Runnable>(2))
 private var gate=RequestGate()
 private val handler=Handler(Looper.getMainLooper());private val waiters=ConcurrentHashMap<String,ArrayBlockingQueue<WearProtocol.Packet>>()
 val connected get()=link?.isReady()==true && SystemClock.elapsedRealtime()<expiry
 override fun onBind(i:Intent?)=null
 override fun onStartCommand(i:Intent?,flags:Int,id:Int):Int {
  val manager=getSystemService(NotificationManager::class.java);manager.createNotificationChannel(NotificationChannel("mark-companion","MARK Companion",NotificationManager.IMPORTANCE_LOW))
  val stop=PendingIntent.getService(this,82,Intent(this,javaClass).setAction("STOP"),PendingIntent.FLAG_IMMUTABLE)
  startForeground(82,Notification.Builder(this,"mark-companion").setSmallIcon(R.drawable.mark_icon).setContentTitle("MARK Companion").setContentText("Gerenciamento autorizado por cinco minutos").setOngoing(true).addAction(Notification.Action.Builder(null,"Encerrar",stop).build()).build(),ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE)
  val ready=synchronized(guard){pending.also {pending=null}}
  if(i?.action=="STOP" || ready?.isReady()!=true){generation++;expiry=0;ready?.close();link?.close();stopSelf();return START_NOT_STICKY}
  generation++;handler.removeCallbacksAndMessages(null);link?.close();gate=RequestGate();link=ready;current=this;expiry=SystemClock.elapsedRealtime()+300000
  val grant=generation
  ready.adopt({bytes->if(connected && generation==grant){val p=runCatching {WearProtocol.decode(bytes)}.getOrNull();if(p!=null && p.kind in setOf("reply","error"))waiters[p.id]?.offer(p)
   else if(p!=null && p.kind=="manage" && gate.accept(p.id,SystemClock.elapsedRealtime()))try{commands.execute {
    if(!connected || generation!=grant)return@execute
    val result=runCatching {PhoneUtilities.media(this,p.payload)}
    if(connected && generation==grant)runCatching {ready.send(if(result.isSuccess)WearProtocol.encode("reply",result.getOrThrow(),p.id) else WearProtocol.encode("error",JSONObject().put("code",if(!PhoneUtilities.access(this))"MEDIA_PERMISSION_REQUIRED" else "MEDIA_NOT_AVAILABLE"),p.id))}
   }}catch(_:RejectedExecutionException){}
}},{handler.post {if(link===ready)stopSelf()}})
  handler.postDelayed({stopSelf()},300000);return START_NOT_STICKY
 }
 /** Single in-flight request; transfer waits for each ACK, no unbounded queued fragments. */
 @Synchronized fun request(payload:JSONObject):JSONObject {
  check(connected){"Conexão não autorizada ou expirada"}
  val bytes=WearProtocol.encode("manage",payload);val id=WearProtocol.decode(bytes).id
  val queue=ArrayBlockingQueue<WearProtocol.Packet>(1);waiters[id]=queue
  try{link!!.send(bytes);val reply=queue.poll(15,TimeUnit.SECONDS)?:error("Relógio não respondeu em quinze segundos");check(connected);check(reply.kind=="reply"){reply.payload.optString("code","Solicitação recusada")};return reply.payload}
  finally{waiters.remove(id);bytes.fill(0)}
 }
 override fun onDestroy(){generation++;current=null;commands.shutdownNow();handler.removeCallbacksAndMessages(null);link?.close();link=null;waiters.values.forEach {it.offer(WearProtocol.Packet("","error",JSONObject().put("code","DISCONNECTED")))};waiters.clear();super.onDestroy()}
}

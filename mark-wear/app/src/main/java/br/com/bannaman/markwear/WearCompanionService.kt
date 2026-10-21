package br.com.bannaman.markwear

import android.app.*
import android.content.*
import android.content.pm.*
import android.os.*
import android.util.Base64
import br.com.bannaman.markwear.protocol.*
import org.json.*
import java.io.File
import java.security.MessageDigest
import java.util.concurrent.*
import java.util.zip.ZipFile

@android.annotation.TargetApi(28)
class WearCompanionService:Service(){
 companion object {
  @Volatile var current:WearCompanionService?=null;private set
  private val guard=Any()
  private var pending:BondedWearLink?=null
  fun retain(context:Context,link:BondedWearLink){require(link.isReady());synchronized(guard){pending?.close();pending=link};try{context.startForegroundService(Intent(context,WearCompanionService::class.java))}catch(e:Exception){synchronized(guard){if(pending===link)pending=null};link.close();throw e}}
  fun revoke(context:Context){val staged=synchronized(guard){pending.also {pending=null}};staged?.close();current?.let {it.expiry=0;it.link?.close()};context.stopService(Intent(context,WearCompanionService::class.java))}
 }
 @Volatile var mediaText="Consulte a mídia do celular";private set
 @Volatile private var mediaPending=""
 @Volatile private var link:BondedWearLink?=null;@Volatile private var expiry=0L
 @Volatile private var generation=0L
 private val handler=Handler(Looper.getMainLooper());private lateinit var endpoint:CompanionEndpoint
 private var gate=RequestGate(4096)
 private val work=ThreadPoolExecutor(1,1,0,TimeUnit.SECONDS,ArrayBlockingQueue<Runnable>(4))
 val connected get()=link?.isReady()==true && SystemClock.elapsedRealtime()<expiry
 val mediaBusy get()=mediaPending.isNotEmpty()
 override fun onCreate(){super.onCreate();endpoint=CompanionEndpoint(this)}
 override fun onBind(i:Intent?)=null
 override fun onStartCommand(i:Intent?,flags:Int,id:Int):Int {
  val manager=getSystemService(NotificationManager::class.java);manager.createNotificationChannel(NotificationChannel("mark-companion","MARK Companion",NotificationManager.IMPORTANCE_LOW))
  val stop=PendingIntent.getService(this,83,Intent(this,javaClass).setAction("STOP"),PendingIntent.FLAG_IMMUTABLE)
  startForeground(83,Notification.Builder(this,"mark-companion").setSmallIcon(R.drawable.mark_icon).setContentTitle("Gerenciamento MARK").setContentText("Sessão local de cinco minutos; sem microfone").setOngoing(true).addAction(Notification.Action.Builder(null,"Encerrar",stop).build()).build())
  val ready=synchronized(guard){pending.also {pending=null}}
  if(i?.action=="STOP" || ready?.isReady()!=true){generation++;expiry=0;ready?.close();link?.close();stopSelf();return START_NOT_STICKY}
  generation++;handler.removeCallbacksAndMessages(null);link?.close();endpoint.close();gate=RequestGate(4096);mediaPending="";mediaText="Consulte a mídia do celular";link=ready;current=this;expiry=SystemClock.elapsedRealtime()+300000
  val grant=generation
  ready.adopt({bytes->if(connected && generation==grant)try{work.execute {if(!connected || generation!=grant)return@execute;val packet=runCatching {WearProtocol.decode(bytes)}.getOrNull()?:return@execute
   if(packet.kind in setOf("reply","error")){if(packet.id==mediaPending){mediaText=if(packet.kind=="reply")packet.payload.optString("title","Mídia ativa").take(120) else "Mídia indisponível; confira permissões no Companion";mediaPending=""};return@execute}
   if(!gate.accept(packet.id,SystemClock.elapsedRealtime()))return@execute
   val result=runCatching {require(packet.kind=="manage");endpoint.execute(packet.payload)}
   if(connected && generation==grant)runCatching {ready.send(if(result.isSuccess)WearProtocol.encode("reply",result.getOrThrow(),packet.id) else WearProtocol.encode("error",JSONObject().put("code","REQUEST_REJECTED_OR_INCOMPATIBLE_APK"),packet.id))}
  }}catch(_:RejectedExecutionException){ready.close()}},{handler.post {if(link===ready)stopSelf()}})
  handler.postDelayed({stopSelf()},300000);return START_NOT_STICKY
 }
 fun media(command:String?=null){
  check(connected && mediaPending.isEmpty());val payload=JSONObject().put("action",if(command==null)"media_status" else "media_control");if(command!=null){require(command in setOf("play","pause","next","previous"));payload.put("command",command)}
  val bytes=WearProtocol.encode("manage",payload);val id=WearProtocol.decode(bytes).id;mediaPending=id;mediaText="Consultando celular…"
  try{link!!.send(bytes)}catch(e:Exception){mediaPending="";throw e}
  handler.postDelayed({if(mediaPending==id){mediaPending="";mediaText="O celular não respondeu"}},15000)
 }
 fun review():String {check(connected);return endpoint.review()}
 fun install(callback:(String)->Unit){try{work.execute {val text=runCatching {check(connected);endpoint.install()}.getOrElse {"Instalação recusada; arquivo incompatível ou instalador indisponível"};handler.post {callback(text)}}}catch(_:RejectedExecutionException){callback("Ocupado")}}
 override fun onDestroy(){generation++;current=null;handler.removeCallbacksAndMessages(null);link?.close();link=null;work.shutdownNow();endpoint.close();super.onDestroy()}
}

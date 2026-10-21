package br.com.bannaman.markwear
import android.app.Activity
import android.os.*
import android.view.WindowManager
import br.com.bannaman.markwear.protocol.*

@android.annotation.TargetApi(28)
class WearCompanionActivity:Activity(){
 private var waiting:BondedWearLink?=null;private var active=false
 private var interrupted=false
 private val pageHandler=Handler(Looper.getMainLooper())
 private val connectionHandler=Handler(Looper.getMainLooper())
 private var pageGeneration=0L
 private fun page(title:String):android.widget.LinearLayout {
  pageGeneration++;pageHandler.removeCallbacksAndMessages(null)
  return WearUi.page(this,title)
 }
 private fun awaitServiceState(expected:Boolean,generation:Long,remaining:Int){pageHandler.postDelayed({
  if(active && pageGeneration==generation){
   if((WearCompanionService.current?.connected==true)==expected)render(if(expected)"Conexão autorizada" else "Conexão revogada")
   else if(remaining>0)awaitServiceState(expected,generation,remaining-1)
   else render("Serviço ainda não confirmou a mudança; atualize o estado")
  }
 },100)}
 override fun onCreate(state:Bundle?){super.onCreate(state);window.addFlags(WindowManager.LayoutParams.FLAG_SECURE);render()}
 private fun cancelWaiting(){val link=waiting;waiting=null;connectionHandler.removeCallbacksAndMessages(null);window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);link?.close()}
 override fun onStart(){super.onStart();active=true;render(if(interrupted)"Tentativa cancelada ao sair da tela; conecte novamente" else "");interrupted=false}
 override fun onStop(){active=false;pageGeneration++;pageHandler.removeCallbacksAndMessages(null);if(waiting!=null)interrupted=true;cancelWaiting();super.onStop()}
 private fun media(){
  val service=WearCompanionService.current?:return render("Conecte o Companion")
  val box=page("Mídia do celular");val status=WearUi.label(this,box,service.mediaText)
  val generation=pageGeneration
  fun refresh(remaining:Int){pageHandler.postDelayed({
   if(active && pageGeneration==generation){
    status.text=if(service.connected)service.mediaText else "Gerenciamento desconectado; reconecte o Companion"
    if(service.connected && service.mediaBusy && remaining>0)refresh(remaining-1)
   }
  },500)}
  fun request(command:String?=null){
   if(runCatching {service.media(command)}.isFailure){status.text=if(service.connected)"Aguarde a resposta do celular" else "Gerenciamento desconectado; reconecte o Companion";return}
   status.text=service.mediaText;refresh(32)
  }
  WearUi.button(this,box,"Consultar / atualizar","refresh"){request()}
  for((command,title) in listOf("play" to "Reproduzir","pause" to "Pausar","previous" to "Anterior","next" to "Próxima"))WearUi.button(this,box,title,command){request(command)}
  if(service.mediaBusy)refresh(32)
  WearUi.button(this,box,"Voltar"){render()}
 }
 private fun render(message:String=""){
  val box=page("Companion")
  WearUi.label(this,box,if(WearCompanionService.current?.connected==true)"Gerenciamento conectado" else "Gerenciamento desconectado")
  if(message.isNotEmpty())WearUi.label(this,box,message)
  if(Build.VERSION.SDK_INT>=33 && checkSelfPermission("android.permission.POST_NOTIFICATIONS")!=android.content.pm.PackageManager.PERMISSION_GRANTED)WearUi.button(this,box,"Permitir notificações Android"){requestPermissions(arrayOf("android.permission.POST_NOTIFICATIONS"),77)}
  if(Build.VERSION.SDK_INT<28){WearUi.label(this,box,"Gerenciamento exige Android9/API28");return}
  if(WearCompanionService.current?.connected==true){
   WearUi.button(this,box,"Mídia do celular","media"){media()}
   WearUi.button(this,box,"Revisar APK recebido","apps"){
    val text=runCatching {WearCompanionService.current?.review()}.getOrNull()?:"APK inválido"
    val review=page("Instalação");val generation=pageGeneration;WearUi.label(this,review,text,12)
    if(CompanionEndpoint.directInstallUnsupported())WearUi.label(this,review,"Este firmware não permite instalação/desinstalação comum pelo APK. Arquivo validado; conclusão exige auxiliar do laboratório, ou loja oficial compatível.",12)
    else WearUi.button(this,review,"Solicitar instalação ao Android"){WearCompanionService.current?.install {if(active && pageGeneration==generation)render(it)}}.isEnabled=text!="Nenhum APK aguardando confirmação" && text!="APK inválido"
    if(!CompanionEndpoint.directInstallUnsupported())WearUi.button(this,review,"Permissão de instalação Android","settings"){
     val intent=android.content.Intent(android.provider.Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,android.net.Uri.parse("package:"+packageName))
     if(intent.resolveActivity(packageManager)!=null)startActivity(intent)else render("Este firmware não oferece tela padrão de fontes desconhecidas")
    }
    WearUi.button(this,review,"Voltar"){render()}
   }
   WearUi.button(this,box,"Revogar gerenciamento","stop"){WearCompanionService.revoke(this);render("Revogação solicitada");awaitServiceState(false,pageGeneration,20)}
  }else{
   if(checkSelfPermission("android.permission.BLUETOOTH_CONNECT")!=android.content.pm.PackageManager.PERMISSION_GRANTED && Build.VERSION.SDK_INT>=31){WearUi.button(this,box,"Permitir Bluetooth"){requestPermissions(arrayOf("android.permission.BLUETOOTH_CONNECT"),75)};return}
   @Suppress("MissingPermission") val adapter=getSystemService(android.bluetooth.BluetoothManager::class.java).adapter
   if(adapter?.isEnabled!=true){WearUi.label(this,box,"Ative Bluetooth nas configurações");return}
   WearUi.label(this,box,"Abra MARK Companion no M23 e escolha Ouvir relógio. Depois selecione o M23 aqui.",12)
   @Suppress("MissingPermission") val peers=adapter.bondedDevices
   peers.forEach {peer->
    @Suppress("MissingPermission") val name=peer.name?.take(60)?:"Celular pareado"
    WearUi.button(this,box,name){
     WearCompanionService.revoke(this);cancelWaiting();val progress=page("Conectando ao celular");WearUi.label(this,progress,"Celular escolhido: $name\nMantenha MARK Companion ouvindo no celular.",12);WearUi.button(this,progress,"Cancelar conexão"){cancelWaiting();render("Conexão cancelada")};window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);lateinit var link:BondedWearLink
     link=BondedWearLink(false,{code->runOnUiThread {if(active && waiting===link){window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);val confirm=page("Confira os códigos");WearUi.label(this,confirm,code,24);WearUi.label(this,confirm,"Compare com o M23. Autoriza gerenciamento por cinco minutos.",12);WearUi.button(this,confirm,"Códigos iguais · confirmar"){link.confirm()};WearUi.button(this,confirm,"Cancelar"){cancelWaiting();render()}}}},
      {runOnUiThread {if(active && waiting===link){runCatching {WearCompanionService.retain(this,link)}.onSuccess {waiting=null;connectionHandler.removeCallbacksAndMessages(null);window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);render("Autorização concluída; aguardando serviço");awaitServiceState(true,pageGeneration,20)}.onFailure {link.close();render("Conexão recusada")}}else link.close()}},
      {},{runOnUiThread {if(waiting===link){cancelWaiting();if(active)render("Conexão não concluída; confira se o M23 está ouvindo e tente novamente")}}},CompanionPolicy.SERVICE)
     waiting=link;runCatching {link.connect(peer)}.onFailure {cancelWaiting();render("Não foi possível iniciar conexão Bluetooth")};connectionHandler.postDelayed({if(waiting===link && !link.isReady())link.close()},60000)
    }
   }
  }
  WearUi.button(this,box,"Diagnóstico BLE limitado","status"){
   val permission=Build.VERSION.SDK_INT<31 || checkSelfPermission("android.permission.BLUETOOTH_ADVERTISE")==android.content.pm.PackageManager.PERMISSION_GRANTED
   if(!permission){requestPermissions(arrayOf("android.permission.BLUETOOTH_ADVERTISE"),76);return@button}
   WearUi.label(this,box,"Testando GATT/publicidade por até cinco segundos. Sem dados pessoais.",12)
   val generation=pageGeneration
   Thread {val result=runCatching {WearBleProbe(this).run()}.getOrElse {"BLE_PROBE_FAILED"};runOnUiThread {if(active && pageGeneration==generation)render(result)}}.start()
  }
  WearUi.button(this,box,"Atualizar","refresh"){render()};WearUi.button(this,box,"Voltar"){finish()}
 }
}

package br.com.bannaman.markcompanion

import android.app.*
import android.content.*
import android.content.pm.*
import android.graphics.Color
import android.graphics.drawable.GradientDrawable
import android.hardware.biometrics.*
import android.os.*
import android.util.Base64
import android.view.*
import android.widget.*
import br.com.bannaman.markwear.protocol.*
import org.json.*
import java.io.File
import java.security.MessageDigest
import java.util.UUID
import java.util.concurrent.*

class MainActivity:Activity(){
 private val handler=Handler(Looper.getMainLooper())
 private val work=ThreadPoolExecutor(1,1,0,TimeUnit.SECONDS,ArrayBlockingQueue<Runnable>(2))
 private var waiting:BondedWearLink?=null
 private var cancel:CancellationSignal?=null
 private var active=false;private var busy=false
 private var pageGeneration=0L
 private val pairing=PairingUiState()
 private lateinit var box:LinearLayout;private lateinit var message:TextView
 private var latest=JSONObject();private var lastSync=0L
 private var localApk:File?=null;private var localMeta:JSONObject?=null
 override fun onCreate(state:Bundle?){super.onCreate(state);window.addFlags(WindowManager.LayoutParams.FLAG_SECURE);window.setHideOverlayWindows(true);home()}
 override fun onStart(){super.onStart();active=true;home();if(pairing.consumeResumeChoice())choose()}
 private fun clearPending(){val link=waiting;waiting=null;handler.removeCallbacksAndMessages(null);link?.close();cancel?.cancel();cancel=null;window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)}
 override fun onStop(){active=false;pairing.pendingCancelled(waiting!=null);clearPending();super.onStop()}
 override fun onRequestPermissionsResult(code:Int,permissions:Array<out String>,results:IntArray){
  super.onRequestPermissionsResult(code,permissions,results)
  if(code==70){val allowed=checkSelfPermission("android.permission.BLUETOOTH_CONNECT")==PackageManager.PERMISSION_GRANTED
   if(pairing.permissionResult(allowed,active))choose()
   else if(!allowed && active)message.text="Permita aparelhos próximos para escolher o relógio"
  }
 }
 override fun onDestroy(){handler.removeCallbacksAndMessages(null);work.shutdownNow();if(!busy)localApk?.delete();super.onDestroy()}
 private fun dp(n:Int)=(n*resources.displayMetrics.density).toInt()
 private fun page(title:String){pageGeneration++;val scroll=ScrollView(this).apply {setBackgroundColor(0xff05080b.toInt());isFillViewport=true};box=LinearLayout(this).apply {orientation=LinearLayout.VERTICAL;setPadding(dp(22),dp(30),dp(22),dp(32))};scroll.addView(box);setContentView(scroll);label("MARK  /  COMPANION",13,0xff79dde0.toInt());label(title,26,Color.WHITE);message=label("",14)}
 private fun label(text:String,size:Int=15,color:Int=0xffa3b6c5.toInt()):TextView=TextView(this).apply {this.text=text;textSize=size.toFloat();setTextColor(color);setPadding(0,dp(10),0,dp(10));box.addView(this)}
 private fun button(text:String,run:()->Unit){box.addView(Button(this).apply {this.text=text;filterTouchesWhenObscured=true;isAllCaps=false;textSize=15f;setTextColor(Color.WHITE);background=GradientDrawable().apply {setColor(0xff13252d.toInt());cornerRadius=dp(16).toFloat();setStroke(dp(1),0xff294149.toInt())};setPadding(dp(16),dp(8),dp(16),dp(8));setOnClickListener {if(busy)message.text="Aguarde a operação atual" else run()}},LinearLayout.LayoutParams(-1,dp(56)).apply {topMargin=dp(10)})}
 private fun home(){
  page("Seu relógio");label(if(CompanionService.current?.connected==true)"Gerenciamento MARK conectado" else "Gerenciamento MARK desconectado",18,0xff79dde0.toInt())
  label("Associação Android: dispositivo já pareado.\nAssociação MARK: códigos + biometria, cinco minutos.\nPareamento completo Wear OS: sistema original.",13)
  if(latest.length()>0){label("${latest.optString("name")} · ${latest.optInt("battery")}%\n${if(latest.optBoolean("charging"))"Carregando" else "Fora do carregador"}\nMARK Wear ${latest.optString("version")}\nAPI ${latest.optInt("api")} · ${latest.optString("firmware")}\nÚltima consulta: ${java.text.DateFormat.getTimeInstance().format(java.util.Date(lastSync))}");label(latest.optString("install"),13)}
  button("Ouvir relógio / reconectar"){choose()}
  button("Consultar estado real"){task {val reply=channel().request(JSONObject().put("action","status"));ui {latest=reply;lastSync=System.currentTimeMillis();home()}}}
  button("MARK Wear Store"){store()}
  button("Aplicativos instalados"){apps(0)}
  button("Sensores disponíveis"){task {val result=channel().request(JSONObject().put("action","sensors"));ui {page("Sensores do relógio");val rows=result.getJSONArray("sensors");for(i in 0 until rows.length())label(rows.getJSONObject(i).getString("name"));button("Voltar"){home()}}}}
  button("Notificações e mídia"){utilities()}
  button("Diagnóstico / recuperação"){page("Diagnóstico");label("Bluetooth: ${getSystemService(android.bluetooth.BluetoothManager::class.java).adapter?.isEnabled==true}\nCanal MARK: ${CompanionService.current?.connected==true}\nTransporte: RFCOMM autenticado\nBLE: em investigação\nGoogle/Mobvoi: sem novas desativações\nReconexão automática: ainda não habilitada\nMicrofone/sensores: desligados\nSem dependência do PC ou Mobile para gerenciamento",14);button("Configurações Bluetooth"){startActivity(Intent(android.provider.Settings.ACTION_BLUETOOTH_SETTINGS))};button("Voltar"){home()}}
  button("Revogar conexão MARK"){AlertDialog.Builder(this).setTitle("Encerrar gerenciamento?").setMessage("Preserva o pareamento Bluetooth do Android.").setPositiveButton("Revogar"){_,_->CompanionService.revoke(this);home();message.text="Encerrando gerenciamento…";awaitServiceState(false,pageGeneration,20)}.setNegativeButton("Cancelar",null).show()}
  if(pairing.consumeCancellation())message.text="Tentativa de conexão cancelada ao sair da tela. Toque em Ouvir relógio para tentar novamente."
 }
 private fun ui(run:()->Unit){runOnUiThread {if(active)run()}}
 private fun channel()=CompanionService.current?.takeIf {it.connected}?:error("Conecte e autorize o relógio primeiro")
 private fun awaitServiceState(expected:Boolean,generation:Long,remaining:Int){handler.postDelayed({
  if(active && pageGeneration==generation){
   if((CompanionService.current?.connected==true)==expected)home()
   else if(remaining>0)awaitServiceState(expected,generation,remaining-1)
   else message.text="Serviço ainda não confirmou a mudança; consulte o estado"
  }
 },100)}
 private fun task(run:()->Unit){if(busy)return;busy=true;message.text="Processando…";try{work.execute {val result=runCatching {run()};runOnUiThread {busy=false;if(active)result.exceptionOrNull()?.let {message.text=it.message?.take(180)?:"Operação não concluída"}}}}catch(_:RejectedExecutionException){busy=false;message.text="Operação ocupada"}}
 private fun choose(){
  if(checkSelfPermission("android.permission.BLUETOOTH_CONNECT")!=PackageManager.PERMISSION_GRANTED){pairing.requestPermission();requestPermissions(arrayOf("android.permission.BLUETOOTH_CONNECT","android.permission.POST_NOTIFICATIONS"),70);return}
  @Suppress("MissingPermission") val adapter=getSystemService(android.bluetooth.BluetoothManager::class.java).adapter
  if(adapter?.isEnabled!=true){message.text="Ative Bluetooth nas configurações";return}
  page("Escolha o relógio pareado")
  @Suppress("MissingPermission") val peers=adapter.bondedDevices
  if(peers.isEmpty()){label("Nenhum relógio pareado no Android. Pareie o TicWatch nas configurações Bluetooth primeiro.");button("Configurações Bluetooth"){startActivity(Intent(android.provider.Settings.ACTION_BLUETOOTH_SETTINGS))}}
  peers.forEach {peer->
   @Suppress("MissingPermission") val name=peer.name?.take(60)?:"Dispositivo pareado"
   button("Ouvir $name"){
    CompanionService.revoke(this);clearPending();page("Aguardando o TicWatch");label("Dispositivo escolhido: $name");button("Cancelar conexão"){clearPending();home()};window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);lateinit var link:BondedWearLink
    link=BondedWearLink(true,{code->ui {if(waiting===link){page("Compare os códigos");label(code,30,0xff79dde0.toInt());label("Compare com o TicWatch. Autoriza gerenciamento local por cinco minutos.");button("Os códigos coincidem · autorizar"){biometric {if(waiting===link)link.confirm()}};button("Cancelar"){clearPending();home()}}}},
     {runOnUiThread {if(active && waiting===link){runCatching {CompanionService.retain(this,link)}.onSuccess {waiting=null;handler.removeCallbacksAndMessages(null);cancel=null;window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);home();message.text="Autorização concluída; aguardando serviço…";awaitServiceState(true,pageGeneration,20)}.onFailure {link.close();message.text="Não consegui iniciar gerenciamento"}}else link.close()}},{},
     {ui {if(waiting===link){clearPending();message.text="Conexão não concluída. Confira se o MARK Companion está aberto no relógio e tente novamente."}}},CompanionPolicy.SERVICE)
    waiting=link;message.text="No relógio: MARK → Configurações → MARK Companion → escolha o M23."
    @Suppress("MissingPermission") val address=peer.address
    link.listen(adapter,address);handler.postDelayed({if(waiting===link && !link.isReady())link.close()},60000)
   }
  };button("Voltar"){clearPending();home()}
 }
 private fun utilities(){
  page("Notificações e mídia");val prefs=getSharedPreferences("utilities",0)
  label("Acesso Android: ${PhoneUtilities.access(this)}\nApps selecionados: ${prefs.getStringSet("notification_apps",emptySet()).orEmpty().size}\nControle de mídia: ${prefs.getBoolean("media",false)}",14)
  label("Notificações enviam somente título dos apps escolhidos durante a sessão autorizada; notificações secretas e contínuas são excluídas. Conteúdo não fica em fila no disco.",13)
  button("Conceder acesso nas configurações Android"){startActivity(Intent(android.provider.Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS))}
  button("Selecionar aplicativos para notificações"){
   if(!PhoneUtilities.access(this)){message.text="Conceda acesso nas configurações primeiro";return@button}
   val apps=packageManager.queryIntentActivities(Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER),0).filter {it.activityInfo.packageName!=packageName}.distinctBy {it.activityInfo.packageName}.sortedBy {it.loadLabel(packageManager).toString()}.take(100)
   val selected=prefs.getStringSet("notification_apps",emptySet()).orEmpty().toMutableSet()
   AlertDialog.Builder(this).setTitle("Enviar somente título destes apps").setMultiChoiceItems(apps.map {it.loadLabel(packageManager).toString()}.toTypedArray(),apps.map {it.activityInfo.packageName in selected}.toBooleanArray()){_,index,checked->val name=apps[index].activityInfo.packageName;if(checked)selected.add(name)else selected.remove(name)}.setPositiveButton("Salvar"){_,_->prefs.edit().putStringSet("notification_apps",selected).apply();utilities()}.setNegativeButton("Cancelar",null).show()
  }
  button(if(prefs.getBoolean("media",false))"Desativar controles de mídia" else "Autorizar controles de mídia"){
   if(!PhoneUtilities.access(this)){message.text="Conceda acesso nas configurações primeiro";return@button}
   prefs.edit().putBoolean("media",!prefs.getBoolean("media",false)).apply();utilities()
  }
  button("Pausar envio de notificações"){prefs.edit().remove("notification_apps").apply();utilities()};button("Voltar"){home()}
 }
 private fun biometric(approved:()->Unit){val m=getSystemService(BiometricManager::class.java);if(m.canAuthenticate(BiometricManager.Authenticators.BIOMETRIC_STRONG)!=BiometricManager.BIOMETRIC_SUCCESS){message.text="Biometria forte indisponível";return};cancel=CancellationSignal();BiometricPrompt.Builder(this).setTitle("Autorizar MARK Companion").setSubtitle("Somente relógio escolhido; cinco minutos").setAllowedAuthenticators(BiometricManager.Authenticators.BIOMETRIC_STRONG).setNegativeButton("Cancelar",mainExecutor){_,_->}.build().authenticate(cancel!!,mainExecutor,object:BiometricPrompt.AuthenticationCallback(){override fun onAuthenticationSucceeded(result:BiometricPrompt.AuthenticationResult){if(active)approved()}})}
 private fun apps(offset:Int){task {val result=channel().request(JSONObject().put("action","apps").put("offset",offset));ui {page("Aplicativos do TicWatch");val rows=result.getJSONArray("apps");for(i in 0 until rows.length()){val a=rows.getJSONObject(i);label("${a.getString("name")} · ${a.optString("version")}\n${a.getString("package")}",14)};val next=result.getInt("next");if(next>=0)button("Próxima página"){apps(next)};button("Voltar"){home()}}}}
 private fun store(){page("MARK Wear Store");label("Catálogo próprio e APK local. Verificação antes da transferência e revisão no relógio.")
  button("Catálogo MARK · aplicativo de teste"){task {val f=File(cacheDir,"store-local.apk");assets.open("mark-store-test.apk").use {src->f.outputStream().use {src.copyTo(it)}};inspect(f,"Catálogo próprio MARK");ui {preview()}}}
  button("Escolher APK local"){startActivityForResult(Intent(Intent.ACTION_OPEN_DOCUMENT).setType("application/vnd.android.package-archive").addCategory(Intent.CATEGORY_OPENABLE),71)}
  label("Catálogos F-Droid: ainda não ativados. APK único até8MiB; splits ainda não suportados. ADB não é instalação do aplicativo.",13);button("Voltar"){home()}
 }
 private fun inspect(file:File,origin:String){
  boundedApkArchive(file);val info=packageManager.getPackageArchiveInfo(file.absolutePath,PackageManager.GET_SIGNING_CERTIFICATES or PackageManager.GET_PERMISSIONS or PackageManager.GET_CONFIGURATIONS)?:error("APK inválido")
  require(!CompanionPolicy.protectedPackage(info.packageName) && info.splitNames.isNullOrEmpty());val signer=info.signingInfo?.apkContentsSigners?:error("APK sem assinatura");require(signer.size==1)
  localApk?.takeIf {it!=file}?.delete();localApk=file
  localMeta=JSONObject().put("action","begin").put("package",info.packageName).put("version",info.longVersionCode).put("size",file.length()).put("sha",CompanionPolicy.hash(file)).put("signer",CompanionPolicy.hex(MessageDigest.getInstance("SHA-256").digest(signer[0].toByteArray()))).put("token",UUID.randomUUID().toString().replace("-",""))
  getSharedPreferences("store",0).edit().putString("preview","${info.packageName}\nVersão ${info.versionName}\nOrigem: $origin\n${file.length()} bytes · API mínima ${info.applicationInfo!!.minSdkVersion}\nSHA256 ${localMeta!!.getString("sha")}\nAssinatura ${localMeta!!.getString("signer")}\nPermissões:\n${info.requestedPermissions.orEmpty().joinToString("\n").ifBlank {"Nenhuma"}}").apply()
 }
 private fun preview(){page("Revise o APK");label(getSharedPreferences("store",0).getString("preview","")!!,13);button("Transferir para revisar no relógio"){task {
  val svc=channel();val file=localApk?:error("Selecione o arquivo novamente");val meta=localMeta?:error("Metadados ausentes");val token=meta.getString("token")
  try{require(CompanionPolicy.hash(file)==meta.getString("sha"));svc.request(meta);file.inputStream().use {src->val buffer=ByteArray(CompanionPolicy.CHUNK);var offset=0L;while(true){val n=src.read(buffer);if(n<0)break;val reply=svc.request(JSONObject().put("action","chunk").put("token",token).put("offset",offset).put("data",Base64.encodeToString(buffer.copyOf(n),Base64.NO_WRAP)));offset+=n;require(reply.getLong("offset")==offset)}};svc.request(JSONObject().put("action","finish").put("token",token));ui {message.text="Arquivo validado. No relógio: Companion → Revisar APK recebido."}}
  catch(e:Exception){runCatching {svc.request(JSONObject().put("action","cancel"))};throw e}
 }};button("Cancelar"){localApk?.delete();localApk=null;localMeta=null;store()}}
 override fun onActivityResult(code:Int,result:Int,data:Intent?){super.onActivityResult(code,result,data);if(code==71 && result==RESULT_OK){val uri=data?.data?:return;task {val file=File(cacheDir,"store-local.apk");try{contentResolver.openInputStream(uri)!!.use {src->file.outputStream().use {dest->val b=ByteArray(8192);var total=0;while(true){val n=src.read(b);if(n<0)break;total+=n;require(total<=CompanionPolicy.MAX_APK);dest.write(b,0,n)}}};inspect(file,"Arquivo autorizado pelo usuário");ui {preview()}}catch(e:Exception){file.delete();throw e}}}}
}

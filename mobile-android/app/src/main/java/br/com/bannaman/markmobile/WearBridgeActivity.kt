package br.com.bannaman.markmobile

import android.app.Activity
import android.content.IntentFilter
import android.hardware.biometrics.BiometricPrompt
import android.hardware.biometrics.BiometricManager
import android.os.*
import android.view.WindowManager
import android.widget.*
import com.google.android.gms.wearable.*
import br.com.bannaman.markwear.protocol.*
import org.json.JSONObject
import java.util.concurrent.*

/** Foreground-only, owner-approved bridge. No credentials ever cross Data Layer. */
class WearBridgeActivity: Activity(),MessageClient.OnMessageReceivedListener {
    private var bluetooth: BondedWearLink?=null
    private var box: LinearLayout?=null
    private var info: TextView?=null
    @Volatile private var node=""
    @Volatile private var active=false
    @Volatile private var expires=0L
    @Volatile private var generation=0L
    private val handler=Handler(Looper.getMainLooper())
    private var cancellation: CancellationSignal?=null
    private val gate=RequestGate()
    private val work=ThreadPoolExecutor(1,1,0,TimeUnit.SECONDS,ArrayBlockingQueue<Runnable>(2))
    private val settings by lazy {SecureSettings(this)}
    private val expiry=Runnable {closeBridge();info?.text="Sessão encerrada. Autorize novamente para continuar."}

    override fun onCreate(state: Bundle?) {
        super.onCreate(state);window.addFlags(WindowManager.LayoutParams.FLAG_SECURE)
        val layout=LinearLayout(this).apply {orientation=LinearLayout.VERTICAL;setPadding(32,48,32,24);setBackgroundColor(0xff05080b.toInt())}
        val scroll=ScrollView(this);scroll.addView(layout);setContentView(scroll);box=layout
        layout.addView(TextView(this).apply {text="MARK Wear";textSize=24f;setTextColor(0xff79dde0.toInt())})
        info=TextView(this).apply {text="Ponte desligada. Bluetooth direto permite bloquear a tela após autorização. A sessão dura até cinco minutos e exige biometria forte.";setTextColor(0xffe6edf3.toInt());textSize=16f}
        layout.addView(info)
        button("Bluetooth direto · sem conta Google") {chooseBluetooth()}
        button("Via Wear OS / serviços Google") {discover()}
        button("Revogar e encerrar ponte") {WearPhoneForeground.stop(this);closeBridge();info?.text="Ponte revogada nesta sessão"}
        button("Voltar") {finish()}
    }
    override fun onStart() {super.onStart();active=true}
    override fun onStop() {active=false;closeBridge();super.onStop()}
    override fun onDestroy() {work.shutdownNow();super.onDestroy()}
    private fun button(text: String,run:()->Unit) {box?.addView(Button(this).apply {this.text=text;isAllCaps=false;setOnClickListener {run()}})}
    private fun discover() {
        if(!settings.paired()){info?.text="Primeiro pareie e autorize este celular no PC.";return}
        Wearable.getCapabilityClient(this).getCapability("mark_wear_v1",CapabilityClient.FILTER_REACHABLE).addOnSuccessListener {cap ->
            if(!active)return@addOnSuccessListener
            if(cap.nodes.isEmpty())info?.text="Nenhum MARK Wear encontrado. Confira Bluetooth, serviços Google, package e assinatura dos APKs."
            cap.nodes.forEach {candidate->button("Autorizar ${candidate.displayName}"){authorize(candidate.id)}}
        }.addOnFailureListener {if(active)info?.text="Data Layer indisponível neste celular"}
    }
    private fun biometric(onSuccess:()->Unit) {
        val manager=getSystemService(BiometricManager::class.java)
        if(manager.canAuthenticate(BiometricManager.Authenticators.BIOMETRIC_STRONG)!=BiometricManager.BIOMETRIC_SUCCESS){info?.text="Biometria forte indisponível. A ponte permanece bloqueada.";return}
        cancellation=CancellationSignal()
        BiometricPrompt.Builder(this).setTitle("Autorizar MARK Wear")
            .setSubtitle("Confirme o relógio; manter a conexão por cinco minutos, com notificação")
            .setAllowedAuthenticators(BiometricManager.Authenticators.BIOMETRIC_STRONG)
            .setNegativeButton("Cancelar",mainExecutor){_,_->}
            .build().authenticate(cancellation!!,mainExecutor,object:BiometricPrompt.AuthenticationCallback(){
                override fun onAuthenticationSucceeded(result:BiometricPrompt.AuthenticationResult){if(active)onSuccess()}
                override fun onAuthenticationError(code:Int,message:CharSequence){if(active)info?.text="Autorização cancelada"}
            })
    }
    private fun authorize(candidate:String) {
        closeBridge();biometric {
            node=candidate;expires=SystemClock.elapsedRealtime()+300000
            val grant=generation
            Wearable.getMessageClient(this).addListener(this).addOnSuccessListener {
                if(!allowed(candidate) || generation!=grant)Wearable.getMessageClient(this).removeListener(this)
                else info?.text="Ponte Wear OS ativa"
            }.addOnFailureListener {if(generation==grant){closeBridge();info?.text="Data Layer indisponível"}}
            handler.postDelayed(expiry,300000)
        }
    }
    @Suppress("MissingPermission") private fun chooseBluetooth() {
        if(!settings.paired()){info?.text="Primeiro autorize este celular no PC.";return}
        if(Build.VERSION.SDK_INT>=31 && checkSelfPermission(android.Manifest.permission.BLUETOOTH_CONNECT)!=android.content.pm.PackageManager.PERMISSION_GRANTED) {
            requestPermissions(arrayOf(android.Manifest.permission.BLUETOOTH_CONNECT),41);return
        }
        val adapter=getSystemService(android.bluetooth.BluetoothManager::class.java).adapter
        if(adapter==null || !adapter.isEnabled){info?.text="Ative Bluetooth nas configurações do celular";return}
        val peers=adapter.bondedDevices
        if(peers.isEmpty()) {info?.text="Pareie o TicWatch pelo Bluetooth do Android, sem copiar conta Google.";return}
        info?.text="Selecione o relógio já pareado. Depois compare os códigos nos dois MARK."
        peers.forEach {peer->button("Ouvir ${peer.name?.take(60)?:"dispositivo pareado"}"){listenBluetooth(adapter,peer.address)}}
    }
    override fun onRequestPermissionsResult(code:Int,permissions:Array<out String>,results:IntArray) {
        super.onRequestPermissionsResult(code,permissions,results)
        if(code==41 && results.firstOrNull()==android.content.pm.PackageManager.PERMISSION_GRANTED && active)chooseBluetooth()
    }
    private fun listenBluetooth(adapter:android.bluetooth.BluetoothAdapter,address:String) {
        WearPhoneForeground.stop(this);closeBridge();val grant=generation;val candidate="bt:"+address
        lateinit var link:BondedWearLink
        link=BondedWearLink(true,{code->runOnUiThread {
            if(!active || grant!=generation){link.close();return@runOnUiThread}
            info?.text="Código $code. Confira o mesmo código no relógio."
            button("Os códigos coincidem · autorizar") {biometric {
                if(bluetooth!==link || grant!=generation)return@biometric
                node=candidate;expires=SystemClock.elapsedRealtime()+300000
                runCatching {link.confirm()}.onFailure {closeBridge();info?.text="Bluetooth desconectado"}
                handler.removeCallbacks(expiry);handler.postDelayed(expiry,300000)
            }}
        }},{runOnUiThread {if(active && grant==generation) {
                runCatching {WearPhoneForeground.start(this,link,expires) {if(active)info?.text="Bluetooth autorizado. Pode bloquear o celular; sessão de cinco minutos com notificação."}}.onSuccess {
                    bluetooth=null;handler.removeCallbacks(expiry)
                    info?.text="Preparando conexão em segundo plano…"
                }.onFailure {closeBridge();info?.text="Não consegui manter a conexão em segundo plano"}
            }}},
            {data->process(candidate,data){reply->link.send(reply)}},
            {runOnUiThread {if(active && grant==generation){closeBridge();info?.text="Bluetooth desconectado"}}})
        bluetooth=link;info?.text="No relógio, abra Configurações → Bluetooth direto e escolha este celular."
        handler.postDelayed(expiry,300000);link.listen(adapter,address)
    }
    private fun allowed(candidate: String)=active && candidate==node && node.isNotBlank() && SystemClock.elapsedRealtime()<expires
    private fun closeBridge() {
        generation++;bluetooth?.close();bluetooth=null;cancellation?.cancel();cancellation=null;node="";expires=0
        handler.removeCallbacks(expiry);Wearable.getMessageClient(this).removeListener(this)
        work.queue.clear();MarkApi.clearSession()
    }
    override fun onMessageReceived(event: MessageEvent) {
        if(event.path!=WearProtocol.REQUEST || !allowed(event.sourceNodeId))return
        process(event.sourceNodeId,event.data){data->Wearable.getMessageClient(this).sendMessage(event.sourceNodeId,WearProtocol.RESPONSE,data)}
    }
    private fun process(origin:String,bytes:ByteArray,reply:(ByteArray)->Unit) {
        if(!allowed(origin))return
        val packet=runCatching {WearProtocol.decode(bytes)}.getOrNull()?:return
        if(!gate.accept(packet.id,SystemClock.elapsedRealtime()))return
        val lease=generation
        try {work.execute {
            if(!allowed(origin) || generation!=lease)return@execute
            val result=runCatching {
                val api=MarkApi(settings.serverUrl(),settings.serverPin(),DeviceIdentity())
                when(packet.kind) {
                    "status"->{require(packet.payload.length()==0 || (packet.payload.length()==1 && packet.payload.opt("telemetry") is Boolean))
                        api.check()
                        val b=registerReceiver(null,IntentFilter(android.content.Intent.ACTION_BATTERY_CHANGED))
                        JSONObject().put("pc","ONLINE").put("service","API disponível")
                            .put("phone_battery",b?.getIntExtra(BatteryManager.EXTRA_LEVEL,-1)?:-1)
                            .apply {if(packet.payload.optBoolean("telemetry",false)) put("telemetry",api.wearSummary())}}
                    "ask"->{require(packet.payload.length()==1)
                        val text=packet.payload.get("text") as? String ?: error("texto inválido")
                        require(WearProtocol.validText(text))
                        JSONObject().put("text",api.ask(text,quick=true,origin="wear").take(WearProtocol.MAX_TEXT))}
                    "telemetry"->{require(packet.payload.length()==1);api.wearTelemetry(packet.payload)}
                    else->error("tipo não autorizado")
                }
            }
            if(!allowed(origin) || generation!=lease)return@execute
            val data=if(result.isSuccess)WearProtocol.encode("reply",result.getOrThrow(),packet.id)
                else WearProtocol.encode("error",JSONObject().put("code","PC_UNAVAILABLE_OR_REQUEST_DENIED"),packet.id)
            runCatching {reply(data)}
        }}catch(_:RejectedExecutionException) {
            if(allowed(origin))runCatching {reply(WearProtocol.encode("error",JSONObject().put("code","BUSY"),packet.id))}
        }
    }
}

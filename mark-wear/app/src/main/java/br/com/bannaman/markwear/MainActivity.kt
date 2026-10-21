package br.com.bannaman.markwear

import android.Manifest
import android.app.Activity
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.hardware.Sensor
import android.os.*
import android.speech.RecognizerIntent
import android.widget.*
import com.google.android.gms.wearable.*
import br.com.bannaman.markwear.protocol.*
import org.json.JSONObject
import org.json.JSONArray
import java.util.UUID

class MainActivity: Activity(),MessageClient.OnMessageReceivedListener,WearLinkService.Listener {
    @Volatile private var bluetooth:BondedWearLink?=null
    private var bluetoothCode=""
    private val handler=Handler(Looper.getMainLooper())
    private val prefs by lazy {getSharedPreferences("mark.wear",MODE_PRIVATE)}
    private val sensors by lazy {SensorSession(this)}
    private val metrics by lazy {WearMetrics(this)}
    private var page="HOME"
    @Volatile private var active=false
    private var googleListener=false
    private var googleGeneration=0
    @Volatile private var phone=""
    private var state="PHONE OFFLINE"
    private var pc="UNAVAILABLE"
    private var pending=""
    private var pendingKind=""
    private var pendingDeadline=0L
    private var answer=""
    private var status=JSONObject()
    private var detail: TextView?=null
    private var renderedPage=""
    private var appsLoaded=false
    private var connectionLabel:TextView?=null
    private var answerLabel:TextView?=null
    private var cancelButton:Button?=null
    private var startedAt=0L
    private var retryAt=0L
    private var attempts=0
    private var deferredAsk=""
    private var voiceOpened=false
    private var voiceChannel:WearVoiceChannel?=null
    private var voiceInfo:TextView?=null
    private var conversation=false
    private var voiceStart:Button?=null
    private var voiceStop:Button?=null
    private var sendButton:Button?=null
    private var speechButton:Button?=null
    private var diagnosticText=""
    private val navigation=mutableListOf<String>()
    private val buttons=ButtonDispatcher()
    private val buttonFlush=Runnable {buttons.flush(SystemClock.elapsedRealtime())?.let {buttonAction(it)}}
    private var appGeneration=0
    private val appWorker=java.util.concurrent.ThreadPoolExecutor(1,1,0,java.util.concurrent.TimeUnit.SECONDS,java.util.concurrent.ArrayBlockingQueue<Runnable>(1))
    private var heart: Float?=null
    private var heartAt=0L
    private var quality=0
    private var heartMeasuring=false
    private var heartMessage=""
    private var mode=PowerMode.SMART
    private val timeout=Runnable {metrics.error();stopConversation();pending="";state="Sem resposta no prazo · tente novamente";render()}

    override fun onCreate(saved: Bundle?) {
        super.onCreate(saved)
        val home=android.content.ComponentName(this,"br.com.bannaman.markwear.HomeEntry")
        if(packageManager.getComponentEnabledSetting(home)!=PackageManager.COMPONENT_ENABLED_STATE_DISABLED) {
            packageManager.setComponentEnabledSetting(home,PackageManager.COMPONENT_ENABLED_STATE_DISABLED,PackageManager.DONT_KILL_APP)
            packageManager.clearPackagePreferredActivities(packageName)
        }
        if(componentName.className.endsWith(".HomeEntry")){returnToWatch();return}
        window.addFlags(android.view.WindowManager.LayoutParams.FLAG_SECURE)
        page=saved?.getString("page")?:WearPresentation.entryPage(intent.getStringExtra("mark_page"))
        navigation.addAll(saved?.getStringArrayList("trail")?.takeLast(12)?:emptyList())
        mode=runCatching {PowerMode.valueOf(prefs.getString("mode","SMART")!!)}.getOrDefault(PowerMode.SMART)
    }
    override fun onNewIntent(next:Intent){
        super.onNewIntent(next);setIntent(next)
        navigate(WearPresentation.entryPage(next.getStringExtra("mark_page")))
    }
    override fun onSaveInstanceState(out: Bundle) {super.onSaveInstanceState(out);out.putString("page",if(page=="LINK")"SETTINGS" else page);out.putStringArrayList("trail",ArrayList(navigation.takeLast(12)))}
    override fun onStart() {
        super.onStart();if(isFinishing)return;active=true;metrics.start();WearLinkService.attach(this)?.let {bluetooth=it}
        if(pending.isNotBlank()){if(SystemClock.elapsedRealtime()>=pendingDeadline){pending="";state="Prazo da resposta expirou"}else handler.postDelayed(timeout,pendingDeadline-SystemClock.elapsedRealtime())}
        startedAt=SystemClock.elapsedRealtime()
        updateGoogleListener()
        render()
        if(bluetooth?.isReady()==true){phone="bt";if(deferredAsk.isNotBlank()){val text=deferredAsk;deferredAsk="";ask(text)}else if(pending.isBlank())request("status",JSONObject())}
        else if(prefs.getString("transport","")!="bt")discover()
        else {state="Bluetooth · autorize · Configurações";render()}
    }
    override fun onStop() {
        if(page=="APPS" && !appsLoaded)renderedPage=""
        WearLinkService.detach(this)
        if(heartMeasuring)heartMessage="Medição interrompida ao sair; toque em Medir agora para repetir."
        active=false;heartMeasuring=false;stopConversation();buttons.cancel();appGeneration++;window.clearFlags(android.view.WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);sensors.stop();handler.removeCallbacksAndMessages(null)
        googleGeneration++
        if(googleListener){Wearable.getMessageClient(this).removeListener(this);googleListener=false}
        if(bluetooth?.isReady()!=true) {
            pending="";answer="";phone=""
            if(!voiceOpened){bluetooth?.close();bluetooth=null;bluetoothCode=""}
        }
        // An approved socket sleeps blocked in read(), with no rendering, sensor or mic.
        // The phone closes it at the five-minute lease deadline.
        if(!voiceOpened) {queueMetrics();metrics.save(sensors.activeMillis)}
        super.onStop()
    }
    @Deprecated("Deprecated in Java") override fun onBackPressed() {if(page!="HOME")goBack()else super.onBackPressed()}
    override fun onDestroy(){appGeneration++;appWorker.shutdownNow();WearLinkService.detach(this);if(!WearLinkService.holds(bluetooth))bluetooth?.close();bluetooth=null;super.onDestroy()}
    private fun buttonAction(action:ButtonAction) {when(action){ButtonAction.HEALTH->navigate("HEALTH");ButtonAction.ACTIONS->navigate("ACTIONS");ButtonAction.APPS->navigate("APPS")}}
    override fun dispatchKeyEvent(event:android.view.KeyEvent):Boolean {
        if(event.keyCode!=android.view.KeyEvent.KEYCODE_STEM_1)return super.dispatchKeyEvent(event)
        if(event.action==android.view.KeyEvent.ACTION_DOWN){buttons.press(event.eventTime,event.repeatCount);return true}
        if(event.action==android.view.KeyEvent.ACTION_UP){buttons.release(event.eventTime,event.isCanceled)?.let {buttonAction(it)};handler.removeCallbacks(buttonFlush);handler.postDelayed(buttonFlush,310);return true}
        return super.dispatchKeyEvent(event)
    }
    private fun openSystem(){try{startActivity(Intent(android.provider.Settings.ACTION_SETTINGS))}catch(_:android.content.ActivityNotFoundException){state="Configurações indisponíveis";render()}}
    private fun returnToWatch(){
        val query=Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_HOME)
        val original=packageManager.queryIntentActivities(query,0).filter {it.activityInfo.packageName!=packageName && it.activityInfo.exported && it.activityInfo.enabled}.maxByOrNull {it.priority}
        try {if(original!=null)startActivity(query.setComponent(android.content.ComponentName(original.activityInfo.packageName,original.activityInfo.name)));finish()}catch(_:android.content.ActivityNotFoundException){finish()}catch(_:SecurityException){finish()}
    }
    private fun restoreInterface() {
        android.app.AlertDialog.Builder(this).setTitle("Voltar à interface do relógio?")
            .setMessage("O mostrador selecionado será mantido. O MARK continua disponível na lista de apps.")
            .setPositiveButton("Voltar ao relógio") {_,_->
                packageManager.setComponentEnabledSetting(android.content.ComponentName(this,"br.com.bannaman.markwear.HomeEntry"),PackageManager.COMPONENT_ENABLED_STATE_DISABLED,PackageManager.DONT_KILL_APP)
                packageManager.clearPackagePreferredActivities(packageName);returnToWatch()
            }.setNegativeButton("Cancelar",null).show()
    }
    private fun feedback(message:String) {state=message;Toast.makeText(this,message,Toast.LENGTH_SHORT).show();render()}
    private fun stopConversation() {
        val wasTalking=conversation;conversation=false;voiceChannel?.close();voiceChannel=null
        if(wasTalking && pendingKind=="ask"){pending="";handler.removeCallbacks(timeout)}
        if(wasTalking)state="Canal encerrado · microfone desligado"
    }
    private fun goBack(){val previous=navigation.removeLastOrNull()?:"HOME";changePage(previous)}
    private fun linked()=phone.isNotBlank() && (phone!="bt" || bluetooth?.isReady()==true)
    private fun refreshControls(){
        val busy=pending.isNotBlank()
        voiceStart?.apply {
            text=WearPresentation.voiceAction(linked(),busy,conversation);contentDescription=text
            isEnabled=!busy && !conversation;alpha=if(isEnabled)1f else .45f
        }
        voiceStop?.apply {isEnabled=conversation;alpha=if(isEnabled)1f else .45f}
        sendButton?.isEnabled=!busy;speechButton?.isEnabled=!busy && !voiceOpened
        cancelButton?.visibility=if(busy)android.view.View.VISIBLE else android.view.View.GONE
    }
    private fun populateApps(box:LinearLayout) {
        appsLoaded=false
        val generation=++appGeneration
        WearUi.label(this,box,"Carregando aplicativos…",12)
        try {appWorker.execute {
            val query=Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER)
            val apps=try {packageManager.queryIntentActivities(query,0)}catch(_:SecurityException){runOnUiThread {if(active && page=="APPS" && generation==appGeneration)WearUi.label(this,box,"O sistema recusou a lista de aplicativos.")};return@execute}.filter {it.activityInfo.exported && it.activityInfo.enabled && it.activityInfo.applicationInfo.enabled}
                .distinctBy {it.activityInfo.packageName+"/"+it.activityInfo.name}.sortedBy {it.loadLabel(packageManager).toString().lowercase(java.util.Locale.getDefault())}.take(100)
            val labels=apps.associateWith {it.loadLabel(packageManager).toString().take(60)}
            val icons=apps.associateWith {app->runCatching {val source=app.loadIcon(packageManager);val bitmap=android.graphics.Bitmap.createBitmap(40,40,android.graphics.Bitmap.Config.ARGB_8888);source.setBounds(0,0,40,40);source.draw(android.graphics.Canvas(bitmap));android.graphics.drawable.BitmapDrawable(resources,bitmap)}.getOrNull()}
            runOnUiThread {
                if(!active || page!="APPS" || generation!=appGeneration)return@runOnUiThread
                appsLoaded=true
                val layout=LinearLayout(this).apply {orientation=LinearLayout.VERTICAL;setBackgroundColor(WearUi.INK);setPadding(WearUi.dp(this@MainActivity,30),WearUi.dp(this@MainActivity,24),WearUi.dp(this@MainActivity,30),WearUi.dp(this@MainActivity,24))}
                setContentView(layout);WearUi.label(this,layout,"Aplicativos",20,android.graphics.Color.WHITE)
                connectionLabel=WearUi.label(this,layout,WearPresentation.status(state),11)
                val list=ListView(this).apply {divider=null;isVerticalScrollBarEnabled=true}
                layout.addView(list,LinearLayout.LayoutParams(-1,0,1f))
                list.adapter=object:BaseAdapter() {
                    override fun getCount()=apps.size+1
                    override fun getItem(position:Int)=apps.getOrNull(position-1)
                    override fun getItemId(position:Int)=position.toLong()
                    override fun getView(position:Int,reuse:android.view.View?,parent:android.view.ViewGroup):android.view.View {
                        val row=(reuse as? Button)?:Button(this@MainActivity).apply {WearUi.styleButton(this@MainActivity,this)}
                        val app=getItem(position);row.text=labels[app]?:"Configurações do sistema";row.contentDescription=row.text
                        val icon=icons[app];icon?.setBounds(0,0,WearUi.dp(this@MainActivity,22),WearUi.dp(this@MainActivity,22));row.setCompoundDrawables(icon,null,null,null);row.compoundDrawablePadding=8
                        row.setOnClickListener {
                            if(app==null)openSystem() else try {startActivity(Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER).setComponent(android.content.ComponentName(app.activityInfo.packageName,app.activityInfo.name)))}
                            catch(_:android.content.ActivityNotFoundException){Toast.makeText(this@MainActivity,"Aplicativo indisponível",Toast.LENGTH_SHORT).show()}
                            catch(_:SecurityException){Toast.makeText(this@MainActivity,"Abertura recusada pelo sistema",Toast.LENGTH_SHORT).show()}
                        }
                        return row
                    }
                }
                WearUi.button(this,layout,"Voltar","back"){goBack()}

            }
        }}catch(_:java.util.concurrent.RejectedExecutionException){WearUi.label(this,box,"Lista ocupada; volte e tente novamente.")}
    }
    private fun updateGoogleListener() {
        val needed=active && prefs.getString("transport","")!="bt"
        if(needed && !googleListener){
            googleListener=true;val generation=++googleGeneration
            Wearable.getMessageClient(this).addListener(this).addOnFailureListener {
                if(active && generation==googleGeneration && prefs.getString("transport","")!="bt"){googleListener=false;state="Wear OS indisponível · use Bluetooth direto";render()}
            }
        }else if(!needed && googleListener){googleGeneration++;Wearable.getMessageClient(this).removeListener(this);googleListener=false}
    }
    private fun battery(): Int=registerReceiver(null,IntentFilter(Intent.ACTION_BATTERY_CHANGED))?.let {
        val scale=it.getIntExtra(BatteryManager.EXTRA_SCALE,100)
        if(scale>0) it.getIntExtra(BatteryManager.EXTRA_LEVEL,0)*100/scale else -1
    }?:-1
    private fun discover() {
        if(prefs.getString("transport","")=="bt")return
        if(SystemClock.elapsedRealtime()<retryAt)return
        Wearable.getCapabilityClient(this).getCapability("mark_phone_v1",CapabilityClient.FILTER_REACHABLE)
            .addOnSuccessListener {cap ->
                if(!active || prefs.getString("transport","")=="bt")return@addOnSuccessListener
                val selected=prefs.getString("phone","")
                val node=cap.nodes.firstOrNull {it.id==selected}
                phone=node?.id?:"";state=if(node==null) "Escolha o M23 em Configurações" else "M23 CONECTADO"
                attempts=0;render()
                if(phone.isNotBlank()) {
                    if(deferredAsk.isNotBlank()){val text=deferredAsk;deferredAsk="";ask(text)}
                    else request("status",JSONObject())
                }
            }.addOnFailureListener {if(active && prefs.getString("transport","")!="bt"){attempts++;retryAt=SystemClock.elapsedRealtime()+WearPower.backoff(attempts);state="PHONE OFFLINE";render()}}
    }
    private fun request(kind: String,payload: JSONObject) {
        if(!active || !linked()){state="PHONE OFFLINE";if(conversation)voiceChannel?.failed();render();return}
        if(pending.isNotBlank()){state="Aguarde a resposta atual";render();return}
        pending=UUID.randomUUID().toString().replace("-","");pendingKind=kind
        val data=runCatching {WearProtocol.encode(kind,payload,pending)}.getOrElse {pending="";state="Pedido inválido";render();return}
        metrics.sent(data.size)
        state=if(kind=="ask")"PROCESSING" else "Atualizando…";render()
        pendingDeadline=SystemClock.elapsedRealtime()+120000;handler.postDelayed(timeout,120000)
        if(phone=="bt") {
            runCatching {bluetooth?.send(data)?:error("No link")}.onFailure {handler.removeCallbacks(timeout);pending="";state="Bluetooth desconectado";if(conversation)voiceChannel?.failed();render()}
            return
        }
        val sentId=pending
        Wearable.getMessageClient(this).sendMessage(phone,WearProtocol.REQUEST,data).addOnFailureListener {
            if(active && pending==sentId){handler.removeCallbacks(timeout);pending="";state="PHONE OFFLINE";if(conversation)voiceChannel?.failed();render()}
        }
    }
    override fun onMessageReceived(event: MessageEvent) {
        if(!active || event.path!=WearProtocol.RESPONSE || event.sourceNodeId!=phone)return
        receive(event.data)
    }
    private fun receive(bytes:ByteArray) {
        metrics.received(bytes.size)
        val packet=runCatching {WearProtocol.decode(bytes)}.getOrNull()?:return
        runOnUiThread {
            if(packet.id!=pending || packet.kind !in setOf("reply","error"))return@runOnUiThread
            val kind=pendingKind;pending="";handler.removeCallbacks(timeout)
            if(packet.kind=="error") {state=packet.payload.optString("code","ERROR");pc="UNAVAILABLE"}
            else {
                if(!conversation || kind=="ask")state="ONLINE"
                when(kind) {
                    "ask"->{answer=packet.payload.optString("text").take(WearProtocol.MAX_TEXT);state="ANSWER READY"}
                    "status"->{status=packet.payload;pc=status.optString("pc","UNAVAILABLE")}
                    "telemetry"->{prefs.edit().remove("queue").apply();state="Telemetria sincronizada"}
                }
            }
            if(page=="VOICE" && conversation){if(packet.kind=="reply" && kind=="ask")voiceChannel?.speak(answer)else if(packet.kind=="error")voiceChannel?.failed()}
            if(active && kind=="telemetry")Toast.makeText(this,WearPresentation.status(state),Toast.LENGTH_SHORT).show()
            render()
        }
    }
    private fun navigate(to:String) {
        val trail=WearPresentation.navigationTrail(navigation,page,to);navigation.clear();navigation.addAll(trail);changePage(to)
    }
    private fun changePage(to:String) {
        if(page==to)return
        if(to!="VOICE")stopConversation()
        if(heartMeasuring){heartMeasuring=false;heartMessage="Medição interrompida ao sair; toque em Medir agora para repetir.";window.clearFlags(android.view.WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)}
        metrics.interaction();sensors.stop();appGeneration++;appWorker.queue.clear();page=to;render()
    }
    private fun render() {
        if(!active)return
        if(renderedPage==page && page in setOf("HOME","ASK","VOICE","APPS")) {
            connectionLabel?.text=WearPresentation.status(state);voiceInfo?.text=WearPresentation.status(state);refreshControls()
            answerLabel?.apply {text=answer;visibility=if(answer.isBlank())android.view.View.GONE else android.view.View.VISIBLE}
            cancelButton?.visibility=if(pending.isBlank())android.view.View.GONE else android.view.View.VISIBLE
            return
        }
        val drawnPage=page
        val scrollY=if(renderedPage==page)WearUi.scroll(this)?.scrollY?:0 else 0
        renderedPage=page;voiceStart=null;voiceStop=null;sendButton=null;speechButton=null;detail=null;voiceInfo=null;connectionLabel=null;answerLabel=null;cancelButton=null
        val box=WearUi.page(this,if(page=="HOME")"MARK" else mapOf("ASK" to "Perguntar", "HEALTH" to "Saúde", "STATUS" to "Estado", "SETTINGS" to "Configurações", "LAB" to "Sensores", "ACTIONS" to "Ações", "BTPEERS" to "Bluetooth direto", "LINK" to "Confirmar vínculo", "VOICE" to "Canal de voz", "WEATHER" to "Tempo · Google", "DIAGNOSTICS" to "Diagnóstico", "METRICS" to "Métricas locais", "LAUNCHER_SETUP" to "Integração", "GOOGLEPEERS" to "Celular · Wear OS")[page]?:"MARK",page=="HOME")
        if(page !in setOf("HOME","ASK","VOICE","APPS"))connectionLabel=WearUi.label(this,box,WearPresentation.status(state),11,WearUi.TEAL)
        when(page) {
            "VOICE"->{voiceInfo=WearUi.label(this,box,WearPresentation.status(state),14,WearUi.TEAL)
                voiceStart=WearUi.button(this,box,"Iniciar conversa","call"){if(linked())startVoiceChannel()else navigate("SETTINGS")}
                voiceStop=WearUi.button(this,box,"Encerrar canal","stop"){stopConversation();state="Canal encerrado · microfone desligado";render()}
                WearUi.label(this,box,"Fale e aguarde a resposta. Ao sair desta tela, o microfone desliga.",12)
                WearUi.label(this,box,"A voz do Android pode usar Google/internet.",11)
                WearUi.button(this,box,"Configurar conexão","settings"){navigate("SETTINGS")}}
            "WEATHER"->{WearUi.label(this,box,"Previsão do tempo do Google no próprio relógio.",13);WearUi.button(this,box,"Abrir Google Weather","weather"){openWeather()}}
            "LAUNCHER_SETUP"->{WearUi.label(this,box,"Integre o MARK ao relógio pelo sistema. A interface original permanece disponível.",13)
                WearUi.label(this,box,"Mostrador e botão superior pertencem ao Wear OS. Abra MARK pelo atalho auxiliar ou pela watchface.",12)
                WearUi.button(this,box,"Selecionar watchface MARK","status"){try{startActivity(Intent(android.app.WallpaperManager.ACTION_CHANGE_LIVE_WALLPAPER).putExtra(android.app.WallpaperManager.EXTRA_LIVE_WALLPAPER_COMPONENT,android.content.ComponentName(this,MarkWatchFace::class.java)))}catch(_:android.content.ActivityNotFoundException){feedback("Seletor indisponível; use a seleção do sistema")}}
                WearUi.button(this,box,"Botão auxiliar · sistema","settings"){openSystem()}
                WearUi.label(this,box,"No sistema, escolha MARK para o atalho do botão auxiliar, quando oferecido. Botão de energia e emergência permanecem originais.",12)
                WearUi.button(this,box,"Voltar à interface original","back"){restoreInterface()}}
            "HOME"->{connectionLabel=WearUi.label(this,box,WearPresentation.status(state),12,WearUi.TEAL).apply {maxLines=2}
                WearUi.quickRow(this,box,"Perguntar","chat","Voz","call",{navigate("ASK")},{navigate("VOICE")})
                WearUi.quickRow(this,box,"Saúde","health","Estado","status",{navigate("HEALTH")},{navigate("STATUS")})
                WearUi.button(this,box,"Aplicativos","apps"){navigate("APPS")}
                WearUi.section(this,box,"UTILIDADES")
                WearUi.button(this,box,"Tempo · Google","weather"){openWeather()}
                WearUi.button(this,box,"Calculadora","calculator"){startActivity(Intent(this,CalculatorActivity::class.java))}
                WearUi.button(this,box,"Ações rápidas","chat"){navigate("ACTIONS")}
                WearUi.button(this,box,"Configurações","settings"){navigate("SETTINGS")}
                WearUi.button(this,box,"Integração do relógio","status"){navigate("LAUNCHER_SETUP")}
                WearUi.button(this,box,"Voltar ao mostrador","back"){returnToWatch()}}
            "ASK"->{connectionLabel=WearUi.label(this,box,WearPresentation.status(state),13,WearUi.TEAL)
                val edit=EditText(this).apply {hint="Sua pergunta";setTextColor(android.graphics.Color.WHITE);maxLines=3;filters=arrayOf(android.text.InputFilter.LengthFilter(2000))}
                box.addView(edit,LinearLayout.LayoutParams(-1,-2))
                sendButton=WearUi.button(this,box,"Enviar","chat"){ask(edit.text.toString())}
                speechButton=WearUi.button(this,box,"Ditar pergunta","mic"){voice()}
                cancelButton=WearUi.button(this,box,"Cancelar pedido","stop"){pending="";deferredAsk="";handler.removeCallbacks(timeout);state="Pedido cancelado; resposta tardia ignorada";render()}.apply {visibility=if(pending.isBlank())android.view.View.GONE else android.view.View.VISIBLE}
                answerLabel=WearUi.label(this,box,answer,15,android.graphics.Color.WHITE).apply {visibility=if(answer.isBlank())android.view.View.GONE else android.view.View.VISIBLE}
            }
            "HEALTH"->{WearUi.label(this,box,heart?.let {"${it.toInt()} bpm"}?:"— bpm",32,android.graphics.Color.WHITE)
                WearUi.label(this,box,if(heartAt==0L)"Leitura indisponível · toque em Medir agora" else "${HealthReading.state(heart,quality,SystemClock.elapsedRealtime()-heartAt)} · há ${(SystemClock.elapsedRealtime()-heartAt)/1000}s")
                WearUi.label(this,box,"Medição local, sem diagnóstico médico. Atividade física ainda indisponível.",12)
                detail=WearUi.label(this,box,heartMessage,12)
                WearUi.button(this,box,if(heartMeasuring)"Medindo…" else if(prefs.getBoolean("health",false))"Medir agora" else "Permitir leitura local","health"){prefs.edit().putBoolean("health",true).apply();measureHeart()}.isEnabled=!heartMeasuring
                WearUi.button(this,box,"Apagar leitura local"){sensors.stop();heartMeasuring=false;window.clearFlags(android.view.WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);heart=null;heartAt=0;heartMessage="";feedback("Leitura local apagada")}}
            "STATUS"->{WearUi.label(this,box,"PC · ${WearPresentation.status(pc)}\nCelular · ${WearPresentation.status(state)}\nBateria relógio · ${battery()}%\nBateria celular · ${status.optString("phone_battery","—")}\nModo · ${modeLabel(mode)}")
                status.optJSONObject("telemetry")?.let {WearUi.label(this,box,"Amostras 24h · ${it.optInt("samples_24h")}\nQueda observada · ${it.opt("observed_drop_pct")}\nConsumo do MARK · UNAVAILABLE\nAutonomia estimada · UNAVAILABLE",12)}
                WearUi.button(this,box,"Atualizar","refresh"){if(bluetooth?.isReady()==true){phone="bt";request("status",JSONObject())}else if(prefs.getString("transport","")=="bt")navigate("BTPEERS")else discover()}}
            "APPS"->{populateApps(box)}
            "ACTIONS"->{WearUi.button(this,box,"Configurações do sistema","settings"){openSystem()}
                WearUi.button(this,box,"Relógio original"){returnToWatch()}
                for(q in listOf("Que horas são?","O que tenho hoje?","Pode descansar")) WearUi.button(this,box,q){navigate("ASK");ask(q)}}
            "SETTINGS"->{WearUi.label(this,box,"Autorize a conexão no M23. A sessão Bluetooth dura cinco minutos; ao expirar, é preciso autorizar novamente.",12)
                WearUi.button(this,box,"Bluetooth direto · sem Google"){navigate("BTPEERS")}
                if(bluetoothCode.isNotBlank()) {
                    WearUi.label(this,box,"Código $bluetoothCode",22,android.graphics.Color.WHITE)
                    WearUi.button(this,box,"Códigos iguais · confirmar"){confirmBluetoothCodes()}
                }
                WearUi.button(this,box,"Via Wear OS / Google"){WearLinkService.revoke(this);bluetooth?.close();bluetooth=null;phone="";prefs.edit().putString("transport","google").apply();updateGoogleListener();state="Buscando celular via Wear OS…";navigate("GOOGLEPEERS")}
                WearUi.section(this,box,"ENERGIA")
                for(m in PowerMode.entries)WearUi.button(this,box,if(mode==m)"✓ ${modeLabel(m)}" else modeLabel(m)){mode=m;prefs.edit().putString("mode",m.name).apply();feedback("Modo ${modeLabel(m)} ativado")}
                WearUi.section(this,box,"DADOS LOCAIS")
                WearUi.button(this,box,if(prefs.getBoolean("telemetry",false))"Pausar telemetria" else "Ativar telemetria técnica"){val enabled=!prefs.getBoolean("telemetry",false);prefs.edit().putBoolean("telemetry",enabled).apply();feedback(if(enabled)"Telemetria ativada" else "Telemetria pausada")}
                WearUi.button(this,box,"Sincronizar telemetria"){syncMetrics()}
                WearUi.button(this,box,"Limpar fila local"){prefs.edit().remove("queue").apply();feedback("Fila local limpa")}
                WearUi.button(this,box,"Consultar dados no PC"){navigate("STATUS");request("status",JSONObject().put("telemetry",true))}
                WearUi.button(this,box,"Pausar saúde e limpar leitura"){prefs.edit().putBoolean("health",false).apply();sensors.stop();heart=null;heartAt=0;feedback("Saúde pausada e leitura apagada")}
                WearUi.button(this,box,"Sensores"){navigate("LAB")}
                WearUi.button(this,box,"MARK Companion · gerenciamento","apps"){startActivity(android.content.Intent(this,WearCompanionActivity::class.java))}
                WearUi.button(this,box,"Integração do relógio"){navigate("LAUNCHER_SETUP")}
                WearUi.button(this,box,"Voltar à interface original","back"){restoreInterface()}
                WearUi.button(this,box,"Diagnóstico","status"){diagnosticText=WearHardware.diagnose(this).toString(2);navigate("DIAGNOSTICS")}
                WearUi.button(this,box,"Métricas técnicas locais"){navigate("METRICS")}
                WearUi.button(this,box,"Desconectar e revogar ponte","stop"){
                    android.app.AlertDialog.Builder(this).setTitle("Revogar conexão?").setMessage("Encerra o vínculo do MARK e limpa a fila técnica local. O pareamento Bluetooth do sistema permanece.")
                        .setPositiveButton("Revogar") {_,_->stopConversation();WearLinkService.revoke(this);bluetooth?.close();bluetooth=null;bluetoothCode="";pending="";handler.removeCallbacks(timeout);prefs.edit().remove("phone").remove("queue").putBoolean("telemetry",false).apply();phone="";feedback("Ponte revogada · celular desconectado")}.setNegativeButton("Cancelar",null).show()
                }}
            "GOOGLEPEERS"->{WearUi.label(this,box,"Mantenha a ponte Wear OS aberta no MARK Mobile.",12);choosePhone(box)}
            "BTPEERS"->{WearUi.label(this,box,"Escolha o M23. Abra também a ponte Bluetooth no celular.",12);populateBluetooth(box)}
            "LINK"->{WearUi.label(this,box,bluetoothCode,28,android.graphics.Color.WHITE)
                WearUi.label(this,box,"Confira este código no M23 antes de confirmar.",13)
                WearUi.button(this,box,"Códigos iguais · confirmar") {confirmBluetoothCodes()}
                WearUi.button(this,box,"Cancelar vínculo") {bluetooth?.close();bluetooth=null;bluetoothCode="";window.clearFlags(android.view.WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);navigate("SETTINGS")}}
            "DIAGNOSTICS"->{WearUi.label(this,box,diagnosticText.ifBlank {WearHardware.diagnose(this).toString(2)},12)}
            "METRICS"->{WearUi.label(this,box,metrics.summary(),12)}
            "LAB"->{detail=WearUi.label(this,box,"Toque em um sensor para uma leitura limitada.",12)
                WearUi.button(this,box,"Calibrar / verificar sensores","status"){startActivity(Intent(this,SensorCalibrationActivity::class.java))}
                sensors.sensors().forEach {s ->WearUi.button(this,box,"${s.name} · ${s.type}") {
                    detail?.text="${s.vendor} · resolução ${s.resolution} · faixa ${s.maximumRange} · ${s.power} mA declarados"
                    sensors.read(s,WearPower.sensorTimeout(mode),{v,a,_->detail?.append("\n${v.joinToString()} · precisão $a")},{e->detail?.append("\n$e")})
                }}}
        }
        if(page!="HOME")WearUi.button(this,box,"Voltar","back"){goBack()}
        refreshControls()
        if(scrollY>0)WearUi.scroll(this)?.let {scroll->scroll.post {if(active && page==drawnPage)scroll.scrollTo(0,scrollY)}}
    }
    @Suppress("MissingPermission") private fun populateBluetooth(box:LinearLayout) {
        if(Build.VERSION.SDK_INT>=31 && checkSelfPermission("android.permission.BLUETOOTH_CONNECT")!=PackageManager.PERMISSION_GRANTED){WearUi.button(this,box,"Permitir Bluetooth"){requestPermissions(arrayOf("android.permission.BLUETOOTH_CONNECT"),42)};return}
        val adapter=getSystemService(android.bluetooth.BluetoothManager::class.java).adapter
        if(adapter==null || !adapter.isEnabled){WearUi.label(this,box,"Bluetooth está desligado ou indisponível.");WearUi.button(this,box,"Abrir Bluetooth do sistema"){startActivity(Intent(android.provider.Settings.ACTION_BLUETOOTH_SETTINGS))};return}
        val peers=adapter.bondedDevices
        if(peers.isEmpty()){WearUi.label(this,box,"Nenhum celular pareado. Pareie o M23 no Bluetooth do sistema, sem copiar a conta Google.");WearUi.button(this,box,"Abrir Bluetooth do sistema"){startActivity(Intent(android.provider.Settings.ACTION_BLUETOOTH_SETTINGS))};return}
        peers.forEach {peer->WearUi.button(this,box,peer.name?.take(60)?:"Celular pareado"){
            WearLinkService.revoke(this);bluetooth?.close();prefs.edit().putString("transport","bt").apply();updateGoogleListener();state="Conectando Bluetooth…";bluetoothCode="";render()
            lateinit var link:BondedWearLink
            link=BondedWearLink(false,{code->runOnUiThread {if(active && bluetooth===link){bluetoothCode=code;page="LINK";state="Confira o código também no M23";window.addFlags(android.view.WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);handler.postDelayed({window.clearFlags(android.view.WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);if(bluetooth===link && !link.isReady())link.close()},60000);render()}}},
                {runOnUiThread {if(active && bluetooth===link){if(runCatching {WearLinkService.retain(this,link,this)}.isFailure){link.close();feedback("Não consegui manter a conexão fora da tela");return@runOnUiThread};metrics.connected();bluetoothCode="";phone="bt";page="HOME";window.clearFlags(android.view.WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);state="Bluetooth autorizado";render();request("status",JSONObject())}}},
                {bytes->if(active && bluetooth===link)receive(bytes)},
                {runOnUiThread {if(bluetooth===link){bluetoothCode="";window.clearFlags(android.view.WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);conversation=false;voiceChannel?.close();voiceChannel=null;state="Bluetooth desconectado";pending="";phone="";handler.removeCallbacks(timeout);render()}}})
            bluetooth=link;handler.postDelayed({if(bluetooth===link && !link.isReady())link.close()},120000);link.connect(peer)
        }}
    }
    override fun onWearPacket(link:BondedWearLink,bytes:ByteArray){if(active && bluetooth===link)receive(bytes)}
    override fun onWearClosed(link:BondedWearLink){
        if(bluetooth!==link)return
        bluetooth=null;phone="";pending="";handler.removeCallbacks(timeout);stopConversation();state="Sessão Bluetooth encerrada · autorize novamente";render()
    }
    private fun confirmBluetoothCodes(){
        val link=bluetooth
        runCatching {require(link!=null);link.confirm()}
            .onSuccess {if(link?.isReady()!=true)feedback("Confirme também no M23 para concluir o vínculo")}
            .onFailure {feedback("Bluetooth desconectado · conecte novamente")}
    }
    private fun choosePhone(box: LinearLayout) {
        Wearable.getCapabilityClient(this).getCapability("mark_phone_v1",CapabilityClient.FILTER_REACHABLE).addOnSuccessListener {cap ->
            if(!active || page!="GOOGLEPEERS" || !box.isAttachedToWindow)return@addOnSuccessListener
            if(cap.nodes.isEmpty())WearUi.label(this,box,"Nenhum MARK Mobile compatível encontrado.")
            cap.nodes.forEach {n->WearUi.button(this,box,n.displayName){prefs.edit().putString("phone",n.id).apply();navigate("HOME");discover()}}
        }.addOnFailureListener {if(active && page=="GOOGLEPEERS" && box.isAttachedToWindow)WearUi.label(this,box,"Wear OS indisponível. Use Bluetooth direto.")}
    }
    private fun ask(text: String) {
        if(!WearProtocol.validText(text)){state="Digite uma pergunta válida";render();return}
        request("ask",JSONObject().put("text",text))
    }
    private fun openWeather(){
        try {startActivity(Intent(Intent.ACTION_MAIN).setComponent(android.content.ComponentName("com.google.android.wearable.app","com.google.android.clockwork.home.weather.WeatherActivity")))}
        catch(_:android.content.ActivityNotFoundException){feedback("Google Weather não disponível neste firmware")}
        catch(_:SecurityException){feedback("Sistema recusou abrir Google Weather")}
    }
    private fun modeLabel(m:PowerMode)=when(m){PowerMode.SMART->"Inteligente";PowerMode.ULTRA_SAVE->"Economia";PowerMode.ACTIVE->"Ativo"}
    private fun startVoiceChannel(){
        if(!WearPresentation.canStartVoice(linked(),pending.isNotBlank(),conversation)){feedback("Conecte o celular e aguarde o pedido atual");return}
        if(phone.isBlank() || (phone=="bt" && bluetooth?.isReady()!=true)){state="Autorize a ponte com o M23 antes de entrar no canal";voiceInfo?.text=state;return}
        if(checkSelfPermission(Manifest.permission.RECORD_AUDIO)!=PackageManager.PERMISSION_GRANTED){requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO),43);return}
        voiceChannel?.close();conversation=true
        voiceChannel=WearVoiceChannel(this,{message->if(active && page=="VOICE"){state=message;voiceInfo?.text=message;refreshControls()}}, {spoken->if(active && page=="VOICE" && conversation){metrics.voice();ask(spoken)}}, {if(active && page=="VOICE"){conversation=false;voiceChannel?.close();voiceChannel=null;refreshControls()}})
        refreshControls();voiceChannel?.begin()
    }
    private fun voice() {
        if(!linked()){feedback("Conecte o celular antes de ditar uma pergunta");return}
        if(voiceOpened || pending.isNotBlank()){state="Aguarde ou cancele o pedido atual";render();return}
        metrics.voice();state="LISTENING";render()
        val intent=Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL,RecognizerIntent.LANGUAGE_MODEL_FREE_FORM).putExtra(RecognizerIntent.EXTRA_LANGUAGE,"pt-BR").putExtra(RecognizerIntent.EXTRA_PROMPT,"Fale com MARK")
        try {voiceOpened=true;startActivityForResult(intent,12)}catch(_:android.content.ActivityNotFoundException){voiceOpened=false;state="Reconhecimento indisponível; use texto";render()}
    }
    @Deprecated("Deprecated in Java") override fun onActivityResult(code: Int,result: Int,data: Intent?) {
        super.onActivityResult(code,result,data)
        if(code==12){voiceOpened=false;page="ASK";if(result!=RESULT_OK){state="Voz cancelada";deferredAsk="";render()}else {val text=data?.getStringArrayListExtra(RecognizerIntent.EXTRA_RESULTS)?.firstOrNull();if(text!=null){deferredAsk=text; if(active){if(bluetooth?.isReady()==true){deferredAsk="";phone="bt";ask(text)}else if(prefs.getString("transport","")=="bt"){deferredAsk="";feedback("Bluetooth desconectado · reconecte o celular")}else discover()}}}}
    }
    private fun measureHeart() {
        if(!prefs.getBoolean("health",false))return
        val permission=if(Build.VERSION.SDK_INT>=36) "android.permission.health.READ_HEART_RATE" else Manifest.permission.BODY_SENSORS
        if(checkSelfPermission(permission)!=PackageManager.PERMISSION_GRANTED){requestPermissions(arrayOf(permission),13);return}
        val sensor=sensors.sensors().firstOrNull {it.type==Sensor.TYPE_HEART_RATE}
        if(sensor==null){detail?.text="Sensor cardíaco não exposto pelo Android";return}
        heartMeasuring=true;heartMessage="Medindo por até45s. Use no pulso, ajuste a pulseira e mantenha o braço parado. No carregador ou fora do pulso não há leitura válida."
        window.addFlags(android.view.WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);render()
        sensors.read(sensor,45000,{v,a,captured->heartMeasuring=false;window.clearFlags(android.view.WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);if(active && page=="HEALTH"){heart=v[0];heartAt=captured;quality=a;heartMessage="Leitura local recebida. Precisão Android: $a";render()}},{e->heartMeasuring=false;window.clearFlags(android.view.WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);if(active && page=="HEALTH"){heartMessage=e;render()}})
    }
    override fun onRequestPermissionsResult(code: Int,permissions: Array<out String>,results: IntArray) {
        super.onRequestPermissionsResult(code,permissions,results)
        if(code==43 && active && page=="VOICE"){if(results.firstOrNull()==PackageManager.PERMISSION_GRANTED)startVoiceChannel()else feedback("Microfone não autorizado · canal desligado")}
        if(code==42 && results.firstOrNull()==PackageManager.PERMISSION_GRANTED && active){page="BTPEERS";render()}
        if(code==13 && active && page=="HEALTH"){if(results.firstOrNull()==PackageManager.PERMISSION_GRANTED)measureHeart()else feedback("Permissão de saúde não autorizada")}
    }
    private fun queueMetrics() {
        if(!prefs.getBoolean("telemetry",false) || !WearPower.optionalSync(mode,battery()))return
        if(battery()<0)return
        val events=runCatching {JSONArray(prefs.getString("queue","[]"))}.getOrDefault(JSONArray())
        val batteryIntent=registerReceiver(null,IntentFilter(Intent.ACTION_BATTERY_CHANGED))
        val charging=batteryIntent?.getIntExtra(BatteryManager.EXTRA_STATUS,-1) in listOf(BatteryManager.BATTERY_STATUS_CHARGING,BatteryManager.BATTERY_STATUS_FULL)
        val e=JSONObject().put("id",UUID.randomUUID().toString().replace("-", "")).put("at",System.currentTimeMillis()/1000)
            .put("battery",battery()).put("charging",charging).put("foreground_ms",(SystemClock.elapsedRealtime()-startedAt).coerceIn(0,86400000))
        events.put(e);val bounded=JSONArray();for(i in maxOf(0,events.length()-32) until events.length())bounded.put(events.get(i))
        prefs.edit().putString("queue",bounded.toString()).apply();startedAt=SystemClock.elapsedRealtime()
    }
    private fun syncMetrics() {
        if(!prefs.getBoolean("telemetry",false)){feedback("Telemetria pausada · ative antes de sincronizar");return}
        if(!WearPower.optionalSync(mode,battery())){feedback("Sincronização suspensa pelo modo de economia ou bateria baixa");return}
        queueMetrics();request("telemetry",JSONObject().put("events",runCatching {JSONArray(prefs.getString("queue","[]"))}.getOrDefault(JSONArray())))
        Toast.makeText(this,WearPresentation.status(state),Toast.LENGTH_SHORT).show()
    }
}

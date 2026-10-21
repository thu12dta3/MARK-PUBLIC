package br.com.bannaman.markwear

import android.app.Activity
import android.app.Instrumentation
import android.app.AlertDialog
import android.content.Intent
import android.os.Bundle
import android.view.View
import android.view.ViewGroup
import android.view.WindowManager
import android.widget.*

/** Owner-signed, opt-in device campaign. Not included in the distributed app APK. */
class WearSmokeInstrumentation:Instrumentation() {
    private val results=mutableListOf<String>()
    private var failures=0
    private var filter=""
    private var installLab=false
    private lateinit var activity:MainActivity
    override fun onCreate(arguments:Bundle?){super.onCreate(arguments);filter=arguments?.getString("filter","")?:"";installLab=arguments?.getString("install_lab")=="true";start()}
    private fun main(body:()->Unit){if(android.os.Looper.myLooper()==android.os.Looper.getMainLooper()){body();return};var thrown:Throwable?=null;runOnMainSync {try{body()}catch(t:Throwable){thrown=t}};thrown?.let {throw it}}
    private fun views(root:View):List<View> = listOf(root)+(if(root is ViewGroup)(0 until root.childCount).flatMap {views(root.getChildAt(it))}else emptyList())
    private fun controls():List<View>{var result=listOf<View>();main{result=views(activity.findViewById(android.R.id.content))};return result}
    private fun labels()=controls().filterIsInstance<TextView>().map {it.text.toString()}
    private fun button(text:String)=controls().filterIsInstance<Button>().first {it.text.toString()==text}
    private fun click(text:String){main{check(button(text).isEnabled){"Disabled: $text"};check(button(text).performClick()){text}};waitForIdleSync()}
    private fun expect(text:String){check(labels().any {text in it}){"Missing: $text"}}
    private fun home(){if(::activity.isInitialized)main{activity.finish()};activity=startActivitySync(Intent(targetContext,MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) as MainActivity;waitForIdleSync()}
    private fun case(name:String,body:()->Unit){
        if(filter.isNotEmpty() && filter !in name)return
        try{body();results.add("PASS: $name")}catch(t:Throwable){android.util.Log.e("MARK-Wear-Tests",name,t);failures++;results.add("FAIL: $name · ${t.javaClass.simpleName}: ${t.message?.take(160)}")}
        android.util.Log.i("MARK-Wear-Tests",results.last())
        sendStatus(0,Bundle().apply {putString("stream",results.last()+"\n")})
    }
    private fun waitUntil(body:()->Boolean){val deadline=android.os.SystemClock.elapsedRealtime()+12000;while(!body()){check(android.os.SystemClock.elapsedRealtime()<deadline){"Timed out"};Thread.sleep(100)}}
    private fun preview(name:String){main{
        val root=activity.findViewById<View>(android.R.id.content)
        val image=android.graphics.Bitmap.createBitmap(root.width,root.height,android.graphics.Bitmap.Config.ARGB_8888)
        val canvas=android.graphics.Canvas(image);canvas.drawColor(WearUi.INK);root.draw(canvas)
        val folder=java.io.File(targetContext.getExternalFilesDir(null),"wear-smoke-preview").apply {mkdirs()}
        java.io.FileOutputStream(java.io.File(folder,"$name.png")).use {image.compress(android.graphics.Bitmap.CompressFormat.PNG,100,it)};image.recycle()
    }}
    private fun numberField()=controls().filterIsInstance<EditText>().single()
    override fun onStart(){
        super.onStart()
        val preferences=targetContext.getSharedPreferences("mark.wear",0)
        val saved=preferences.all.filterKeys {it in setOf("mode","telemetry","transport")}
        try{
            preferences.edit().putString("transport","bt").putString("mode","SMART").putBoolean("telemetry",false).apply()
            case("Calibration: real hardware collection is bounded and does not auto-save"){
                val before=targetContext.getSharedPreferences("sensor-offsets",0).all.toMap()
                val a=startActivitySync(Intent(targetContext,SensorCalibrationActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) as SensorCalibrationActivity
                try{
                    main{check(a.window.attributes.flags and WindowManager.LayoutParams.FLAG_SECURE!=0);check(!targetContext.packageManager.getActivityInfo(android.content.ComponentName(targetContext,SensorCalibrationActivity::class.java),0).exported);SensorCalibrationActivity::class.java.getDeclaredMethod("measure").apply {isAccessible=true}.invoke(a)}
                    Thread.sleep(6800)
                    val running=SensorCalibrationActivity::class.java.getDeclaredField("running").apply {isAccessible=true};check(running.getBoolean(a)==false)
                    val stats=SensorCalibrationActivity::class.java.getDeclaredField("stats").apply {isAccessible=true}.get(a) as Map<*,*>
                    check((stats[1] as? RestCalibration)?.valid()==true){"Accelerometer did not provide enough accurate real samples"}
                    main{check(a.window.attributes.flags and WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON==0)}
                    check(targetContext.getSharedPreferences("sensor-offsets",0).all==before)
                }finally{main{a.finish()}}
            }
            case("Calibration: leaving cancels collection and clears screen wake"){
                val a=startActivitySync(Intent(targetContext,SensorCalibrationActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) as SensorCalibrationActivity
                try{
                    main{SensorCalibrationActivity::class.java.getDeclaredMethod("measure").apply {isAccessible=true}.invoke(a);callActivityOnPause(a);callActivityOnStop(a);check(a.window.attributes.flags and WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON==0)}
                    check(!SensorCalibrationActivity::class.java.getDeclaredField("running").apply {isAccessible=true}.getBoolean(a))
                    val pending=SensorCalibrationActivity::class.java.getDeclaredField("pending").apply {isAccessible=true}.get(a) as Map<*,*>;check(pending.isEmpty())
                }finally{main{a.finish()}}
            }
            case("Health: BPM number renders and clearing removes it"){
                home();click("Saúde")
                main{MainActivity::class.java.getDeclaredField("heart").apply {isAccessible=true}.set(activity,72f);MainActivity::class.java.getDeclaredField("heartAt").apply {isAccessible=true}.setLong(activity,android.os.SystemClock.elapsedRealtime());MainActivity::class.java.getDeclaredMethod("render").apply {isAccessible=true}.invoke(activity)}
                expect("72 bpm");click("Apagar leitura local");expect("— bpm");check(labels().none {it=="72 bpm"})
            }
            if(filter=="HeartReal")case("HeartReal: owner wrist acquisition shows an actual BPM number"){
                home();click("Saúde");main{targetContext.getSharedPreferences("mark.wear",0).edit().putBoolean("health",true).apply();MainActivity::class.java.getDeclaredMethod("measureHeart").apply {isAccessible=true}.invoke(activity)}
                val deadline=android.os.SystemClock.elapsedRealtime()+47000
                val heart=MainActivity::class.java.getDeclaredField("heart").apply {isAccessible=true}
                while(heart.get(activity)==null && android.os.SystemClock.elapsedRealtime()<deadline)Thread.sleep(200)
                val bpm=heart.get(activity) as? Float;check(bpm!=null && bpm in 25f..240f){"No valid heart-rate sample; requires owner wrist/contact"};check(labels().any {it=="${bpm!!.toInt()} bpm"});main{check(activity.window.attributes.flags and WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON==0)}
            }
            case("Home: four shortcuts and minimum tap area"){
                home();for(label in listOf("Perguntar","Voz","Saúde","Estado")){val b=button(label);main{check(b.height>=WearUi.dp(activity,48));check(b.hasOnClickListeners());check(b.contentDescription==label)}};main{check(button("Aplicativos").bottom<=activity.resources.displayMetrics.heightPixels-10)};preview("home")
            }
            case("Empty question gives visible validation"){
                home();click("Perguntar");click("Enviar");expect("Digite uma pergunta válida")
            }
            case("Offline question and dictation give feedback"){
                home();click("Perguntar");main{numberField().setText("Teste de conexão")};click("Enviar");expect("Celular desconectado");main{check(numberField().text.toString()=="Teste de conexão")};click("Ditar pergunta");expect("Conecte o celular")
            }
            case("Ask controls and return"){
                home();click("Perguntar");check(labels().none {it=="Cancelar pedido" && button(it).visibility==View.VISIBLE});click("Voltar");expect("MARK")
            }
            case("Offline voice offers connection and end is disabled"){
                home();click("Voz");expect("Conectar celular");preview("voice");check(!button("Encerrar canal").isEnabled);click("Conectar celular");expect("Configurações");click("Voltar");expect("Canal de voz")
            }
            case("Voice lifecycle never holds a microphone offline"){
                home();click("Voz");main{callActivityOnPause(activity);callActivityOnStop(activity)}
                val field=MainActivity::class.java.getDeclaredField("voiceChannel").apply {isAccessible=true};check(field.get(activity)==null)
                main{callActivityOnRestart(activity);callActivityOnStart(activity);callActivityOnResume(activity)};expect("Canal de voz");check(!button("Encerrar canal").isEnabled)
            }
            case("Status refresh routes to Bluetooth and back to status"){
                home();click("Estado");expect("Bateria relógio");click("Atualizar");expect("Bluetooth direto");click("Voltar");expect("Estado")
            }
            case("Energy modes acknowledge and preserve scroll"){
                home();click("Configurações");main{WearUi.scroll(activity)!!.scrollTo(0,220)}
                click("Economia");expect("Modo Economia ativado");expect("✓ Economia");main{check(WearUi.scroll(activity)!!.scrollY>0)}
                click("Inteligente");expect("✓ Inteligente")
            }
            case("Telemetry toggle acknowledgement"){
                preferences.edit().putBoolean("telemetry",false).apply();home();click("Configurações");click("Ativar telemetria técnica");expect("Telemetria ativada");click("Pausar telemetria");expect("Telemetria pausada");click("Sincronizar telemetria");expect("ative antes de sincronizar")
            }
            case("Diagnostics and metrics return to settings"){
                home();click("Configurações");preview("settings");click("Diagnóstico");expect("sensor_count");click("Voltar");expect("Configurações");click("Métricas técnicas locais");expect("Métricas locais");click("Voltar");expect("Configurações")
            }
            case("Health cleanup acknowledges and remains local"){
                home();click("Saúde");click("Apagar leitura local");expect("Leitura local apagada");expect("Leitura indisponível")
            }
            case("Sensor read is on demand and navigation stops it"){
                home();click("Configurações");click("Sensores");val sensor=controls().filterIsInstance<Button>().first {it.text.toString().endsWith(" · 1")};click(sensor.text.toString());waitUntil{labels().any {"precisão" in it}}
                click("Voltar");expect("Configurações")
                val field=MainActivity::class.java.getDeclaredField("sensors\$delegate").apply {isAccessible=true};val session=(field.get(activity) as Lazy<*>).value!!
                val callback=session.javaClass.getDeclaredField("callback").apply {isAccessible=true};check(callback.get(session)==null)
            }
            case("Apps catalog keeps a reusable ListView with real listeners"){
                home();click("Aplicativos");waitUntil{controls().any {it is ListView}}
                main{val list=controls().filterIsInstance<ListView>().single();check(list.adapter.count>=4);val row=list.adapter.getView(0,null,list) as Button;check(row.hasOnClickListeners());check(row.text=="Configurações do sistema")}
            }
            case("Reopening from watchface selects the requested page without duplicate Activity"){
                home();click("Configurações");main{activity.startActivity(Intent(activity,MainActivity::class.java).putExtra("mark_page","VOICE").addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))};waitUntil{labels().any {it=="Canal de voz"}};check(!activity.isFinishing)
                main{activity.startActivity(Intent(activity,MainActivity::class.java).putExtra("mark_page","HOME").addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))};waitUntil{labels().any {it=="MARK"}}
            }
            case("Wear OS transport opens its own page and registers reply handling"){
                home();click("Configurações");click("Via Wear OS / Google");expect("Celular · Wear OS");check(preferences.getString("transport","")=="google");val generation=MainActivity::class.java.getDeclaredField("googleGeneration").apply {isAccessible=true};check(generation.getInt(activity)>0)
                click("Voltar");expect("Configurações");preferences.edit().putString("transport","bt").apply()
            }
            case("Connection service refuses an Intent without an approved grant"){
                home();main{targetContext.startForegroundService(Intent(targetContext,WearLinkService::class.java))};waitForIdleSync()
                waitUntil{val manager=targetContext.getSystemService(android.app.ActivityManager::class.java);manager.getRunningServices(50).none {it.service.className==WearLinkService::class.java.name}}
                check(!WearLinkService.holds(null));Thread.sleep(6000);expect("MARK")
            }
            case("Companion endpoint obtains real state without Mobile or PC"){
                val endpoint=CompanionEndpoint(targetContext)
                val status=endpoint.execute(org.json.JSONObject().put("action","status"))
                check(status.getInt("api")==android.os.Build.VERSION.SDK_INT)
                check(status.getInt("battery") in 0..100)
                check(status.getString("version")=="0.4.4-sensors-rc")
                endpoint.close()
            }
            case("Companion inventory is paged and identifies protected packages"){
                val endpoint=CompanionEndpoint(targetContext)
                val apps=endpoint.execute(org.json.JSONObject().put("action","apps").put("offset",0)).getJSONArray("apps")
                check(apps.length() in 1..12)
                for(i in 0 until apps.length()){val app=apps.getJSONObject(i);if(br.com.bannaman.markwear.protocol.CompanionPolicy.protectedPackage(app.getString("package")))check(!app.getBoolean("removable"))}
                endpoint.close()
            }
            case("Companion refuses shell and protected APK transfers"){
                val endpoint=CompanionEndpoint(targetContext)
                check(runCatching {endpoint.execute(org.json.JSONObject().put("action","shell").put("command","id"))}.isFailure)
                val begin=org.json.JSONObject().put("action","begin").put("package","com.google.android.apps.maps").put("version",1).put("size",10).put("sha","a".repeat(64)).put("signer","a".repeat(64)).put("token","a".repeat(32))
                check(runCatching {endpoint.execute(begin)}.isFailure)
                endpoint.close()
            }
            case("Companion foreground service refuses launch without authorization"){
                val pending=WearCompanionService::class.java.getDeclaredField("pending").apply {isAccessible=true};check(pending.get(null)==null)
                val revoked=br.com.bannaman.markwear.protocol.BondedWearLink(false,{},{},{},{})
                try{
                    (revoked.javaClass.getDeclaredField("ready").apply {isAccessible=true}.get(revoked) as java.util.concurrent.atomic.AtomicBoolean).set(true)
                    pending.set(null,revoked);main{WearCompanionService.revoke(targetContext)}
                    check(pending.get(null)==null)
                    check((revoked.javaClass.getDeclaredField("closed").apply {isAccessible=true}.get(revoked) as java.util.concurrent.atomic.AtomicBoolean).get())
                }finally{pending.set(null,null);revoked.close()}
                main{targetContext.startForegroundService(Intent(targetContext,WearCompanionService::class.java))}
                waitUntil{val manager=targetContext.getSystemService(android.app.ActivityManager::class.java);manager.getRunningServices(50).none {it.service.className==WearCompanionService::class.java.name}}
                Thread.sleep(6000);check(WearCompanionService.current==null)
            }
            case("Companion_media: delayed refresh must not reopen a page after Back"){
                val current=WearCompanionService::class.java.getDeclaredField("current").apply {isAccessible=true}
                check(current.get(null)==null){"Run this scenario without a live Companion grant"}
                var screen:WearCompanionActivity?=null
                try{
                    // No Bluetooth, grant, foreground service or microphone is started by this fixture.
                    current.set(null,WearCompanionService())
                    screen=startActivitySync(Intent(targetContext,WearCompanionActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) as WearCompanionActivity
                    val opened=screen
                    fun texts():List<String>{var labels=listOf<String>();main{labels=views(opened.findViewById(android.R.id.content)).filterIsInstance<TextView>().map {it.text.toString()}};return labels}
                    fun tap(title:String){main{val button=views(opened.findViewById(android.R.id.content)).filterIsInstance<Button>().single {it.text.toString()==title};check(button.performClick())};waitForIdleSync()}
                    main{WearCompanionActivity::class.java.getDeclaredMethod("media").apply {isAccessible=true}.invoke(opened)}
                    check("Mídia do celular" in texts())
                    tap("Consultar / atualizar");tap("Voltar")
                    Thread.sleep(2000);waitForIdleSync()
                    check("Companion" in texts() && "Mídia do celular" !in texts()){"A delayed callback reopened the media page after Back"}
                }finally{if(screen!=null)main{screen!!.finish()};current.set(null,null)}
            }
            case("Companion_pending: leaving pairing clears wake flag and pending grant"){
                val screen=startActivitySync(Intent(targetContext,WearCompanionActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) as WearCompanionActivity
                val waiting=WearCompanionActivity::class.java.getDeclaredField("waiting").apply {isAccessible=true}
                val link=br.com.bannaman.markwear.protocol.BondedWearLink(false,{},{},{},{})
                try{
                    main{waiting.set(screen,link);screen.window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);callActivityOnPause(screen);callActivityOnStop(screen)}
                    check(waiting.get(screen)==null && !link.isReady())
                    val closed=br.com.bannaman.markwear.protocol.BondedWearLink::class.java.getDeclaredField("closed").apply {isAccessible=true}
                    check((closed.get(link) as java.util.concurrent.atomic.AtomicBoolean).get())
                    main{check(screen.window.attributes.flags and WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON==0);callActivityOnRestart(screen);callActivityOnStart(screen);callActivityOnResume(screen)}
                    main{check(views(screen.findViewById(android.R.id.content)).filterIsInstance<TextView>().any {"Tentativa cancelada" in it.text.toString()})}
                }finally{link.close();main{screen.finish()}}
            }
            case("Companion_media_pending: reopened page observes the outstanding reply"){
                val current=WearCompanionService::class.java.getDeclaredField("current").apply {isAccessible=true}
                check(current.get(null)==null){"Run without a live Companion grant"}
                val service=WearCompanionService()
                val link=br.com.bannaman.markwear.protocol.BondedWearLink(false,{},{},{},{})
                var screen:WearCompanionActivity?=null
                try{
                    // Model ready/pending UI state only. No socket, crypto grant or service is started.
                    val ready=link.javaClass.getDeclaredField("ready").apply {isAccessible=true}
                    (ready.get(link) as java.util.concurrent.atomic.AtomicBoolean).set(true)
                    service.javaClass.getDeclaredField("link").apply {isAccessible=true}.set(service,link)
                    service.javaClass.getDeclaredField("expiry").apply {isAccessible=true}.setLong(service,android.os.SystemClock.elapsedRealtime()+10000)
                    val pending=service.javaClass.getDeclaredField("mediaPending").apply {isAccessible=true}
                    val text=service.javaClass.getDeclaredField("mediaText").apply {isAccessible=true}
                    pending.set(service,"a".repeat(32));text.set(service,"Consultando celular…");current.set(null,service)
                    screen=startActivitySync(Intent(targetContext,WearCompanionActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) as WearCompanionActivity
                    val opened=screen
                    main{WearCompanionActivity::class.java.getDeclaredMethod("media").apply {isAccessible=true}.invoke(opened)}
                    pending.set(service,"");text.set(service,"Faixa de teste atualizada")
                    Thread.sleep(900);waitForIdleSync()
                    main{check(views(opened.findViewById(android.R.id.content)).filterIsInstance<TextView>().any {it.text.toString()=="Faixa de teste atualizada"})}
                }finally{if(screen!=null)main{screen!!.finish()};current.set(null,null);link.close()}
            }
            case("Companion_service_ui: startup and stop refresh the connection header"){
                val current=WearCompanionService::class.java.getDeclaredField("current").apply {isAccessible=true}
                check(current.get(null)==null)
                val service=WearCompanionService();val link=br.com.bannaman.markwear.protocol.BondedWearLink(false,{},{},{},{})
                var screen:WearCompanionActivity?=null
                try{
                    current.set(null,service)
                    screen=startActivitySync(Intent(targetContext,WearCompanionActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) as WearCompanionActivity
                    val opened=screen
                    val generation=opened.javaClass.getDeclaredField("pageGeneration").apply {isAccessible=true}
                    val refresh=opened.javaClass.getDeclaredMethod("awaitServiceState",Boolean::class.javaPrimitiveType,Long::class.javaPrimitiveType,Int::class.javaPrimitiveType).apply {isAccessible=true}
                    main{refresh.invoke(opened,true,generation.getLong(opened),10)}
                    val ready=link.javaClass.getDeclaredField("ready").apply {isAccessible=true};(ready.get(link) as java.util.concurrent.atomic.AtomicBoolean).set(true)
                    service.javaClass.getDeclaredField("link").apply {isAccessible=true}.set(service,link)
                    service.javaClass.getDeclaredField("expiry").apply {isAccessible=true}.setLong(service,android.os.SystemClock.elapsedRealtime()+10000)
                    fun shown(text:String):Boolean{var result=false;main{result=views(opened.findViewById(android.R.id.content)).filterIsInstance<TextView>().any {it.text.toString()==text}};return result}
                    waitUntil{shown("Gerenciamento conectado")}
                    main{refresh.invoke(opened,false,generation.getLong(opened),10)};link.close()
                    waitUntil{shown("Gerenciamento desconectado")}
                }finally{if(screen!=null)main{screen!!.finish()};current.set(null,null);link.close()}
            }
            case("Store stages an actual owner-signed APK and validates it on this firmware"){
                val file=java.io.File(targetContext.cacheDir,"store-instrumentation.apk")
                context.assets.open("mark-store-test.apk").use {src->file.outputStream().use {src.copyTo(it)}}
                val info=targetContext.packageManager.getPackageArchiveInfo(file.absolutePath,android.content.pm.PackageManager.GET_SIGNING_CERTIFICATES or (if(android.os.Build.VERSION.SDK_INT==28)android.content.pm.PackageManager.GET_SIGNATURES else 0))?:error("Fixture archive unavailable")
                val signer=br.com.bannaman.markwear.protocol.CompanionPolicy.hex(java.security.MessageDigest.getInstance("SHA-256").digest((info.signingInfo?:error("Fixture SigningInfo unavailable")).apkContentsSigners[0].toByteArray()))
                val token="d".repeat(32);val endpoint=CompanionEndpoint(targetContext)
                try{
                    endpoint.execute(org.json.JSONObject().put("action","begin").put("token",token).put("package",info.packageName).put("version",info.longVersionCode).put("size",file.length()).put("sha",br.com.bannaman.markwear.protocol.CompanionPolicy.hash(file)).put("signer",signer))
                    file.inputStream().use {src->val b=ByteArray(4096);var offset=0L;while(true){val n=src.read(b);if(n<0)break;val reply=endpoint.execute(org.json.JSONObject().put("action","chunk").put("token",token).put("offset",offset).put("data",android.util.Base64.encodeToString(b.copyOf(n),android.util.Base64.NO_WRAP)));offset+=n;check(reply.getLong("offset")==offset)}}
                    check(endpoint.execute(org.json.JSONObject().put("action","finish").put("token",token)).getBoolean("review"))
                    check("br.com.bannaman.markstoretest" in endpoint.review())
                    val allowed=targetContext.packageManager.canRequestPackageInstalls()
                    sendStatus(0,Bundle().apply {putString("stream","Store unknown-source permission available: $allowed\n")})
                    if(!allowed && installLab){val outcome=endpoint.install(laboratoryProbe=true);sendStatus(0,Bundle().apply {putString("stream","Official installer outcome: $outcome\n")});Thread.sleep(45000)}
                }finally {endpoint.close();file.delete()}
            }
            case("Android9 archive signature verification refuses a modified APK"){
                val original=java.io.File(targetContext.cacheDir,"store-original.apk");val modified=java.io.File(targetContext.cacheDir,"store-modified.apk")
                try{
                    context.assets.open("mark-store-test.apk").use {src->original.outputStream().use {src.copyTo(it)}}
                    java.util.zip.ZipFile(original).use {zip->java.util.zip.ZipOutputStream(modified.outputStream()).use {out->
                        zip.entries().asSequence().forEach {entry->out.putNextEntry(java.util.zip.ZipEntry(entry.name));if(!entry.isDirectory){val bytes=zip.getInputStream(entry).use {it.readBytes()};if(entry.name=="classes.dex")bytes[0]=(bytes[0].toInt() xor 1).toByte();out.write(bytes)};out.closeEntry()}
                    }}
                    val flags=android.content.pm.PackageManager.GET_SIGNING_CERTIFICATES or android.content.pm.PackageManager.GET_SIGNATURES
                    check(targetContext.packageManager.getPackageArchiveInfo(original.absolutePath,flags)?.signingInfo!=null)
                    check(targetContext.packageManager.getPackageArchiveInfo(modified.absolutePath,flags)==null)
                }finally{original.delete();modified.delete()}
            }
            case("BLE lab probe closes GATT and advertising after measuring capability"){
                val result=WearBleProbe(targetContext).run()
                check(result.startsWith("BLE_"));sendStatus(0,Bundle().apply {putString("stream","BLE probe result: $result\n")})
            }
            case("Protected windows remain secure; no blanket screen wake"){
                home();main{
                    val flags=activity.window.attributes.flags;check(flags and WindowManager.LayoutParams.FLAG_SECURE !=0);check(flags and WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON==0)
                    val control=button("Perguntar");check(control.filterTouchesWhenObscured)
                    val properties=arrayOf(android.view.MotionEvent.PointerProperties().apply {id=0;toolType=android.view.MotionEvent.TOOL_TYPE_FINGER})
                    val coordinates=arrayOf(android.view.MotionEvent.PointerCoords().apply {x=1f;y=1f;pressure=1f;size=1f})
                    for(obscured in listOf(false,true)){
                        val event=android.view.MotionEvent.obtain(0,0,android.view.MotionEvent.ACTION_DOWN,1,properties,coordinates,0,0,1f,1f,0,0,android.view.InputDevice.SOURCE_TOUCHSCREEN,if(obscured)android.view.MotionEvent.FLAG_WINDOW_IS_OBSCURED else 0)
                        try{check(control.onFilterTouchEventForSecurity(event)==!obscured)}finally{event.recycle()}
                    }
                }
            }
        }finally{
            if(::activity.isInitialized)main{activity.finish()}
            val editor=preferences.edit();for(key in listOf("mode","telemetry","transport")){when(val value=saved[key]){null->editor.remove(key);is Boolean->editor.putBoolean(key,value);is String->editor.putString(key,value)}};editor.commit()
        }
        finish(if(failures==0)Activity.RESULT_OK else Activity.RESULT_CANCELED,Bundle().apply {putString("stream",results.joinToString("\n")+"\nTests: ${results.size}, failures: $failures\n");putInt("tests",results.size);putInt("failures",failures)})
    }
}

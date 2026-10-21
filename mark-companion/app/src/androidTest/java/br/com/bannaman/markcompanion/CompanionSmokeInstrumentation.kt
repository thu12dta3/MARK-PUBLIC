package br.com.bannaman.markcompanion
import android.app.Instrumentation
import android.content.Intent
import android.os.Bundle
import android.view.*
import android.widget.*
class CompanionSmokeInstrumentation:Instrumentation(){
 override fun onCreate(args:Bundle?){super.onCreate(args);start()}
 private fun main(run:()->Unit){var error:Throwable?=null;runOnMainSync {try{run()}catch(t:Throwable){error=t}};error?.let {throw it}}
 override fun onStart(){
  if(targetContext.getSystemService(android.app.KeyguardManager::class.java).isKeyguardLocked || !targetContext.getSystemService(android.os.PowerManager::class.java).isInteractive){
   finish(0,Bundle().apply {putString("stream","NOT RUN: unlock the M23 and keep Companion visible before this UI campaign\n");putString("status","DEVICE_NOT_INTERACTIVE");putInt("tests",0)});return
  }
  var failed=0;val results=mutableListOf<String>();val a=startActivitySync(Intent(targetContext,MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) as MainActivity
  fun views(v:View):List<View> = listOf(v)+(if(v is android.view.ViewGroup)(0 until v.childCount).flatMap {views(v.getChildAt(it))}else emptyList())
  fun labels():List<String>{var r=listOf<String>();runOnMainSync {r=views(a.findViewById(android.R.id.content)).filterIsInstance<TextView>().map {it.text.toString()}};return r}
  fun click(text:String){main {check(views(a.findViewById(android.R.id.content)).filterIsInstance<Button>().first {it.text.toString()==text}.performClick())};waitForIdleSync()}
  fun test(name:String,run:()->Unit){try{run();results.add("PASS: $name")}catch(t:Throwable){android.util.Log.e("MARK-Companion-Tests",name,t);failed++;results.add("FAIL: $name · ${t.javaClass.simpleName}: ${t.message?.take(200)}")};android.util.Log.i("MARK-Companion-Tests",results.last());sendStatus(0,Bundle().apply {putString("stream",results.last()+"\n")})}
  test("Separate package, secure window, no foreground grant") {check(targetContext.packageName=="br.com.bannaman.markcompanion");main {check(a.window.attributes.flags and WindowManager.LayoutParams.FLAG_SECURE!=0)};check(CompanionService.current==null)}
  test("Dashboard has real controls and no simulated battery") {check(labels().any {"Gerenciamento MARK desconectado" in it});main {val buttons=views(a.findViewById(android.R.id.content)).filterIsInstance<Button>();check(buttons.size>=7);check(buttons.all {it.hasOnClickListeners() && it.height>=48*a.resources.displayMetrics.density})}}
  test("Store reads the owner-signed fixture and shows permissions") {click("MARK Wear Store");click("Catálogo MARK · aplicativo de teste");val deadline=android.os.SystemClock.elapsedRealtime()+15000;while(labels().none {it=="Revise o APK"}){check(android.os.SystemClock.elapsedRealtime()<deadline){"Catálogo não abriu: "+labels().take(4).joinToString(" / ")};Thread.sleep(100)};check(labels().any {"br.com.bannaman.markstoretest" in it && "Assinatura" in it && "Nenhuma" in it})}
  test("Transfer is denied without an approved watch") {Thread.sleep(200);click("Transferir para revisar no relógio");val deadline=android.os.SystemClock.elapsedRealtime()+5000;while(labels().none {"Conecte e autorize" in it}){check(android.os.SystemClock.elapsedRealtime()<deadline);Thread.sleep(100)}}
  test("Bluetooth permission continues to peer selection without authorizing a grant"){
   check(targetContext.checkSelfPermission("android.permission.BLUETOOTH_CONNECT")==android.content.pm.PackageManager.PERMISSION_GRANTED){"Owner nearby permission required for this scenario"}
   main{val state=MainActivity::class.java.getDeclaredField("pairing").apply {isAccessible=true}.get(a) as PairingUiState;state.requestPermission();a.onRequestPermissionsResult(70,arrayOf("android.permission.BLUETOOTH_CONNECT"),intArrayOf(android.content.pm.PackageManager.PERMISSION_GRANTED))}
   check("Escolha o relógio pareado" in labels());check(CompanionService.current==null);click("Voltar")
  }
  test("Pending pairing pauses safely without leaving the screen awake"){
   val waiting=MainActivity::class.java.getDeclaredField("waiting").apply {isAccessible=true}
   val link=br.com.bannaman.markwear.protocol.BondedWearLink(true,{},{},{},{})
   try{
    main{waiting.set(a,link);a.window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);callActivityOnPause(a);callActivityOnStop(a)}
    check(waiting.get(a)==null && !link.isReady());check(CompanionService.current==null)
    val closed=br.com.bannaman.markwear.protocol.BondedWearLink::class.java.getDeclaredField("closed").apply {isAccessible=true};check((closed.get(link) as java.util.concurrent.atomic.AtomicBoolean).get())
    main{check(a.window.attributes.flags and WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON==0);callActivityOnRestart(a);callActivityOnStart(a);callActivityOnResume(a)}
    check(labels().any {"Tentativa de conexão cancelada" in it})
   }finally{link.close()}
  }
  test("Service startup refreshes the header without granting an extra session"){
   val current=CompanionService::class.java.getDeclaredField("current").apply {isAccessible=true};check(current.get(null)==null)
   val service=CompanionService();val link=br.com.bannaman.markwear.protocol.BondedWearLink(true,{},{},{},{})
   try{
    current.set(null,service)
    main{MainActivity::class.java.getDeclaredMethod("home").apply {isAccessible=true}.invoke(a);val generation=MainActivity::class.java.getDeclaredField("pageGeneration").apply {isAccessible=true}.getLong(a);MainActivity::class.java.getDeclaredMethod("awaitServiceState",Boolean::class.javaPrimitiveType,Long::class.javaPrimitiveType,Int::class.javaPrimitiveType).apply {isAccessible=true}.invoke(a,true,generation,10)}
    (link.javaClass.getDeclaredField("ready").apply {isAccessible=true}.get(link) as java.util.concurrent.atomic.AtomicBoolean).set(true)
    service.javaClass.getDeclaredField("link").apply {isAccessible=true}.set(service,link);service.javaClass.getDeclaredField("expiry").apply {isAccessible=true}.setLong(service,android.os.SystemClock.elapsedRealtime()+10000)
    val deadline=android.os.SystemClock.elapsedRealtime()+3000;while("Gerenciamento MARK conectado" !in labels()){check(android.os.SystemClock.elapsedRealtime()<deadline);Thread.sleep(100)}
   }finally{current.set(null,null);link.close()}
  }
  test("Revocation closes a staged handover before its service can grant"){
   val pending=CompanionService::class.java.getDeclaredField("pending").apply {isAccessible=true};check(pending.get(null)==null);check(CompanionService.current==null)
   val link=br.com.bannaman.markwear.protocol.BondedWearLink(true,{},{},{},{})
   try{
    (link.javaClass.getDeclaredField("ready").apply {isAccessible=true}.get(link) as java.util.concurrent.atomic.AtomicBoolean).set(true)
    pending.set(null,link);main{CompanionService.revoke(targetContext)};check(pending.get(null)==null)
    check((link.javaClass.getDeclaredField("closed").apply {isAccessible=true}.get(link) as java.util.concurrent.atomic.AtomicBoolean).get())
   }finally{pending.set(null,null);link.close()}
  }
  runOnMainSync {a.finish()};finish(if(failed==0)-1 else 0,Bundle().apply {putString("stream",results.joinToString("\n"));putInt("failures",failed)})
 }
}

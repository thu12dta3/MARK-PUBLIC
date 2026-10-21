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

/** Restricted administration only. No PC credentials, shell, sensor sampling or microphone. */
@android.annotation.TargetApi(28)
class CompanionEndpoint(private val context:Context) {
 companion object {
  // Exact firmware physically returned "install/uninstall unsupported on Android Wear".
  fun directInstallUnsupported()=Build.VERSION.SDK_INT==28 && Build.DISPLAY=="PWDD.190617.074"
 }
 private val prefs=context.getSharedPreferences("companion-install",0)
 private val transfer=ApkTransfer(File(context.cacheDir,"companion")){SystemClock.elapsedRealtime()}
 init {File(context.cacheDir,"companion").deleteRecursively()}
 private val notifications=NotificationBudget()
 private var expected:JSONObject?=null
 private var staged:File?=null
 @Synchronized fun close(){StoreFileProvider.revoke(context);transfer.clear();staged?.delete();staged=null;expected=null}
 @Synchronized fun execute(p:JSONObject):JSONObject=when(p.getString("action")) {
  "status"->{require(p.length()==1);val b=context.registerReceiver(null,IntentFilter(Intent.ACTION_BATTERY_CHANGED));val pm=context.packageManager;val a=pm.getPackageInfo(context.packageName,0)
   JSONObject().put("name",Build.MODEL).put("api",Build.VERSION.SDK_INT).put("firmware",Build.DISPLAY.take(80)).put("abis",JSONArray(Build.SUPPORTED_ABIS.toList())).put("version",a.versionName).put("battery",b?.getIntExtra(BatteryManager.EXTRA_LEVEL,-1)?:-1).put("charging",(b?.getIntExtra(BatteryManager.EXTRA_PLUGGED,0)?:0)!=0).put("install",if(directInstallUnsupported())"Instalação direta indisponível neste firmware" else prefs.getString("result","Nenhuma instalação")).put("unknown_sources_allowed",pm.canRequestPackageInstalls()).put("direct_install_supported",!directInstallUnsupported())}
  "apps"->{require(p.length()==2);val start=p.getInt("offset");require(start in 0..5000);val pm=context.packageManager
   val apps=pm.queryIntentActivities(Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER),0).map {it.activityInfo.packageName}.distinct().sorted()
   val rows=JSONArray();apps.drop(start).take(12).forEach {name->val item=pm.getPackageInfo(name,0);rows.put(JSONObject().put("package",name).put("name",item.applicationInfo!!.loadLabel(pm).toString().take(80)).put("version",item.versionName?.take(40)).put("code",item.longVersionCode).put("removable",(item.applicationInfo!!.flags and ApplicationInfo.FLAG_SYSTEM)==0 && !CompanionPolicy.protectedPackage(name)))}
   JSONObject().put("apps",rows).put("next",if(start+12<apps.size)start+12 else -1)}
  "notification"->{require(p.length()==4 && WearProtocol.validId(p.getString("id")) && CompanionPolicy.packageName(p.getString("app")));val title=p.getString("title");require(title.length<=120 && WearProtocol.validText(title) && notifications.accept(SystemClock.elapsedRealtime()))
   val manager=context.getSystemService(NotificationManager::class.java);manager.createNotificationChannel(NotificationChannel("companion-titles","Notificações selecionadas do celular",NotificationManager.IMPORTANCE_DEFAULT))
   if(Build.VERSION.SDK_INT>=33 && context.checkSelfPermission(android.Manifest.permission.POST_NOTIFICATIONS)!=PackageManager.PERMISSION_GRANTED)return JSONObject().put("delivered",false).put("error","NOTIFICATION_PERMISSION_REQUIRED")
   manager.notify("companion:"+p.getString("id"),85,Notification.Builder(context,"companion-titles").setSmallIcon(R.drawable.mark_icon).setContentTitle(title).setContentText(p.getString("app").take(80)).setVisibility(Notification.VISIBILITY_PRIVATE).setAutoCancel(true).build());JSONObject().put("delivered",true)}
  "sensors"->{require(p.length()==1);val manager=context.getSystemService(android.hardware.SensorManager::class.java);val rows=JSONArray();manager.getSensorList(android.hardware.Sensor.TYPE_ALL).take(32).forEach {rows.put(JSONObject().put("name",it.name.take(100)).put("type",it.type))};JSONObject().put("sensors",rows)}
  "begin"->{require(p.length()==7);require(staged==null);val pkg=p.getString("package");require(CompanionPolicy.packageName(pkg) && !CompanionPolicy.protectedPackage(pkg) && CompanionPolicy.digest(p.getString("signer")) && p.getLong("version")>0)
   val offset=transfer.begin(p.getString("token"),p.getLong("size"),p.getString("sha"));expected=JSONObject(p.toString());JSONObject().put("offset",offset)}
  "chunk"->{require(p.length()==4);val encoded=p.getString("data");require(encoded.length<=5464);val bytes=Base64.decode(encoded,Base64.NO_WRAP);try{JSONObject().put("offset",transfer.append(p.getString("token"),p.getLong("offset"),bytes))}finally{bytes.fill(0)}}
  "finish"->{require(p.length()==2);val file=transfer.finish(p.getString("token"));try{validate(file,expected!!);staged=file;prefs.edit().putString("result","Arquivo validado; confirme no relógio").apply();JSONObject().put("review",true).put("result","VALIDATED_AWAITING_WATCH_CONFIRMATION")}catch(e:Exception){file.delete();expected=null;throw e}}
  "cancel"->{require(p.length()==1);close();JSONObject().put("cancelled",true)}
  else->error("UNSUPPORTED_ACTION")
 }
 private fun validate(file:File,meta:JSONObject):PackageInfo {
  boundedApkArchive(file)
  val pm=context.packageManager;val info=pm.getPackageArchiveInfo(file.absolutePath,PackageManager.GET_SIGNING_CERTIFICATES or PackageManager.GET_PERMISSIONS or PackageManager.GET_CONFIGURATIONS or (if(Build.VERSION.SDK_INT==28)PackageManager.GET_SIGNATURES else 0))?:error("INVALID_APK")
  require(info.packageName==meta.getString("package") && info.longVersionCode==meta.getLong("version"));require(info.splitNames.isNullOrEmpty())
  val cert=info.signingInfo?.apkContentsSigners?:error("UNSIGNED_APK");require(cert.size==1)
  val sha=CompanionPolicy.hex(MessageDigest.getInstance("SHA-256").digest(cert[0].toByteArray()));require(sha==meta.getString("signer"))
  val abis=ZipFile(file).use {z->z.entries().asSequence().filter {it.name.startsWith("lib/") && it.name.endsWith(".so")}.map {it.name.split('/')[1]}.toSet()}
  require(CompanionPolicy.compatible(info.applicationInfo!!.minSdkVersion,Build.VERSION.SDK_INT,abis,Build.SUPPORTED_ABIS.toSet()))
  for(f in info.reqFeatures.orEmpty())require(f.flags and FeatureInfo.FLAG_REQUIRED==0 || f.name==null || pm.hasSystemFeature(f.name))
  val old=runCatching {pm.getPackageInfo(info.packageName,PackageManager.GET_SIGNING_CERTIFICATES)}.getOrNull()
  if(old!=null){require((old.applicationInfo!!.flags and ApplicationInfo.FLAG_SYSTEM)==0 && old.longVersionCode<=info.longVersionCode);require(old.signingInfo!!.apkContentsSigners.toSet()==cert.toSet())}
  require(info.requestedPermissions.orEmpty().none {it in setOf("android.permission.INSTALL_PACKAGES","android.permission.DELETE_PACKAGES","android.permission.WRITE_SECURE_SETTINGS")})
  return info
 }
 @Synchronized fun review():String {val f=staged?:return "Nenhum APK aguardando confirmação";val m=expected?:return "Metadados ausentes";val info=validate(f,m);return "${info.packageName}\nVersão ${info.versionName}\n${f.length()} bytes\nSHA256 ${m.getString("sha")}\nAssinatura ${m.getString("signer")}\nPermissões:\n${info.requestedPermissions.orEmpty().joinToString("\n").ifBlank {"Nenhuma"}}"}
 @Synchronized fun install(laboratoryProbe:Boolean=false):String {
  if(directInstallUnsupported() && !laboratoryProbe)return "Instalação direta indisponível neste firmware. Arquivo validado exige instalação auxiliar no laboratório."
  val file=staged?:error("NO_STAGED_APK");val info=validate(file,expected!!);val pm=context.packageManager
  if(!pm.canRequestPackageInstalls()){
   val uri=StoreFileProvider.publish(context,file)
   val intent=Intent(Intent.ACTION_INSTALL_PACKAGE).setDataAndType(uri,"application/vnd.android.package-archive").addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_GRANT_READ_URI_PERMISSION).apply {clipData=ClipData.newRawUri("MARK APK",uri)}
   val resolved=pm.resolveActivity(intent,0)
   if(resolved==null || (resolved.activityInfo.applicationInfo.flags and ApplicationInfo.FLAG_SYSTEM)==0){StoreFileProvider.revoke(context);return "Instalação direta bloqueada: instalador oficial indisponível"}
   intent.component=ComponentName(resolved.activityInfo.packageName,resolved.activityInfo.name)
   prefs.edit().putString("result","Aguardando confirmação do instalador oficial").apply()
   try{context.startActivity(intent)}catch(e:Exception){StoreFileProvider.revoke(context);throw e}
   return "Instalador oficial aberto; confirme ou consulte o erro na tela do Android."
  }
  val installer=pm.packageInstaller;val params=PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL).apply {setAppPackageName(info.packageName);setSize(file.length())}
  val id=installer.createSession(params)
  try{installer.openSession(id).use {session->session.openWrite("base.apk",0,file.length()).use {dest->file.inputStream().use {it.copyTo(dest)};session.fsync(dest)}
   prefs.edit().putString("expected_package",info.packageName).putLong("expected_version",info.longVersionCode).putInt("session",id).putString("result","Aguardando resultado do Android").apply()
   val intent=Intent(context,CompanionInstallResult::class.java).setAction("br.com.bannaman.markwear.INSTALL_RESULT")
   val flags=PendingIntent.FLAG_UPDATE_CURRENT or if(Build.VERSION.SDK_INT>=31)PendingIntent.FLAG_MUTABLE else 0
   session.commit(PendingIntent.getBroadcast(context,id,intent,flags).intentSender)}
   close();return "Enviado ao instalador; aguarde a confirmação do Android."
  }catch(e:Exception){runCatching {installer.abandonSession(id)};throw e}
 }
}

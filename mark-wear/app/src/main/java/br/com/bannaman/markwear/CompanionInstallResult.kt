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
class CompanionInstallResult:BroadcastReceiver(){
 override fun onReceive(context:Context,intent:Intent){
  if(intent.action!="br.com.bannaman.markwear.INSTALL_RESULT")return
  val prefs=context.getSharedPreferences("companion-install",0);if(intent.getIntExtra(PackageInstaller.EXTRA_SESSION_ID,-1)!=prefs.getInt("session",-2))return
  val status=intent.getIntExtra(PackageInstaller.EXTRA_STATUS,PackageInstaller.STATUS_FAILURE)
  if(status==PackageInstaller.STATUS_PENDING_USER_ACTION){
   val confirmation=intent.getParcelableExtra<Intent>(Intent.EXTRA_INTENT)
   val result=runCatching {require(confirmation!=null);context.startActivity(confirmation!!.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))}
   prefs.edit().putString("result",if(result.isSuccess)"Confirme a instalação na tela do Android" else "Firmware não oferece confirmação utilizável; instalação não comprovada").apply()
   if(result.isFailure)runCatching {context.packageManager.packageInstaller.abandonSession(prefs.getInt("session",-1))};return
  }
  val pkg=prefs.getString("expected_package","")!!;val actual=runCatching {context.packageManager.getPackageInfo(pkg,0).longVersionCode}.getOrNull()
  val verified=status==PackageInstaller.STATUS_SUCCESS && actual==prefs.getLong("expected_version",-1)
  prefs.edit().putString("result",if(verified)"Instalado e verificado: $pkg" else "Não instalado: resultado Android $status").apply()
 }
}


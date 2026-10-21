package br.com.bannaman.markcompanion

import android.app.Notification
import android.app.NotificationManager
import android.content.*
import android.media.session.*
import android.os.SystemClock
import android.service.notification.*
import br.com.bannaman.markwear.protocol.*
import org.json.*
import java.security.MessageDigest
import java.util.concurrent.*

object PhoneUtilities {
 fun access(c:Context)=c.getSystemService(NotificationManager::class.java).isNotificationListenerAccessGranted(ComponentName(c,MarkNotifications::class.java))
 fun media(c:Context,p:JSONObject):JSONObject {
  val prefs=c.getSharedPreferences("utilities",0);check(prefs.getBoolean("media",false) && access(c)){"MEDIA_PERMISSION_REQUIRED"}
  val sessions=c.getSystemService(MediaSessionManager::class.java).getActiveSessions(ComponentName(c,MarkNotifications::class.java))
  val current=sessions.firstOrNull {it.playbackState?.state==PlaybackState.STATE_PLAYING}?:sessions.firstOrNull()?:error("NO_ACTIVE_MEDIA")
  when(p.getString("action")) {
   "media_status"->require(p.length()==1)
   "media_control"->{require(p.length()==2);when(p.getString("command")){"play"->current.transportControls.play();"pause"->current.transportControls.pause();"next"->current.transportControls.skipToNext();"previous"->current.transportControls.skipToPrevious();else->error("UNSUPPORTED_MEDIA_CONTROL")}}
   else->error("UNSUPPORTED_ACTION")
  }
  val metadata=current.metadata
  return JSONObject().put("package",current.packageName).put("title",metadata?.getString(android.media.MediaMetadata.METADATA_KEY_TITLE)?.take(120)?:"Mídia ativa").put("playing",current.playbackState?.state==PlaybackState.STATE_PLAYING)
 }
}

/** Opt-in selected apps, title only, no disk queue or content logging. */
class MarkNotifications:NotificationListenerService(){
 private val work=ThreadPoolExecutor(1,1,0,TimeUnit.SECONDS,ArrayBlockingQueue<Runnable>(4))
 private val budget=NotificationBudget()
 override fun onNotificationPosted(s:StatusBarNotification){
  val allowed=getSharedPreferences("utilities",0).getStringSet("notification_apps",emptySet()).orEmpty()
  if(s.packageName !in allowed || s.packageName==packageName || s.notification.visibility==Notification.VISIBILITY_SECRET || s.isOngoing || CompanionService.current?.connected!=true)return
  val title=s.notification.extras.getCharSequence(Notification.EXTRA_TITLE)?.toString()?.take(120)?:return
  if(!budget.accept(SystemClock.elapsedRealtime()) || !WearProtocol.validText(title))return
  val id=CompanionPolicy.hex(MessageDigest.getInstance("SHA-256").digest(s.key.toByteArray())).take(32)
  val payload=JSONObject().put("action","notification").put("id",id).put("title",title).put("app",s.packageName)
  try{work.execute {runCatching {CompanionService.current?.takeIf {it.connected}?.request(payload)}}}catch(_:RejectedExecutionException){}
 }
 override fun onDestroy(){work.shutdownNow();super.onDestroy()}
}

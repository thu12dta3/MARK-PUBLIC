package br.com.bannaman.markwear
import android.content.*
import android.database.*
import android.net.Uri
import android.os.ParcelFileDescriptor
import android.provider.OpenableColumns
import java.io.File
import java.util.UUID

/** One validated APK, opaque grant URI, no paths supplied by callers or network API. */
class StoreFileProvider:ContentProvider(){
 companion object {
  @Volatile private var file:File?=null
  @Volatile private var uri:Uri?=null
  @Synchronized fun publish(c:Context,apk:File):Uri {
   require(apk.isFile && apk.canonicalFile.parentFile==File(c.cacheDir,"companion").canonicalFile)
   revoke(c);file=apk
   return Uri.Builder().scheme("content").authority(c.packageName+".store").appendPath("apk").appendPath(UUID.randomUUID().toString()).build().also {uri=it}
  }
  @Synchronized fun revoke(c:Context){uri?.let {runCatching {c.revokeUriPermission(it,Intent.FLAG_GRANT_READ_URI_PERMISSION)}};uri=null;file=null}
  @Synchronized private fun lookup(value:Uri):File {require(value==uri && value.query==null && value.fragment==null);return file?.takeIf {it.isFile}?:error("APK_UNAVAILABLE")}
 }
 override fun onCreate()=true
 override fun getType(value:Uri):String{lookup(value);return "application/vnd.android.package-archive"}
 override fun openFile(value:Uri,mode:String):ParcelFileDescriptor{require(mode=="r");return ParcelFileDescriptor.open(lookup(value),ParcelFileDescriptor.MODE_READ_ONLY)}
 override fun query(value:Uri,projection:Array<out String>?,selection:String?,args:Array<out String>?,sort:String?):Cursor {
  val apk=lookup(value);val names=projection?:arrayOf(OpenableColumns.DISPLAY_NAME,OpenableColumns.SIZE)
  require(names.all {it in setOf(OpenableColumns.DISPLAY_NAME,OpenableColumns.SIZE)})
  return MatrixCursor(names).apply {addRow(names.map {if(it==OpenableColumns.DISPLAY_NAME)"reviewed.apk" else apk.length()})}
 }
 override fun insert(value:Uri,values:ContentValues?):Uri?=throw UnsupportedOperationException()
 override fun delete(value:Uri,selection:String?,args:Array<out String>?):Int=throw UnsupportedOperationException()
 override fun update(value:Uri,values:ContentValues?,selection:String?,args:Array<out String>?):Int=throw UnsupportedOperationException()
}

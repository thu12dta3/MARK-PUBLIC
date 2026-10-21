package br.com.bannaman.markwear.protocol

import java.io.File
import java.io.FileOutputStream
import java.security.MessageDigest
import java.util.UUID

object CompanionPolicy {
    val SERVICE:UUID=UUID.fromString("2c09f06b-c34c-4c41-a736-11c421d134a1")
    const val MAX_APK=8*1024*1024
    const val CHUNK=4096
    fun hex(bytes:ByteArray)=bytes.joinToString(""){"%02x".format(it)}
    fun hash(file:File):String {val digest=MessageDigest.getInstance("SHA-256");file.inputStream().use {s->val b=ByteArray(8192);while(true){val n=s.read(b);if(n<0)break;digest.update(b,0,n)}};return hex(digest.digest())}
    fun digest(value:String)=Regex("[0-9a-f]{64}").matches(value)
    fun packageName(value:String)=value.length<=160 && Regex("[a-zA-Z][a-zA-Z0-9_]*(\\.[a-zA-Z][a-zA-Z0-9_]*)+").matches(value)
    fun protectedPackage(value:String)=value=="android" || listOf("com.android.","com.google.","com.mobvoi.","br.com.bannaman.mark").any {value.startsWith(it)} && value!="br.com.bannaman.markstoretest"
    fun compatible(min:Int,api:Int,native:Set<String>,abis:Set<String>)=min<=api && (native.isEmpty() || native.any {it in abis})
}

/** One bounded file, exact offsets, expiry; never trusts a filename supplied by a peer. */
class ApkTransfer(private val directory:File,private val clock:()->Long) {
    private var file:File?=null
    private var output:FileOutputStream?=null
    private var expiry=0L
    private var size=0L
    private var checksum=""
    var offset=0L;private set
    var token="";private set
    @Synchronized fun begin(id:String,total:Long,sha:String):Long {
        require(WearProtocol.validId(id) && total in 1..CompanionPolicy.MAX_APK && CompanionPolicy.digest(sha))
        clear();directory.mkdirs();file=File.createTempFile("mark-store-",".apk",directory);output=FileOutputStream(file!!)
        token=id;size=total;checksum=sha;expiry=clock()+180000;return offset
    }
    @Synchronized fun append(id:String,position:Long,bytes:ByteArray):Long {
        require(id==token && token.isNotEmpty() && clock()<expiry && position==offset && bytes.size in 1..CompanionPolicy.CHUNK && offset+bytes.size<=size)
        output!!.write(bytes);offset+=bytes.size;return offset
    }
    @Synchronized fun finish(id:String):File {
        require(id==token && token.isNotEmpty() && clock()<expiry && offset==size)
        output!!.fd.sync();output!!.close();output=null
        val result=file!!
        if(CompanionPolicy.hash(result)!=checksum){clear();error("CHECKSUM_MISMATCH")}
        file=null;token="";offset=0;return result
    }
    @Synchronized fun clear(){runCatching {output?.close()};output=null;file?.delete();file=null;token="";offset=0;expiry=0;size=0;checksum=""}
}

/** Event budget independent of clocks being reset; no busy polling. */
class NotificationBudget {
 private var start=-1L;private var count=0
 @Synchronized fun accept(now:Long):Boolean {
  if(now<0)return false
  if(start<0 || now<start || now-start>=60000){start=now;count=0}
  if(count>=20)return false
  count++;return true
 }
}

/** Inspect ZIP metadata before the platform parser allocates manifest/resource tables. */
fun boundedApkArchive(file:File) {
 require(file.length() in 1..CompanionPolicy.MAX_APK)
 java.util.zip.ZipFile(file).use {zip->
  val names=HashSet<String>();var total=0L;var count=0
  zip.entries().asSequence().forEach {entry->
   count++;require(count<=4096 && entry.name.length<=240 && !entry.name.startsWith('/') && ".." !in entry.name.split('/') && names.add(entry.name))
   require(entry.size in 0..(32L*1024*1024));total+=entry.size;require(total<=64L*1024*1024)
   if(entry.name=="AndroidManifest.xml")require(entry.size<=512*1024)
   if(entry.name=="resources.arsc")require(entry.size<=8*1024*1024)
  }
  require("AndroidManifest.xml" in names)
 }
}

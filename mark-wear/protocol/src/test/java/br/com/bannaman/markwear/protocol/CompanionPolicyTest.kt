package br.com.bannaman.markwear.protocol
import org.junit.Test
import org.junit.Assert.*
import java.nio.file.Files
class CompanionPolicyTest {
 private fun denied(run:()->Unit){try{run();fail("Expected rejection")}catch(_:IllegalArgumentException){}}
 @Test fun protectedPackages(){assertTrue(CompanionPolicy.protectedPackage("com.google.android.apps.maps"));assertTrue(CompanionPolicy.protectedPackage("br.com.bannaman.markmobile"));assertFalse(CompanionPolicy.protectedPackage("br.com.bannaman.markstoretest"));assertTrue(CompanionPolicy.packageName("org.example.app"));assertFalse(CompanionPolicy.packageName("../escape"))}
 @Test fun compatibility(){assertTrue(CompanionPolicy.compatible(25,28,emptySet(),setOf("armeabi-v7a")));assertFalse(CompanionPolicy.compatible(29,28,emptySet(),setOf("armeabi-v7a")));assertFalse(CompanionPolicy.compatible(25,28,setOf("arm64-v8a"),setOf("armeabi-v7a")))}
 @Test fun boundedTransfer(){val dir=Files.createTempDirectory("mark-test").toFile();var now=10L;val t=ApkTransfer(dir){now};val id="a".repeat(32);val src=java.io.File(dir,"source").apply {writeBytes(byteArrayOf(1,2,3))};val hash=CompanionPolicy.hash(src);t.begin(id,3,hash);denied {t.append(id,1,byteArrayOf(1))};assertEquals(2,t.append(id,0,byteArrayOf(1,2)));denied {t.append(id,0,byteArrayOf(1))};denied {t.append(id,2,byteArrayOf(3,4))};t.append(id,2,byteArrayOf(3));assertArrayEquals(src.readBytes(),t.finish(id).readBytes());dir.deleteRecursively()}
 @Test fun expiryAndCancellation(){val dir=Files.createTempDirectory("mark-test").toFile();var now=0L;val t=ApkTransfer(dir){now};t.begin("b".repeat(32),1,"a".repeat(64));now=180001;denied {t.append("b".repeat(32),0,byteArrayOf(1))};t.clear();assertEquals(0,dir.listFiles()!!.size);dir.delete()}
 @Test fun checksum(){val dir=Files.createTempDirectory("mark-test").toFile();val t=ApkTransfer(dir){0};val id="c".repeat(32);t.begin(id,1,"a".repeat(64));t.append(id,0,byteArrayOf(9));try{t.finish(id);fail()}catch(_:IllegalStateException){};assertEquals(0,dir.listFiles()!!.size);dir.delete()}
 @Test fun largeAndMalformedDenied(){val dir=Files.createTempDirectory("mark-test").toFile();val t=ApkTransfer(dir){0};denied {t.begin("z",1,"a".repeat(64))};denied {t.begin("a".repeat(32),CompanionPolicy.MAX_APK+1L,"a".repeat(64))};denied {t.begin("a".repeat(32),2,"sha")};assertEquals(0,dir.listFiles()!!.size);dir.delete()}
}

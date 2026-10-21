package br.com.bannaman.markwear.protocol
import org.junit.Test
import org.junit.Assert.*
import java.io.File
import java.nio.file.Files
import java.util.zip.*
class ApkArchiveTest {
 private fun archive(entries:List<Pair<String,Int>>):File {val file=Files.createTempFile("mark-zip",".apk").toFile();ZipOutputStream(file.outputStream()).use {out->entries.forEach {(name,size)->out.putNextEntry(ZipEntry(name));out.write(ByteArray(size));out.closeEntry()}};return file}
 private fun refused(f:File){try{boundedApkArchive(f);fail("Expected refusal")}catch(_:IllegalArgumentException){}finally{f.delete()}}
 @Test fun reasonableMetadata(){val f=archive(listOf("AndroidManifest.xml" to 10,"classes.dex" to 100));try{boundedApkArchive(f)}finally{f.delete()}}
 @Test fun compressedOversizedManifest(){refused(archive(listOf("AndroidManifest.xml" to (513*1024))))}
 @Test fun traversalAndMissingManifest(){refused(archive(listOf("../AndroidManifest.xml" to 10)));refused(archive(listOf("classes.dex" to 10)))}
}

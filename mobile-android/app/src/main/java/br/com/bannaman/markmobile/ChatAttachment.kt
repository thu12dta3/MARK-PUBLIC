package br.com.bannaman.markmobile

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Color
import android.graphics.Matrix
import android.graphics.pdf.PdfRenderer
import android.net.Uri
import android.provider.OpenableColumns
import android.util.Base64
import java.io.ByteArrayOutputStream

/** Loads a user-selected SAF document once, without broad storage permission. */
data class ChatAttachment(val name: String, val text: String? = null, val imageDataUrl: String? = null)

object AttachmentLoader {
    fun fromJpeg(bytes: ByteArray, rotationDegrees: Int): ChatAttachment {
        require(bytes.size in 1..12_000_000) { "Foto grande demais." }
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeByteArray(bytes, 0, bytes.size, bounds)
        require(bounds.outWidth > 0 && bounds.outHeight > 0) { "Foto inválida." }
        var sample = 1
        while (maxOf(bounds.outWidth, bounds.outHeight) / sample > 1600) sample *= 2
        val bitmap = BitmapFactory.decodeByteArray(bytes, 0, bytes.size,
            BitmapFactory.Options().apply { inSampleSize = sample }) ?: error("Não consegui abrir a foto.")
        val rotated = if (rotationDegrees % 360 == 0) bitmap else
            Bitmap.createBitmap(bitmap, 0, 0, bitmap.width, bitmap.height,
                Matrix().apply { postRotate(rotationDegrees.toFloat()) }, true)
        try {
            val jpeg = ByteArrayOutputStream().use { output ->
                require(rotated.compress(Bitmap.CompressFormat.JPEG, 85, output)) { "Não consegui compactar a foto." }
                output.toByteArray()
            }
            require(jpeg.size <= 6_000_000) { "Foto grande demais para anexar." }
            return ChatAttachment("foto-mark.jpg", imageDataUrl = dataUrl("image/jpeg", jpeg))
        } finally {
            if (rotated !== bitmap) bitmap.recycle()
            rotated.recycle()
        }
    }

    fun load(context: Context, uri: Uri): ChatAttachment {
        val resolver = context.contentResolver
        var name = "anexo"
        resolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use { cursor ->
            if (cursor.moveToFirst()) name = cursor.getString(0)?.take(160) ?: name
        }
        val mime = resolver.getType(uri).orEmpty()
        if (mime == "application/pdf") {
            // Bound input before entering the native PDF parser, including unknown SAF sizes.
            val limited = resolver.openInputStream(uri)?.use { BoundedInput.read(it, 6_000_000) }
                ?: error("Não consegui abrir o PDF.")
            val temporary = java.io.File.createTempFile("mark-pdf-", ".pdf", context.cacheDir)
            val descriptor = try {
                temporary.writeBytes(limited)
                android.os.ParcelFileDescriptor.open(temporary, android.os.ParcelFileDescriptor.MODE_READ_ONLY)
            } finally {
                temporary.delete()
            }
            descriptor.use { descriptor ->
                PdfRenderer(descriptor).use { renderer ->
                    require(renderer.pageCount > 0) { "PDF sem páginas." }
                    renderer.openPage(0).use { page ->
                        val scale = minOf(1200f / page.width, 1200f / page.height, 2f)
                        val bitmap = Bitmap.createBitmap((page.width * scale).toInt().coerceAtLeast(1),
                            (page.height * scale).toInt().coerceAtLeast(1), Bitmap.Config.ARGB_8888)
                        try {
                            bitmap.eraseColor(Color.WHITE)
                            page.render(bitmap, null, null, PdfRenderer.Page.RENDER_MODE_FOR_DISPLAY)
                            val bytes = ByteArrayOutputStream().use { stream ->
                                bitmap.compress(Bitmap.CompressFormat.PNG, 100, stream)
                                stream.toByteArray()
                            }
                            require(bytes.size <= 6_000_000) { "Primeira página do PDF excede 6 MB." }
                            return ChatAttachment(name, imageDataUrl = dataUrl("image/png", bytes))
                        } finally { bitmap.recycle() }
                    }
                }
            }
            error("Não consegui abrir o PDF.")
        }
        val bytes = resolver.openInputStream(uri)?.use { input ->
            val output = ByteArrayOutputStream()
            val chunk = ByteArray(8192)
            while (true) {
                val count = input.read(chunk)
                if (count < 0) break
                output.write(chunk, 0, count)
                require(output.size() <= 6_000_000) { "Arquivo excede 6 MB." }
            }
            output.toByteArray()
        } ?: error("Não consegui abrir o arquivo.")
        if (mime in setOf("image/jpeg", "image/png", "image/webp")) {
            return ChatAttachment(name, imageDataUrl = dataUrl(mime, bytes))
        }
        require(mime.startsWith("text/") || mime in setOf("application/json", "application/xml")) {
            "Este tipo de arquivo ainda não é aceito no chat."
        }
        val text = bytes.toString(Charsets.UTF_8)
        require(text.length <= 400_000) { "Texto do anexo é longo demais." }
        return ChatAttachment(name, text = text)
    }

    private fun dataUrl(mime: String, bytes: ByteArray): String =
        "data:$mime;base64," + Base64.encodeToString(bytes, Base64.NO_WRAP)
}

package br.com.bannaman.markmobile

import java.io.ByteArrayOutputStream
import java.io.InputStream

/** Counts actual bytes, including responses without Content-Length. */
internal object BoundedInput {
    fun read(input: InputStream, limit: Int): ByteArray {
        require(limit > 0)
        val output = ByteArrayOutputStream()
        val buffer = ByteArray(8192)
        var total = 0
        while (true) {
            val count = input.read(buffer, 0, minOf(buffer.size, limit - total + 1))
            if (count < 0) break
            total += count
            require(total <= limit) { "Conteúdo excede o limite permitido." }
            output.write(buffer, 0, count)
        }
        return output.toByteArray()
    }
}

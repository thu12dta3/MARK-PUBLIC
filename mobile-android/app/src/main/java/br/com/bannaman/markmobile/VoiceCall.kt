package br.com.bannaman.markmobile

import android.annotation.SuppressLint
import android.content.Context
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaPlayer
import android.media.MediaRecorder
import kotlinx.coroutines.delay
import java.io.ByteArrayOutputStream
import java.util.concurrent.atomic.AtomicBoolean

/** Captures one utterance in PCM 16 kHz; recording stops between turns and on hangup. */
object VoiceCall {
    @SuppressLint("MissingPermission") // Caller checks RECORD_AUDIO at runtime.
    fun listen(active: AtomicBoolean): ByteArray? {
        val format = AudioFormat.Builder().setSampleRate(16_000)
            .setEncoding(AudioFormat.ENCODING_PCM_16BIT).setChannelMask(AudioFormat.CHANNEL_IN_MONO).build()
        val chunk = ByteArray(3_200)
        val recorder = AudioRecord.Builder()
            .setAudioSource(MediaRecorder.AudioSource.VOICE_COMMUNICATION)
            .setAudioFormat(format).setBufferSizeInBytes(6_400).build()
        require(recorder.state == AudioRecord.STATE_INITIALIZED) { "Microfone indisponível." }
        val output = ByteArrayOutputStream()
        var heard = false
        var silenceChunks = 0
        var elapsedChunks = 0
        try {
            recorder.startRecording()
            while (active.get() && elapsedChunks < 250) {
                val read = recorder.read(chunk, 0, chunk.size)
                if (read <= 0) continue
                elapsedChunks++
                var energy = 0L
                var samples = 0
                for (index in 0 until read - 1 step 2) {
                    val value = ((chunk[index + 1].toInt() shl 8) or (chunk[index].toInt() and 0xff)).toShort().toInt()
                    energy += kotlin.math.abs(value).toLong()
                    samples++
                }
                val voice = samples > 0 && energy / samples > 480
                if (voice) heard = true
                if (heard) {
                    output.write(chunk, 0, read)
                    silenceChunks = if (voice) 0 else silenceChunks + 1
                    if (silenceChunks >= 12 && output.size() >= 5_760) break
                } else if (elapsedChunks >= 100) {
                    break
                }
            }
        } finally {
            runCatching { recorder.stop() }
            recorder.release()
        }
        return output.toByteArray().takeIf { active.get() && it.size >= 5_760 }
    }

    suspend fun play(context: Context, wav: ByteArray, active: AtomicBoolean) {
        require(wav.size in 44..8_000_000) { "Áudio de resposta inválido." }
        val file = java.io.File.createTempFile("mark-voice-", ".wav", context.cacheDir)
        val player = MediaPlayer()
        try {
            file.writeBytes(wav)
            player.setAudioAttributes(AudioAttributes.Builder()
                .setUsage(AudioAttributes.USAGE_VOICE_COMMUNICATION)
                .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build())
            player.setDataSource(file.absolutePath)
            player.prepare()
            player.start()
            while (active.get() && player.isPlaying) delay(80)
        } finally {
            runCatching { player.stop() }
            player.release()
            file.delete()
        }
    }
}

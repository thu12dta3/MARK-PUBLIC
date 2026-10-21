package br.com.bannaman.markwear

object HealthReading {
    fun state(bpm: Float?, accuracy: Int, ageMs: Long): String = when {
        bpm==null || !bpm.isFinite() || bpm !in 25f..240f || ageMs<0 -> "INDISPONÍVEL"
        accuracy<=0 -> "QUALIDADE INCERTA"
        ageMs<=30000 -> "ATUAL"
        ageMs<=300000 -> "RECENTE"
        else -> "ANTIGA"
    }
}

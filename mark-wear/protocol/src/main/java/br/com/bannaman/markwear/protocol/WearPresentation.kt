package br.com.bannaman.markwear.protocol

/** Pure UI decisions: unavailable actions expose their prerequisite instead of silently failing. */
object WearPresentation {
    private val entryPages=setOf("HOME","VOICE","WEATHER","LAUNCHER_SETUP")
    fun entryPage(requested:String?)=requested?.takeIf {it in entryPages}?:"HOME"
    fun status(raw: String): String = when(raw) {
        "Bluetooth · autorize · Configurações", "PHONE OFFLINE" -> "Celular desconectado"
        "BUSY" -> "Celular ocupado · tente novamente"
        "PC_UNAVAILABLE_OR_REQUEST_DENIED" -> "PC indisponível ou pedido recusado · confira a ponte"
        "UNAVAILABLE" -> "Indisponível"
        "ONLINE", "M23 CONECTADO" -> "Celular conectado"
        "PROCESSING" -> "MARK preparando resposta…"
        "ANSWER READY" -> "Resposta recebida"
        "LISTENING" -> "Ouvindo sua pergunta…"
        else -> raw.take(240)
    }
    fun voiceAction(linked: Boolean, busy: Boolean, talking: Boolean): String = when {
        talking -> "Canal ativo"
        busy -> "Aguarde a resposta"
        !linked -> "Conectar celular"
        else -> "Iniciar conversa"
    }
    fun canStartVoice(linked: Boolean, busy: Boolean, talking: Boolean) = linked && !busy && !talking
    fun navigationTrail(trail: List<String>, current: String, next: String): List<String> =
        if(current == next) trail else if(next == "HOME") emptyList() else (trail + current).takeLast(12)
}

package br.com.bannaman.markcompanion

/** UI continuation only: never grants a Bluetooth session or biometric authorization. */
internal class PairingUiState {
    private var requested = false
    private var resumeChoice = false
    private var cancelled = false
    fun requestPermission() { requested = true; resumeChoice = false }
    fun permissionResult(granted:Boolean, visible:Boolean):Boolean {
        if (!requested) return false
        requested = false
        resumeChoice = granted && !visible
        return granted && visible
    }
    fun consumeResumeChoice():Boolean = resumeChoice.also { resumeChoice = false }
    fun pendingCancelled(hadPendingLink:Boolean) { if (hadPendingLink) cancelled = true }
    fun consumeCancellation():Boolean = cancelled.also { cancelled = false }
}

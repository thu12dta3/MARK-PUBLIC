package br.com.bannaman.markmobile

/** Only non-secret connection metadata is stored here; the signing key stays in Keystore. */
class SecureSettings(context: android.content.Context) {
    private val prefs = context.getSharedPreferences("mark.mobile", android.content.Context.MODE_PRIVATE)
    init {
        // Remove credentials left by the older shared-token prototype.
        if (prefs.contains("api_key_encrypted")) prefs.edit().remove("api_key_encrypted").apply()
        runCatching {
            java.security.KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
                .deleteEntry("mark-mobile-settings-v1")
        }
    }

    fun serverUrl(): String = prefs.getString("server_url", "") ?: ""
    fun serverPin(): String = prefs.getString("server_pin", "") ?: ""
    fun saveServerUrl(value: String) {
        prefs.edit().putString("server_url", value.trim().trimEnd('/')).apply()
    }
    fun saveServerPin(value: String) {
        require(Regex("[a-f0-9]{64}").matches(value))
        prefs.edit().putString("server_pin", value).apply()
    }
    fun paired(): Boolean = prefs.getBoolean("paired", false)
    fun secureScreen(): Boolean = prefs.getBoolean("secure_screen", true)
    fun setSecureScreen(value: Boolean) {
        prefs.edit().putBoolean("secure_screen", value).apply()
    }
    fun setPaired(value: Boolean) {
        prefs.edit().putBoolean("paired", value).apply()
    }
}

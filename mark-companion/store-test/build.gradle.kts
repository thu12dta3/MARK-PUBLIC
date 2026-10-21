plugins { alias(libs.plugins.android.application) }
android {
    namespace = "br.com.bannaman.markstoretest"
    compileSdk = 37
    defaultConfig {
        applicationId = "br.com.bannaman.markstoretest"
        minSdk = 25
        targetSdk = 35
        versionCode = providers.gradleProperty("storeFixtureVersion").orElse("1").get().toInt()
        versionName = "0.1.0-test-${versionCode}"
    }
    val store = System.getenv("MARK_ANDROID_KEYSTORE")
    val passwordFile = System.getenv("MARK_ANDROID_KEY_PASSWORD_FILE")
    if (store != null && passwordFile != null) {
        signingConfigs { create("owner") {
            storeFile = file(store)
            val password = file(passwordFile).readText().trim()
            storePassword = password; keyPassword = password; keyAlias = "mark-mobile"
        } }
        buildTypes { getByName("release") {signingConfig = signingConfigs.getByName("owner")} }
    }
}

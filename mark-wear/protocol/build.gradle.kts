plugins { id("com.android.library") }
android {
    namespace = "br.com.bannaman.markwear.protocol"
    compileSdk = 37
    defaultConfig { minSdk = 25 }
}
dependencies { testImplementation("junit:junit:4.13.2"); testImplementation("org.json:json:20250517") }

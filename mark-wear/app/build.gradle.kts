plugins { alias(libs.plugins.android.application) }
android {
    namespace = "br.com.bannaman.markwear"
    compileSdk = 37
    defaultConfig {
        applicationId = "br.com.bannaman.markmobile"
        minSdk = 25
        targetSdk = 35
        versionCode = 21
        versionName = "0.4.4-sensors-rc"
        testInstrumentationRunner = "br.com.bannaman.markwear.WearSmokeInstrumentation"
    }
    testBuildType = "release"
    val store = System.getenv("MARK_ANDROID_KEYSTORE")
    val passwordFile = System.getenv("MARK_ANDROID_KEY_PASSWORD_FILE")
    if (store != null && passwordFile != null) {
        signingConfigs { create("owner") {
            storeFile = file(store)
            val password = file(passwordFile).readText().trim()
            storePassword = password; keyPassword = password; keyAlias = "mark-mobile"
        } }
        buildTypes { getByName("release") {
            signingConfig = signingConfigs.getByName("owner")
        } }
    }
}
dependencies {
    implementation(project(":wear-protocol"))
    implementation("com.google.android.support:wearable:2.9.0")
    compileOnly("com.google.android.wearable:wearable:2.9.0")
    implementation("com.google.android.gms:play-services-wearable:20.0.1")
    testImplementation("junit:junit:4.13.2")
}

// The store fixture exists only in the opt-in instrumentation APK.
val storeTestAssets=layout.buildDirectory.dir("generated/store-test-assets")
val fixtureTask=tasks.register<Copy>("prepareStoreTestAssets") {
    dependsOn(":store-test:assembleRelease")
    from(project(":store-test").layout.buildDirectory.file("outputs/apk/release/store-test-release.apk"))
    into(storeTestAssets)
    rename { "mark-store-test.apk" }
}
android.sourceSets.getByName("androidTest").assets.srcDir(storeTestAssets.get().asFile)
tasks.matching { it.name == "preReleaseAndroidTestBuild" }.configureEach {dependsOn(fixtureTask)}

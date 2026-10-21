# Android Gradle Plugin preserves AndroidManifest components and serialization
# classes referenced directly by the application. Keep the QR capture activity
# explicit for stable ActivityResult intent resolution.
-keep class com.journeyapps.barcodescanner.CaptureActivity { *; }

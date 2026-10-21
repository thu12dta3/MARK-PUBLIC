package br.com.bannaman.markmobile

import android.app.ActivityManager
import android.content.Context
import android.content.pm.PackageManager
import android.hardware.Sensor
import android.hardware.SensorManager
import android.hardware.biometrics.BiometricManager
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CameraManager
import android.media.MediaCodecList
import android.os.BatteryManager
import android.os.Build
import android.os.PowerManager
import com.google.ar.core.ArCoreApk

/** Local-only runtime capability report. No phone identifiers are collected or transmitted. */
object DeviceProfile {
    fun describe(context: Context): String {
        val lines = mutableListOf("Android ${Build.VERSION.RELEASE} (API ${Build.VERSION.SDK_INT})")
        val display = context.display
        lines += "Tela: ${display.refreshRate.toInt()} Hz atuais"
        val sensors = context.getSystemService(SensorManager::class.java).getSensorList(Sensor.TYPE_ALL)
        lines += "Sensores disponíveis: ${sensors.size}"
        lines += "Bússola: ${if (sensors.any { it.type == Sensor.TYPE_MAGNETIC_FIELD }) "sim" else "não"}"
        lines += "Proximidade: ${if (sensors.any { it.type == Sensor.TYPE_PROXIMITY }) "sim" else "não"}"
        val cameraManager = context.getSystemService(CameraManager::class.java)
        runCatching {
            val ids = cameraManager.cameraIdList
            val external = ids.count {
                cameraManager.getCameraCharacteristics(it).get(CameraCharacteristics.LENS_FACING) ==
                    CameraCharacteristics.LENS_FACING_EXTERNAL
            }
            lines += "Câmeras: ${ids.size}; externas: $external"
        }.onFailure { lines += "Câmeras: indisponíveis para consulta" }
        lines += "USB Host: ${if (context.packageManager.hasSystemFeature(PackageManager.FEATURE_USB_HOST)) "sim" else "não"}"
        val biometrics = context.getSystemService(BiometricManager::class.java)
        lines += "Biometria forte: ${if (biometrics?.canAuthenticate(BiometricManager.Authenticators.BIOMETRIC_STRONG) == BiometricManager.BIOMETRIC_SUCCESS) "pronta" else "não configurada ou indisponível"}"
        val codecs = MediaCodecList(MediaCodecList.REGULAR_CODECS).codecInfos
        lines += "Decodificação H.264: ${if (codecs.any { !it.isEncoder && "video/avc" in it.supportedTypes }) "sim" else "não"}"
        val memory = ActivityManager.MemoryInfo()
        context.getSystemService(ActivityManager::class.java).getMemoryInfo(memory)
        lines += "RAM detectada: ${memory.totalMem / (1024 * 1024)} MiB"
        val battery = context.getSystemService(BatteryManager::class.java)
        lines += "Bateria: ${battery.getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY)}%"
        lines += "Economia de energia: ${if (context.getSystemService(PowerManager::class.java).isPowerSaveMode) "ativa" else "desligada"}"
        val ar = ArCoreApk.getInstance().checkAvailability(context)
        lines += "ARCore: ${if (ar.isSupported) "compatível" else if (ar.isUnknown) "verificando" else "indisponível"}"
        return lines.joinToString("\n")
    }
}

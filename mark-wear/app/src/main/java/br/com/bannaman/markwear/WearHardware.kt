package br.com.bannaman.markwear

import android.app.ActivityManager
import android.content.Context
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.StatFs
import android.speech.SpeechRecognizer
import org.json.JSONArray
import org.json.JSONObject

object WearHardware {
    fun diagnose(context: Context): JSONObject {
        val memory=ActivityManager.MemoryInfo()
        context.getSystemService(ActivityManager::class.java).getMemoryInfo(memory)
        val display=context.resources.displayMetrics
        val sensors=context.getSystemService(SensorManager::class.java).getSensorList(Sensor.TYPE_ALL)
        val gms=runCatching { context.packageManager.getPackageInfo("com.google.android.gms",0).versionName }.getOrNull()
        return JSONObject().put("api",Build.VERSION.SDK_INT).put("android",Build.VERSION.RELEASE)
            .put("abis",JSONArray(Build.SUPPORTED_ABIS.toList())).put("ram_available",memory.availMem)
            .put("storage_available",StatFs(context.filesDir.path).availableBytes)
            .put("width",display.widthPixels).put("height",display.heightPixels).put("density",display.density)
            .put("round",context.resources.configuration.isScreenRound)
            .put("gms",gms ?: "UNAVAILABLE").put("recognizer",SpeechRecognizer.isRecognitionAvailable(context))
            .put("microphone",context.packageManager.hasSystemFeature("android.hardware.microphone"))
            .put("audio_output",context.packageManager.hasSystemFeature("android.hardware.audio.output"))
            .put("sensor_count",sensors.size).put("watchface","PHYSICAL TEST REQUIRED")
            .put("tiles","PHYSICAL TEST REQUIRED").put("idle_power","PHYSICAL TEST REQUIRED")
    }
}

/** Single on-demand sensor; always unregister on completion, timeout or UI stop. */
class SensorSession(private val context: Context) : SensorEventListener {
    private val settings=context.getSharedPreferences("mark.wear",Context.MODE_PRIVATE)
    private val manager=context.getSystemService(SensorManager::class.java)
    private val handler=Handler(Looper.getMainLooper())
    private var callback: ((FloatArray,Int,Long)->Unit)?=null
    private var failure: ((String)->Unit)?=null
    private var started=0L
    private var triggerSensor:Sensor?=null
    private val trigger=object:android.hardware.TriggerEventListener(){
        override fun onTrigger(event:android.hardware.TriggerEvent){deliver(event.sensor,event.values,0,event.timestamp)}
    }
    var activeMillis=0L
        private set
    private val timeout=Runnable {val error=failure;stop();error?.invoke("Sem leitura válida no prazo. Ajuste o relógio e tente novamente.") }
    fun sensors(): List<Sensor> = manager.getSensorList(Sensor.TYPE_ALL)
    fun read(sensor: Sensor, millis: Long, onValue: (FloatArray,Int,Long)->Unit, onError: (String)->Unit) {
        stop()
        if(sensor.type==Sensor.TYPE_HEART_RATE && !settings.getBoolean("health",false)){onError("Saúde pausada; habilite na tela Saúde");return}
        callback=onValue; failure=onError;started=android.os.SystemClock.elapsedRealtime()
        try {
            val accepted=if(sensor.reportingMode==Sensor.REPORTING_MODE_ONE_SHOT){triggerSensor=sensor;manager.requestTriggerSensor(trigger,sensor)}else manager.registerListener(this,sensor,SensorManager.SENSOR_DELAY_NORMAL)
            if (!accepted) {
                onError("Sensor indisponível");stop();return
            }
            handler.postDelayed(timeout,millis.coerceIn(1000,if(sensor.type==Sensor.TYPE_HEART_RATE)45000 else 15000))
        } catch (_: SecurityException) { onError("Permissão do sensor não concedida");stop() }
    }
    override fun onSensorChanged(event: SensorEvent) {
        deliver(event.sensor,event.values,event.accuracy,event.timestamp)
    }
    private fun deliver(sensor:Sensor,raw:FloatArray,accuracy:Int,nanos:Long){
        if(raw.isEmpty() || raw.any {!it.isFinite()})return
        if(sensor.type==Sensor.TYPE_HEART_RATE && raw[0] !in 25f..240f)return
        val cb=callback;val values=SensorOffsets.apply(context,sensor,raw)
        val now=android.os.SystemClock.elapsedRealtime()
        val captured=nanos/1_000_000
        val validTime=captured>0 && captured<=now+1000
        stop();cb?.invoke(values,if(validTime) accuracy else 0,if(validTime) captured.coerceAtMost(now) else now)
    }
    override fun onAccuracyChanged(sensor: Sensor?,accuracy: Int) {}
    fun stop() {if(started>0){activeMillis+=(android.os.SystemClock.elapsedRealtime()-started).coerceAtLeast(0);started=0};manager.unregisterListener(this);triggerSensor?.let {manager.cancelTriggerSensor(trigger,it)};triggerSensor=null;handler.removeCallbacks(timeout);callback=null;failure=null }
}

package br.com.bannaman.markwear

import android.content.Context
import android.os.Process
import android.os.SystemClock
import org.json.JSONObject

/** Bounded technical snapshot, opt-in; never contains speech, health or peer identifiers. */
class WearMetrics(context:Context) {
    private val prefs=context.getSharedPreferences("mark.wear",Context.MODE_PRIVATE)
    private var start=0L
    private var cpu=0L
    private var interactions=0L
    private var voices=0L
    private var sent=0L
    private var received=0L
    private var errors=0L
    private var connects=0L
    @Synchronized fun start(){start=SystemClock.elapsedRealtime();cpu=Process.getElapsedCpuTime()}
    @Synchronized fun interaction(){interactions++}
    @Synchronized fun voice(){voices++}
    @Synchronized fun sent(bytes:Int){sent+=bytes.coerceAtLeast(0)}
    @Synchronized fun received(bytes:Int){received+=bytes.coerceAtLeast(0)}
    @Synchronized fun error(){errors++}
    @Synchronized fun connected(){connects++}
    @Synchronized fun save(sensorMs:Long){if(!prefs.getBoolean("telemetry",false))return
        val value=JSONObject().put("active_ms",(SystemClock.elapsedRealtime()-start).coerceAtLeast(0))
            .put("process_cpu_ms",(Process.getElapsedCpuTime()-cpu).coerceAtLeast(0))
            .put("interactions",interactions).put("voice_events",voices).put("protocol_tx_bytes",sent)
            .put("protocol_rx_bytes",received).put("request_errors",errors).put("connections",connects).put("sensor_active_ms",sensorMs)
        prefs.edit().putString("local_metrics",value.toString()).apply()
    }
    @Synchronized fun summary()=prefs.getString("local_metrics","Sem sessão técnica registrada.")!!
}

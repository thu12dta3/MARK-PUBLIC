package br.com.bannaman.markwear

import android.content.Context
import android.hardware.Sensor
import org.json.JSONObject

/** App-local offset for the exact sensor identity. No HAL/OEM or health changes. */
internal object SensorOffsets {
    private fun key(sensor:Sensor)="${sensor.type}:${sensor.vendor}:${sensor.name}:${sensor.version}"
    fun store(context:Context,sensor:Sensor,offset:FloatArray){
        require(sensor.type in setOf(4,10) && offset.size==3 && offset.all {it.isFinite()})
        val limit=if(sensor.type==4)0.05 else 0.4
        require(kotlin.math.sqrt(offset.sumOf {it.toDouble()*it})<=limit)
        val value=JSONObject().put("time",System.currentTimeMillis())
        for(i in 0..2)value.put("v$i",offset[i].toDouble())
        context.getSharedPreferences("sensor-offsets",0).edit().putString(key(sensor),value.toString()).apply()
    }
    fun clear(context:Context){context.getSharedPreferences("sensor-offsets",0).edit().clear().apply()}
    fun apply(context:Context,sensor:Sensor,values:FloatArray):FloatArray {
        val copy=values.copyOf();if(sensor.type !in setOf(4,10) || copy.size<3)return copy
        val text=context.getSharedPreferences("sensor-offsets",0).getString(key(sensor),null)?:return copy
        runCatching {
            val json=JSONObject(text);val age=System.currentTimeMillis()-json.getLong("time")
            require(age in 0..86_400_000L)
            val bias=FloatArray(3){json.getDouble("v$it").toFloat()}
            val limit=if(sensor.type==4)0.05 else 0.4
            require(bias.all {it.isFinite()} && kotlin.math.sqrt(bias.sumOf {it.toDouble()*it})<=limit)
            for(i in 0..2)copy[i]-=bias[i]
        }
        return copy
    }
}

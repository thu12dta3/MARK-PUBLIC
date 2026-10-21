package br.com.bannaman.markwear

import android.app.Activity
import android.content.Intent
import android.hardware.*
import android.os.*
import android.view.WindowManager
import android.widget.TextView

/** Explicit foreground-only collection. Stopping the page cancels every listener. */
class SensorCalibrationActivity:Activity(),SensorEventListener {
    private lateinit var manager:SensorManager
    private lateinit var text:TextView
    private val handler=Handler(Looper.getMainLooper())
    private val stats=mutableMapOf<Int,RestCalibration>()
    private val selected=mutableMapOf<Int,Sensor>()
    private var running=false
    private var pending=emptyMap<Int,FloatArray>()
    private var simple:SensorSession?=null
    private fun cancel(){running=false;manager.unregisterListener(this);handler.removeCallbacksAndMessages(null);window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);simple?.stop();pending=emptyMap()}
    override fun onCreate(state:Bundle?){super.onCreate(state);window.addFlags(WindowManager.LayoutParams.FLAG_SECURE);manager=getSystemService(SensorManager::class.java);simple=SensorSession(this);home()}
    override fun onStop(){cancel();super.onStop()}
    override fun onStart(){super.onStart();if(::text.isInitialized)home()}
    private fun home(){
        cancel();val box=WearUi.page(this,"Sensores · ajuste local")
        text=WearUi.label(this,box,"Coloque o relógio imóvel em apoio estável, sem vibração. O ajuste só afeta o laboratório MARK por até24h; não altera firmware, pulso, passos ou Maps.",12)
        WearUi.button(this,box,"Medir repouso · 6 segundos","status"){measure()}
        WearUi.button(this,box,"Apagar ajustes locais","stop"){SensorOffsets.clear(this);text.text="Ajustes apagados; leituras originais preservadas."}
        WearUi.button(this,box,"Verificar todos os sensores","health"){
            cancel();val inventory=WearUi.page(this,"Verificação de sensores");text=WearUi.label(this,inventory,"Toque para uma leitura de até10s. Precisão é o estado informado pelo Android; não é prova de calibração.",12)
            for(sensor in manager.getSensorList(Sensor.TYPE_ALL))WearUi.button(this,inventory,sensor.name){
                simple?.stop()
                val advice=when(sensor.type){
                    2,14,3,11,20->"Bússola/orientação: afaste ímãs e carregador; mova lentamente nos três eixos. O sistema gerencia a calibração magnética."
                    21->"Pulso: ajuste a pulseira e use Saúde → Medir agora com permissão. Sem correção numérica ou calibração clínica."
                    18,19->"Passos: compare uma caminhada com contagem manual. Não zerar o contador do sistema nem criar passos artificiais."
                    5->"Luz: compare ambientes claro/escuro; ajuste absoluto exige referência de iluminância."
                    else->"Sensor do sistema: leitura original. Ajuste absoluto exige referência adequada."
                }
                text.text="${sensor.name}\n$advice"
                if(sensor.type==21)return@button
                if(sensor.reportingMode==Sensor.REPORTING_MODE_ONE_SHOT)text.append("\nEvento único: mova o relógio para tentar gerar um evento.")
                simple?.read(sensor,10000,{v,a,_->text.append("\nLeitura: ${v.joinToString {"%.3f".format(it)}}\nPrecisão Android: $a")},{e->text.append("\n$e")})
            }
            WearUi.label(this,inventory,"GPS: depende de céu aberto e fixação de satélites; não possui offset calibrável por este app. Verifique a localização no Maps. Sensores proprietários de atividade podem exigir serviços Mobvoi.",12)
            WearUi.button(this,inventory,"Voltar"){home()}
        }
        WearUi.button(this,box,"Voltar"){finish()}
    }
    private fun measure(){
        cancel();stats.clear();selected.clear()
        for(kind in listOf(1,4,10))manager.getDefaultSensor(kind)?.let {selected[kind]=it;stats[kind]=RestCalibration()}
        if(1 !in selected){text.text="Acelerômetro indisponível; não é possível validar repouso.";return}
        running=true;window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);text.text="Medindo por6s… mantenha imóvel. Nenhum ajuste será salvo sem sua confirmação."
        try{for(sensor in selected.values)check(manager.registerListener(this,sensor,20000)){"Sensor não aceitou leitura"}}
        catch(_:Exception){cancel();text.text="Leitura indisponível ou permissão não concedida.";return}
        handler.postDelayed({finishMeasurement()},6000)
    }
    override fun onSensorChanged(event:SensorEvent){if(running)stats[event.sensor.type]?.add(event.values,event.timestamp,event.accuracy)}
    override fun onAccuracyChanged(sensor:Sensor?,accuracy:Int){}
    private fun finishMeasurement(){
        running=false;manager.unregisterListener(this);window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        if(stats[1]?.stationaryAcceleration()!=true){text.text="Repouso não confirmado: houve movimento, amostras insuficientes ou precisão baixa. Nenhum ajuste salvo.";return}
        pending=stats.mapNotNull {(kind,stat)->stat.offset(kind)?.let {kind to it}}.toMap()
        val box=WearUi.page(this,"Revise o ajuste")
        text=WearUi.label(this,box,if(pending.isEmpty())"Nenhum offset válido; mantenha imóvel e tente novamente." else pending.entries.joinToString("\n"){(kind,bias)->"${selected[kind]?.name}: ${bias.joinToString {"%.5f".format(it)}}"},12)
        WearUi.label(this,box,"Só confirme se o relógio permaneceu imóvel. Correção local por24h; reavalie após mudança de temperatura. Acelerômetro foi usado para validar repouso, não zerado.",12)
        if(pending.isNotEmpty())WearUi.button(this,box,"Confirmar ajuste local"){for((kind,bias)in pending)SensorOffsets.store(this,selected.getValue(kind),bias);home();text.text="Ajuste aplicado às leituras do laboratório MARK. Firmware e demais apps preservados."}
        WearUi.button(this,box,"Descartar / voltar"){home()}
    }
}

package br.com.bannaman.markwear

import kotlin.math.sqrt

/** Bounded statistics only; never retain a stream of sensor samples. */
internal class RestCalibration {
    private var count=0
    private var first=0L
    private var last=0L
    private val mean=DoubleArray(3)
    private val m2=DoubleArray(3)
    fun add(values:FloatArray,time:Long,accuracy:Int):Boolean {
        if(count>=1000 || values.size<3 || values.take(3).any {!it.isFinite()} || accuracy<2 || time<=0 || time<=last)return false
        if(count==0)first=time
        last=time;count++
        for(i in 0..2){val delta=values[i]-mean[i];mean[i]+=delta/count;m2[i]+=delta*(values[i]-mean[i])}
        return true
    }
    fun average()=FloatArray(3){mean[it].toFloat()}
    fun valid(minimum:Int=40)=count>=minimum && last-first>=3_000_000_000L
    fun noise()=if(count<2)Double.POSITIVE_INFINITY else sqrt(m2.sum()/(count-1))
    fun magnitude()=sqrt(mean.sumOf {it*it})
    fun stationaryAcceleration()=valid() && noise()<=0.15 && magnitude() in 9.0..10.6
    fun offset(kind:Int):FloatArray? {
        if(!valid())return null
        return when(kind){
            4->average().takeIf {noise()<=0.012 && magnitude()<=0.05}
            10->average().takeIf {noise()<=0.15 && magnitude()<=0.4}
            else->null
        }
    }
}

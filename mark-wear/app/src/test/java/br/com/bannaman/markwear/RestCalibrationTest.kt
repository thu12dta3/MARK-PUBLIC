package br.com.bannaman.markwear
import org.junit.Assert.*
import org.junit.Test
class RestCalibrationTest {
    private fun samples(values:FloatArray)=RestCalibration().also {s->repeat(220){s.add(values,1L+it*20_000_000L,3)}}
    @Test fun stationaryGravityIsNotZeroed(){val s=samples(floatArrayOf(0f,0f,9.81f));assertTrue(s.stationaryAcceleration());assertNull(s.offset(1))}
    @Test fun acceptsSmallGyroOffset(){assertArrayEquals(floatArrayOf(.002f,-.001f,.003f),samples(floatArrayOf(.002f,-.001f,.003f)).offset(4),.000001f)}
    @Test fun rejectsRotation(){assertNull(samples(floatArrayOf(.2f,0f,0f)).offset(4))}
    @Test fun acceptsSmallLinearOffset(){assertNotNull(samples(floatArrayOf(.03f,0f,-.02f)).offset(10))}
    @Test fun rejectsLargeLinearOffset(){assertNull(samples(floatArrayOf(.5f,0f,0f)).offset(10))}
    @Test fun rejectsLowAccuracyAndBadValues(){val s=RestCalibration();assertFalse(s.add(floatArrayOf(0f,0f,0f),1,1));assertFalse(s.add(floatArrayOf(Float.NaN,0f,0f),2,3));assertFalse(s.add(floatArrayOf(0f),3,3));assertNull(s.offset(4))}
    @Test fun requiresDurationAndMonotonicTime(){val s=RestCalibration();repeat(100){s.add(floatArrayOf(0f,0f,0f),it+1L,3)};assertNull(s.offset(4));assertFalse(s.add(floatArrayOf(0f,0f,0f),1,3))}
    @Test fun rejectsMotionNoise(){val s=RestCalibration();repeat(220){s.add(floatArrayOf(if(it%2==0)1f else -1f,0f,9.81f),it*20_000_000L+1,3)};assertFalse(s.stationaryAcceleration())}
    @Test fun doesNotCalibrateHealthOrCompass(){val s=samples(floatArrayOf(0f,0f,0f));for(kind in listOf(2,5,14,18,19,21))assertNull(s.offset(kind))}
    @Test fun boundsTheSampleCount(){val s=RestCalibration();repeat(1000){assertTrue(s.add(floatArrayOf(0f,0f,0f),it*20_000_000L+1,3))};assertFalse(s.add(floatArrayOf(0f,0f,0f),21_000_000_000L,3))}
}

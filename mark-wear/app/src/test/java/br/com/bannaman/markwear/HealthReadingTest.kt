package br.com.bannaman.markwear
import org.junit.Assert.*
import org.junit.Test
class HealthReadingTest {
    @Test fun currentRecentOldAndUncertain() {
        assertEquals("ATUAL",HealthReading.state(70f,2,5000))
        assertEquals("RECENTE",HealthReading.state(70f,2,60000))
        assertEquals("ANTIGA",HealthReading.state(70f,2,400000))
        assertEquals("QUALIDADE INCERTA",HealthReading.state(70f,0,1000))
    }
    @Test fun missingAndInvalid() {
        assertEquals("INDISPONÍVEL",HealthReading.state(null,2,1000))
        assertEquals("INDISPONÍVEL",HealthReading.state(Float.NaN,2,1000))
        assertEquals("INDISPONÍVEL",HealthReading.state(0f,2,1000))
        assertEquals("INDISPONÍVEL",HealthReading.state(70f,2,-1))
    }
}

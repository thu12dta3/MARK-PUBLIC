package br.com.bannaman.markwear.protocol
import org.junit.Test
import org.junit.Assert.*

class WearCalculatorTest {
    @Test fun precedence() { assertEquals("14", WearCalculator.calculate("2+3×4")); assertEquals("20", WearCalculator.calculate("(2+3)*4")) }
    @Test fun decimalAndUnary() { assertEquals("0,3", WearCalculator.calculate("0,1+0,2")); assertEquals("-4", WearCalculator.calculate("-2×2")); assertEquals("2,5", WearCalculator.calculate("5÷2")); assertEquals("0,5", WearCalculator.calculate(".5")) }
    @Test fun failures() { for (input in listOf("", "1/0", "(2+3", "2x3", "1..2", "1+", "2);exec()", "9".repeat(35), "(".repeat(129))) assertTrue(input, runCatching { WearCalculator.calculate(input) }.isFailure) }
    @Test fun bounds() { assertTrue(runCatching {WearCalculator.calculate("9999999999999999999999999999999999*9999999999999999999999999999999999")}.isFailure); assertTrue(runCatching {WearCalculator.calculate("(".repeat(128))}.isFailure) }
}

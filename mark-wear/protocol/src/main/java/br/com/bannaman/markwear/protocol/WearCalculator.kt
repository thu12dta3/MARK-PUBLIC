package br.com.bannaman.markwear.protocol

import java.math.BigDecimal
import java.math.MathContext

/** Bounded arithmetic only: no scripts, reflection, files or network. */
object WearCalculator {
    fun calculate(expression: String): String {
        require(expression.length in 1..128) { "Expressão muito longa ou vazia" }
        val p = Parser(expression.replace(',', '.').replace('×', '*').replace('÷', '/').replace('−', '-'))
        val result = p.expression().stripTrailingZeros()
        p.space()
        require(p.position == p.text.length) { "Confira a expressão" }
        require(result.precision() + maxOf(0, -result.scale()) <= 40 && result.scale() in -40..40) { "Resultado fora do limite" }
        return result.toPlainString().replace('.', ',')
    }
    private class Parser(val text: String) {
        var position = 0
        private val math = MathContext.DECIMAL128
        fun space() { while (position < text.length && text[position].isWhitespace()) position++ }
        private fun take(c: Char): Boolean { space(); if (position < text.length && text[position] == c) { position++; return true }; return false }
        fun expression(): BigDecimal {
            var v = term()
            while (true) v = when { take('+') -> v.add(term(), math); take('-') -> v.subtract(term(), math); else -> return v }
        }
        private fun term(): BigDecimal {
            var v = atom()
            while (true) v = when { take('*') -> v.multiply(atom(), math); take('/') -> { val d = atom(); require(d.signum() != 0) { "Não é possível dividir por zero" }; v.divide(d, math) }; else -> return v }
        }
        private fun atom(): BigDecimal {
            if (take('+')) return atom()
            if (take('-')) return atom().negate(math)
            if (take('(')) { val v = expression(); require(take(')')) { "Feche os parênteses" }; return v }
            space(); val start = position
            while (position < text.length && (text[position] in '0'..'9' || text[position] == '.')) position++
            require(position > start && position-start <= 34) { "Confira o número" }
            return text.substring(start, position).toBigDecimalOrNull()?.round(math) ?: throw IllegalArgumentException("Confira o número")
        }
    }
}

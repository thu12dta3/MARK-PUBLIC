package br.com.bannaman.markwear

import android.app.Activity
import android.graphics.Color
import android.os.Bundle
import android.view.Gravity
import android.widget.*
import br.com.bannaman.markwear.protocol.WearCalculator

/** Independent local utility; never creates the MARK phone link or microphone. */
class CalculatorActivity : Activity() {
    private var expression = ""
    private var calculated = false
    private lateinit var display: TextView
    override fun onCreate(saved: Bundle?) {
        super.onCreate(saved)
        expression = saved?.getString("expression")?.takeIf { it.length <= 128 } ?: ""
        calculated = saved?.getBoolean("calculated") ?: false
        val box = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL; setBackgroundColor(WearUi.INK)
            val inset = if(resources.configuration.isScreenRound) 30 else 12
            setPadding(WearUi.dp(this@CalculatorActivity, inset), WearUi.dp(this@CalculatorActivity, 18), WearUi.dp(this@CalculatorActivity, inset), WearUi.dp(this@CalculatorActivity, 26))
        }
        setContentView(box)
        WearUi.label(this, box, "Calculadora", 14, Color.WHITE).setPadding(0, 0, 0, 0)
        display = WearUi.label(this, box, expression.ifBlank { "0" }, 22, WearUi.TEAL).apply {
            gravity = Gravity.END or Gravity.CENTER_VERTICAL; setPadding(0, 0, 0, 0)
            setTextIsSelectable(true); maxLines = 1; ellipsize = android.text.TextUtils.TruncateAt.START
            layoutParams = LinearLayout.LayoutParams(-1, WearUi.dp(this@CalculatorActivity, 32))
        }
        val rows = listOf(listOf("C", "⌫", "(", ")"), listOf("7", "8", "9", "÷"), listOf("4", "5", "6", "×"), listOf("1", "2", "3", "−"), listOf("0", ",", "=", "+"))
        rows.forEach { keys ->
            val row = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; isBaselineAligned = false }
            box.addView(row, LinearLayout.LayoutParams(-1, 0, 1f))
            keys.forEach { key ->
                WearUi.button(this, row, key) { press(key) }.apply {
                    textSize = 18f; minHeight = 0; minimumHeight = 0; minimumWidth = 0
                    setPadding(0, 0, 0, 0); contentDescription = when(key) { "C" -> "Limpar"; "⌫" -> "Apagar último caractere"; "=" -> "Calcular"; else -> key }
                    layoutParams = LinearLayout.LayoutParams(0, -1, 1f).apply { setMargins(2, 3, 2, 0) }
                    if(key == "=") setTextColor(WearUi.TEAL)
                }
            }
        }
    }
    override fun onSaveInstanceState(out: Bundle) { out.putString("expression", expression); out.putBoolean("calculated", calculated); super.onSaveInstanceState(out) }
    private fun press(key: String) {
        when(key) {
            "C" -> { expression = ""; calculated = false }
            "⌫" -> { expression = expression.dropLast(1); calculated = false }
            "=" -> { runCatching { WearCalculator.calculate(expression) }.onSuccess { expression = it; calculated = true }.onFailure { Toast.makeText(this, it.message ?: "Confira a expressão", Toast.LENGTH_SHORT).show() } }
            else -> { if(calculated && (key.first().isDigit() || key in listOf(",", "("))) expression = ""; calculated = false; if(expression.length < 128) expression += key }
        }
        display.text = expression.ifBlank { "0" }
    }
}

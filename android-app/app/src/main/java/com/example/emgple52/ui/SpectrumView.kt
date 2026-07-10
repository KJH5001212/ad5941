package com.example.emgple52.ui

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.util.AttributeSet
import android.view.View
import kotlin.math.max

/** Simple FFT magnitude spectrum view (bars over a frequency axis). */
class SpectrumView @JvmOverloads constructor(
    context: Context, attrs: AttributeSet? = null
) : View(context, attrs) {

    private var mags = FloatArray(0)
    private var binHz = 1.0
    var maxFreq = 200f
    var label = ""
    var barColor = Color.parseColor("#F5B544")

    private val barPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.STROKE; strokeWidth = 2.4f }
    private val axisPaint = Paint().apply { color = Color.parseColor("#2A3850"); strokeWidth = 1f }
    private val labelPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = Color.parseColor("#8A95A8"); textSize = 24f }
    private val tickPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = Color.parseColor("#6B7890"); textSize = 20f; textAlign = Paint.Align.CENTER }

    fun setData(m: FloatArray, bin: Double) { mags = m; binHz = bin; postInvalidateOnAnimation() }
    fun clearData() { mags = FloatArray(0); postInvalidate() }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        val w = width.toFloat(); val h = height.toFloat()
        val base = h - 18f
        barPaint.color = barColor
        canvas.drawLine(0f, base, w, base, axisPaint)
        if (label.isNotEmpty()) canvas.drawText(label, 8f, 22f, labelPaint)

        if (mags.size < 2 || binHz <= 0) {
            canvas.drawText("waiting...", 8f, base / 2f + 8f, labelPaint); return
        }
        val nBins = minOf(mags.size - 1, (maxFreq / binHz).toInt()).coerceAtLeast(1)
        var mx = 1e-9f
        for (k in 1..nBins) mx = max(mx, mags[k])

        for (k in 1..nBins) {
            val x = (k.toFloat() / nBins) * w
            val y = base - (mags[k] / mx) * (base - 24f)
            canvas.drawLine(x, base, x, y, barPaint)
        }
        // frequency ticks
        val ticks = intArrayOf(0, 50, 100, 150, 200)
        for (t in ticks) {
            if (t > maxFreq) continue
            val x = (t / maxFreq) * w
            canvas.drawText("$t", x.coerceIn(12f, w - 12f), h - 2f, tickPaint)
        }
    }
}

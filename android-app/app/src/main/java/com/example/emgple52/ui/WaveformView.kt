package com.example.emgple52.ui

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.DashPathEffect
import android.util.AttributeSet
import android.view.View
import kotlin.math.abs
import kotlin.math.max

/** Ring-buffer real-time waveform view with optional uV grid and fixed scale. */
class WaveformView @JvmOverloads constructor(
    context: Context, attrs: AttributeSet? = null
) : View(context, attrs) {

    private val capacity = 2500
    private val data = FloatArray(capacity)
    private var head = 0
    private var count = 0

    private val linePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.parseColor("#4F86F7"); strokeWidth = 2.4f; style = Paint.Style.STROKE
    }
    private val bgPaint = Paint().apply { color = Color.TRANSPARENT }
    private val gridPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.parseColor("#22314A"); strokeWidth = 1f; style = Paint.Style.STROKE
        pathEffect = DashPathEffect(floatArrayOf(6f, 8f), 0f)
    }
    private val zeroPaint = Paint().apply { color = Color.parseColor("#2A3850"); strokeWidth = 1f }
    private val axisTextPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.parseColor("#6B7890"); textSize = 22f; textAlign = Paint.Align.RIGHT
    }
    private val labelPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.parseColor("#8A95A8"); textSize = 24f
    }

    var label: String = ""
    var lineColor: Int = Color.parseColor("#4F86F7")
        set(value) { field = value; linePaint.color = value }

    /** Multiplier applied to stored volts for display (1e6 = uV, 1e3 = mV). */
    var displayScale = 1f
    /** Fixed +/- range in display units; 0 = auto-scale. */
    var fixedRange = 0f
    /** Draw dashed uV grid + right-side axis labels. */
    var showGrid = false
    var unitSuffix = "V"

    @Synchronized
    fun push(v: Float) {
        data[head] = v
        head = (head + 1) % capacity
        if (count < capacity) count++
    }

    fun clearData() {
        synchronized(this) { head = 0; count = 0 }
        postInvalidate()
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        val w = width.toFloat(); val h = height.toFloat(); val mid = h / 2f
        val scale = displayScale

        val n: Int
        val snapshot: FloatArray
        synchronized(this) {
            n = count
            snapshot = FloatArray(maxOf(n, 0))
            for (i in 0 until n) snapshot[i] = data[(head - n + i + capacity) % capacity] * scale
        }

        var range = fixedRange
        if (range <= 0f) {
            range = 1e-9f
            for (v in snapshot) range = max(range, abs(v))
            range *= 1.2f
        }

        if (showGrid) {
            // 4 dashed divisions
            for (g in -2..2) {
                val yy = mid - (g / 2f) * (h * 0.45f)
                if (g == 0) canvas.drawLine(0f, mid, w, mid, zeroPaint)
                else canvas.drawLine(0f, yy, w, yy, gridPaint)
            }
            canvas.drawText("${fmt(range)} $unitSuffix", w - 8f, 26f, axisTextPaint)
            canvas.drawText("0", w - 8f, mid + 8f, axisTextPaint)
            canvas.drawText("-${fmt(range)} $unitSuffix", w - 8f, h - 8f, axisTextPaint)
        }

        if (n >= 2) {
            val dx = w / (n - 1).toFloat()
            var prevX = 0f
            var prevY = mid - (snapshot[0] / range) * (h * 0.45f)
            for (i in 1 until n) {
                val x = i * dx
                val y = mid - (snapshot[i] / range) * (h * 0.45f)
                canvas.drawLine(prevX, prevY, x, y, linePaint)
                prevX = x; prevY = y
            }
        }
        if (label.isNotEmpty()) canvas.drawText(label, 10f, 26f, labelPaint)
    }

    private fun fmt(v: Float): String = when {
        v >= 100 -> String.format("%.0f", v)
        v == v.toLong().toFloat() -> String.format("%.0f", v) // whole numbers e.g. "1"
        else -> String.format("%.3g", v)
    }
}

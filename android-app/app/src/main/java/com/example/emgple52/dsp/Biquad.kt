package com.example.emgple52.dsp

import kotlin.math.PI
import kotlin.math.cos
import kotlin.math.sin

/**
 * Single 2nd-order IIR section (biquad) in Direct Form II Transposed.
 * Coefficients follow the RBJ Audio EQ Cookbook, normalized by a0.
 */
class Biquad(
    private val b0: Double,
    private val b1: Double,
    private val b2: Double,
    private val a1: Double,
    private val a2: Double
) {
    private var z1 = 0.0
    private var z2 = 0.0

    fun process(x: Double): Double {
        val y = b0 * x + z1
        z1 = b1 * x - a1 * y + z2
        z2 = b2 * x - a2 * y
        return y
    }

    fun reset() {
        z1 = 0.0
        z2 = 0.0
    }

    /**
     * Set internal state to the steady state for a constant DC input d,
     * to suppress the start-up transient (mainly for the high-pass section).
     */
    fun prime(d: Double) {
        val g = (b0 + b1 + b2) / (1.0 + a1 + a2) // DC gain
        val y = g * d
        z1 = y - b0 * d
        z2 = b2 * d - a2 * y
    }

    companion object {
        private const val BUTTERWORTH_Q = 0.70710678 // 2nd-order Butterworth response

        fun lowpass(fs: Double, f0: Double, q: Double = BUTTERWORTH_Q): Biquad {
            val w0 = 2.0 * PI * f0 / fs
            val cw = cos(w0); val sw = sin(w0)
            val alpha = sw / (2.0 * q)
            val a0 = 1.0 + alpha
            return Biquad(
                b0 = ((1.0 - cw) / 2.0) / a0,
                b1 = (1.0 - cw) / a0,
                b2 = ((1.0 - cw) / 2.0) / a0,
                a1 = (-2.0 * cw) / a0,
                a2 = (1.0 - alpha) / a0
            )
        }

        fun highpass(fs: Double, f0: Double, q: Double = BUTTERWORTH_Q): Biquad {
            val w0 = 2.0 * PI * f0 / fs
            val cw = cos(w0); val sw = sin(w0)
            val alpha = sw / (2.0 * q)
            val a0 = 1.0 + alpha
            return Biquad(
                b0 = ((1.0 + cw) / 2.0) / a0,
                b1 = (-(1.0 + cw)) / a0,
                b2 = ((1.0 + cw) / 2.0) / a0,
                a1 = (-2.0 * cw) / a0,
                a2 = (1.0 - alpha) / a0
            )
        }

        /** Notch (band-stop). q = f0 / bandwidth. e.g. 59-61 Hz -> BW=2 -> q=30 */
        fun notch(fs: Double, f0: Double, q: Double): Biquad {
            val w0 = 2.0 * PI * f0 / fs
            val cw = cos(w0); val sw = sin(w0)
            val alpha = sw / (2.0 * q)
            val a0 = 1.0 + alpha
            return Biquad(
                b0 = 1.0 / a0,
                b1 = (-2.0 * cw) / a0,
                b2 = 1.0 / a0,
                a1 = (-2.0 * cw) / a0,
                a2 = (1.0 - alpha) / a0
            )
        }
    }
}

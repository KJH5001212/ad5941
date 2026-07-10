package com.example.emgple52.dsp

import kotlin.math.abs
import kotlin.math.sqrt

/**
 * Rolling buffer of recent (filtered) samples in volts, with EMG metrics:
 *  - RMS amplitude
 *  - iEMG (integrated EMG = integral of |x|)
 *  - median / mean frequency (via FFT)
 */
class MetricsEngine(private val fs: Double, private val capacity: Int = 1024) {

    private val buf = FloatArray(capacity)
    private var head = 0
    private var count = 0

    @Synchronized
    fun push(v: Float) {
        buf[head] = v
        head = (head + 1) % capacity
        if (count < capacity) count++
    }

    @Synchronized
    fun reset() { head = 0; count = 0 }

    /** Most recent n samples in chronological order. */
    @Synchronized
    fun recent(n: Int): FloatArray {
        val m = minOf(n, count)
        val out = FloatArray(m)
        for (i in 0 until m) out[i] = buf[(head - m + i + capacity) % capacity]
        return out
    }

    /** RMS over the last [window] samples, in volts. */
    fun rmsVolts(window: Int): Double {
        val x = recent(window); if (x.isEmpty()) return 0.0
        var s = 0.0; for (v in x) s += v.toDouble() * v
        return sqrt(s / x.size)
    }

    /** Integrated EMG over the last [window] samples, in volt-seconds. */
    fun iemgVoltSec(window: Int): Double {
        val x = recent(window); if (x.isEmpty()) return 0.0
        var s = 0.0; for (v in x) s += abs(v.toDouble())
        return s / fs
    }

    fun medianFreqHz(window: Int = 512): Double = Spectrum.medianFrequency(recent(window), fs)
    fun meanFreqHz(window: Int = 512): Double = Spectrum.meanFrequency(recent(window), fs)

    /** Magnitude spectrum (mags, binHz) of the last [window] samples, or null. */
    fun magnitudeSpectrum(window: Int = 512): Pair<FloatArray, Double>? =
        Spectrum.magnitude(recent(window), fs)
}

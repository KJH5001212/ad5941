package com.example.emgple52.dsp

import kotlin.math.PI
import kotlin.math.cos
import kotlin.math.sqrt

/** Minimal radix-2 FFT and spectral metrics (mean / median frequency, magnitude spectrum). */
object Spectrum {

    /** In-place iterative radix-2 FFT. size must be a power of two. */
    fun fft(re: DoubleArray, im: DoubleArray) {
        val n = re.size
        var j = 0
        for (i in 1 until n) {
            var bit = n shr 1
            while (j and bit != 0) { j = j xor bit; bit = bit shr 1 }
            j = j or bit
            if (i < j) {
                val tr = re[i]; re[i] = re[j]; re[j] = tr
                val ti = im[i]; im[i] = im[j]; im[j] = ti
            }
        }
        var len = 2
        while (len <= n) {
            val ang = -2.0 * PI / len
            val wlenR = cos(ang); val wlenI = kotlin.math.sin(ang)
            var i = 0
            while (i < n) {
                var wR = 1.0; var wI = 0.0
                for (k in 0 until len / 2) {
                    val a = i + k; val b = i + k + len / 2
                    val vR = re[b] * wR - im[b] * wI
                    val vI = re[b] * wI + im[b] * wR
                    re[b] = re[a] - vR; im[b] = im[a] - vI
                    re[a] += vR; im[a] += vI
                    val nwR = wR * wlenR - wI * wlenI
                    val nwI = wR * wlenI + wI * wlenR
                    wR = nwR; wI = nwI
                }
                i += len
            }
            len = len shl 1
        }
    }

    private fun largestPow2(n: Int): Int { var p = 1; while (p * 2 <= n) p *= 2; return p }

    /** Power spectrum for k = 0..N/2 (DC zeroed) plus bin width in Hz. Hann-windowed. */
    fun power(samples: FloatArray, fs: Double): Pair<DoubleArray, Double>? {
        val n = largestPow2(samples.size)
        if (n < 16) return null
        val start = samples.size - n
        val re = DoubleArray(n); val im = DoubleArray(n)
        var mean = 0.0; for (i in 0 until n) mean += samples[start + i]; mean /= n
        for (i in 0 until n) {
            val w = 0.5 - 0.5 * cos(2.0 * PI * i / (n - 1))
            re[i] = (samples[start + i] - mean) * w
        }
        fft(re, im)
        val half = n / 2
        val p = DoubleArray(half + 1)
        for (k in 1..half) p[k] = re[k] * re[k] + im[k] * im[k]
        return p to (fs / n)
    }

    /** Magnitude spectrum (sqrt of power), k = 0..N/2, plus bin width in Hz. */
    fun magnitude(samples: FloatArray, fs: Double): Pair<FloatArray, Double>? {
        val (p, bin) = power(samples, fs) ?: return null
        val m = FloatArray(p.size) { sqrt(p[it]).toFloat() }
        return m to bin
    }

    fun medianFrequency(samples: FloatArray, fs: Double): Double {
        val (p, bin) = power(samples, fs) ?: return 0.0
        var total = 0.0; for (v in p) total += v
        if (total <= 0.0) return 0.0
        var cum = 0.0
        for (k in p.indices) { cum += p[k]; if (cum >= total / 2.0) return k * bin }
        return (p.size - 1) * bin
    }

    fun meanFrequency(samples: FloatArray, fs: Double): Double {
        val (p, bin) = power(samples, fs) ?: return 0.0
        var num = 0.0; var den = 0.0
        for (k in p.indices) { num += k * bin * p[k]; den += p[k] }
        return if (den > 0) num / den else 0.0
    }
}

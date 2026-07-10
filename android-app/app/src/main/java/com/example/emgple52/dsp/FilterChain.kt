package com.example.emgple52.dsp

/**
 * Per-channel filter chain for EMG.
 *
 * Measured signal characteristics for this device: EMG energy spans ~20-150 Hz
 * with strong 60 Hz mains interference. Defaults are therefore:
 *   High-pass 20 Hz, Low-pass 150 Hz, Band-stop 59-61 Hz (center 60 Hz, Q=30).
 *
 * Processing order: HPF -> Notch -> LPF.
 *
 * To change cutoffs, edit the constants below.
 *  - A 0.5-20 Hz pass band removes most EMG; use it only for low-frequency work.
 *  - The 60 Hz notch matters only if the low-pass cutoff is above 60 Hz.
 */
class FilterChain(private val fs: Double) {

    companion object {
        const val HP_CUTOFF = 20.0    // Hz
        const val LP_CUTOFF = 150.0   // Hz
        const val NOTCH_CENTER = 60.0 // Hz (mains frequency)
        const val NOTCH_Q = 30.0      // 60 / (61-59)
    }

    private val hp = Biquad.highpass(fs, HP_CUTOFF)
    private val lp = Biquad.lowpass(fs, LP_CUTOFF)
    private val notch = Biquad.notch(fs, NOTCH_CENTER, NOTCH_Q)

    @Volatile var hpEnabled = true
    @Volatile var lpEnabled = true
    @Volatile var notchEnabled = true

    fun process(input: Double): Double {
        var y = input
        if (hpEnabled) y = hp.process(y)
        if (notchEnabled) y = notch.process(y)
        if (lpEnabled) y = lp.process(y)
        return y
    }

    fun reset() {
        hp.reset(); lp.reset(); notch.reset()
    }

    /**
     * Prime the high-pass with the first sample so the start-up transient
     * (a large slow decay caused by the DC offset) is removed.
     */
    fun prime(firstSample: Double) {
        hp.prime(firstSample)
        notch.reset()
        lp.reset()
    }
}

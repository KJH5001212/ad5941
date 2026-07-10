package com.example.emgple52.data

/**
 * Parses BLE notification bytes from the "8ch" EMG firmware (nRF52832 + ADS1299).
 *
 * Protocol (ASCII lines over Nordic UART Service TX notify):
 *   Data line   : "d1 d2 d3 d4 d5 d6 d7 d8\n"  - 8 space-separated raw int24
 *                 values, one snapshot every 50 ms (20 Hz).
 *   Status line : "STATUS id=0x3C OK cnt=1234 drdy=1234\n" - once per second.
 *
 * Notifications are chunked to fit the ATT MTU, so lines may arrive split
 * across packets - bytes are buffered and reassembled on '\n'.
 *
 * Raw -> volts: V = raw * VREF / (2^23 * gain). ADS1299 internal VREF = 4.5 V,
 * channel gain 24 (firmware real-EMG mode).
 *
 * Note: current board carries an ADS1299-4 (4-channel variant) - columns
 * ch5..ch8 arrive as 0 until the chip is swapped for an 8-channel ADS1299IPAG.
 */
class FrameParser {

    /** Number of values expected per data line. */
    @Volatile var channelCount = 8

    /** ADS1299 conversion parameters (firmware: internal VREF 4.5 V, gain 24). */
    @Volatile var vref = 4.5
    @Volatile var gain = 24.0

    /** Which channel the live waveform shows (0-based). */
    @Volatile var displayChannel = 0

    /** Most recent STATUS line from the device (e.g. "STATUS id=0x3C OK cnt=.. drdy=.."). */
    @Volatile var lastStatus: String? = null
    @Volatile var statusCount = 0L
    @Volatile var frameCount = 0L

    val lsbVolts: Double get() = vref / (8388608.0 * gain) // 2^23

    private val lineBuf = StringBuilder()

    /** Feed raw notification bytes; returns complete frames (volts per channel). */
    @Synchronized
    fun feed(data: ByteArray): List<FloatArray> {
        val out = ArrayList<FloatArray>()
        for (byte in data) {
            when (val c = byte.toInt().toChar()) {
                '\n' -> {
                    parseLine(lineBuf.toString(), out)
                    lineBuf.setLength(0)
                }
                '\r' -> { /* ignore */ }
                else -> {
                    lineBuf.append(c)
                    // Runaway guard: no valid line is anywhere near this long.
                    if (lineBuf.length > 512) lineBuf.setLength(0)
                }
            }
        }
        return out
    }

    private fun parseLine(line: String, out: ArrayList<FloatArray>) {
        val t = line.trim()
        if (t.isEmpty()) return
        if (t.startsWith("STATUS")) {
            lastStatus = t
            statusCount++
            return
        }
        val parts = t.split(' ')
        val vals = FloatArray(channelCount)
        var n = 0
        for (p in parts) {
            if (p.isEmpty()) continue
            if (n >= channelCount) break
            // Any non-numeric token means this is not a data line - drop it whole.
            val raw = p.toLongOrNull() ?: return
            vals[n++] = (raw * lsbVolts).toFloat()
        }
        if (n == 0) return
        frameCount++
        out.add(vals) // channels missing at the tail stay 0
    }

    @Synchronized
    fun reset() {
        lineBuf.setLength(0)
        lastStatus = null
        statusCount = 0
        frameCount = 0
    }
}

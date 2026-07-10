package com.example.emgple52.data

import org.json.JSONObject

/**
 * Parses NUS notify frames from the AD5941 potentiostat firmware.
 *
 * Protocol (newline-delimited JSON over NUS TX 6E400003):
 *   Data   : {"d":[[seq,t_ms,current_nA,range],...]}\n   (batched samples)
 *   Status : {"st":"run|idle|rest","mode":M,"rate":R,"cyc":N,"range":idx,
 *             "pend":X,"buf":Y,"gap":0/1}\n                (~1/s)
 *
 * Notifications are chunked to the ATT MTU -> bytes are buffered and
 * reassembled on '\n'. Samples are de-duplicated by seq (in-order), so
 * firmware re-transmits after reconnect are absorbed without double-plotting.
 */
class PstatParser {

    data class Sample(val seq: Long, val tMs: Long, val currentNa: Float, val range: Int)

    /* Latest status fields */
    @Volatile var state: String = "idle"
    @Volatile var mode: Int = 0
    @Volatile var rate: Int = 0
    @Volatile var cyc: Long = 0
    @Volatile var range: Int = 0
    @Volatile var pend: Long = 0      // device unsent count
    @Volatile var buf: Long = 0       // device unacked count
    @Volatile var gap: Boolean = false
    @Volatile var lastStatus: String? = null

    /* ACK/dedup tracking (highest contiguous seq accepted this run) */
    @Volatile var maxSeq: Long = -1
    private var lastSeq: Long = -1
    @Volatile var gapSeen: Boolean = false   // a mid-stream seq jump was observed

    private val lineBuf = StringBuilder()

    /** Call when a new run starts (Start command) to reset seq tracking. */
    @Synchronized
    fun beginRun() {
        maxSeq = -1; lastSeq = -1; gapSeen = false
    }

    @Synchronized
    fun reset() {
        lineBuf.setLength(0)
        beginRun()
        lastStatus = null
        state = "idle"   // 상태도 초기화 — stop 후 stale "run" 으로 재조정이 running 을 되살리는 것 방지
    }

    @Synchronized
    fun feed(data: ByteArray): List<Sample> {
        val out = ArrayList<Sample>()
        for (byte in data) {
            when (val c = byte.toInt().toChar()) {
                '\n' -> { parseLine(lineBuf.toString(), out); lineBuf.setLength(0) }
                '\r' -> { /* ignore */ }
                else -> {
                    lineBuf.append(c)
                    if (lineBuf.length > 2048) lineBuf.setLength(0)   // runaway guard
                }
            }
        }
        return out
    }

    private fun parseLine(line: String, out: ArrayList<Sample>) {
        val t = line.trim()
        if (t.length < 5 || t[0] != '{') return
        try {
            val o = JSONObject(t)
            when {
                o.has("d") -> {
                    val arr = o.getJSONArray("d")
                    for (i in 0 until arr.length()) {
                        val s = arr.getJSONArray(i)
                        val seq = s.getLong(0)
                        // run restart -> seq counter reset
                        if (seq < lastSeq) { lastSeq = -1; maxSeq = -1 }
                        if (seq <= lastSeq) continue           // duplicate (resend) -> skip
                        if (lastSeq >= 0 && seq > lastSeq + 1) gapSeen = true
                        lastSeq = seq
                        if (seq > maxSeq) maxSeq = seq
                        out.add(Sample(seq, s.getLong(1), s.getDouble(2).toFloat(), s.getInt(3)))
                    }
                }
                o.has("st") -> {
                    state = o.optString("st", state)
                    mode = o.optInt("mode", mode)
                    rate = o.optInt("rate", rate)
                    cyc = o.optLong("cyc", cyc)
                    range = o.optInt("range", range)
                    pend = o.optLong("pend", pend)
                    buf = o.optLong("buf", buf)
                    gap = o.optInt("gap", 0) != 0
                    lastStatus = t
                }
            }
        } catch (_: Exception) { /* partial/garbage line -> drop */ }
    }
}

package com.example.emgple52.data

import android.content.Context
import java.io.BufferedWriter
import java.io.File
import java.io.FileWriter
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * Writes samples to a CSV file in app-specific external storage:
 *   /storage/emulated/0/Android/data/com.example.emgple52/files/
 * The file is copied to the public Downloads folder on export.
 * Data is flushed periodically so the file on disk stays current.
 */
class Recorder(private val context: Context, private val fs: Double = 500.0) {

    private var writer: BufferedWriter? = null
    private var file: File? = null
    @Volatile var recording = false
        private set
    private var sampleIndex = 0L
    private var sinceFlush = 0
    private val flushEvery = 250 // ~0.5 s

    /**
     * Start a CSV with an explicit header line (potentiostat: "seq,t_ms,current_nA,range").
     * Use writeRaw() to append pre-formatted rows.
     */
    fun startCsv(prefix: String, header: String): File {
        val dir = context.getExternalFilesDir(null)
            ?: throw IllegalStateException("External storage unavailable")
        if (!dir.exists()) dir.mkdirs()
        val name = prefix + SimpleDateFormat("yyyyMMdd_HHmmss", Locale.US).format(Date()) + ".csv"
        val f = File(dir, name)
        val w = BufferedWriter(FileWriter(f))
        w.write(header); w.newLine(); w.flush()
        writer = w; file = f; sampleIndex = 0; sinceFlush = 0; recording = true
        return f
    }

    @Synchronized
    fun writeRaw(line: String) {
        val w = writer ?: return
        w.write(line); w.newLine()
        if (++sinceFlush >= flushEvery) {
            try { w.flush() } catch (_: Exception) {}
            sinceFlush = 0
        }
    }

    fun start(columns: List<String>): File {
        val dir = context.getExternalFilesDir(null)
            ?: throw IllegalStateException("External storage unavailable")
        if (!dir.exists()) dir.mkdirs()
        val name = "emg_" + SimpleDateFormat("yyyyMMdd_HHmmss", Locale.US).format(Date()) + ".csv"
        val f = File(dir, name)
        val w = BufferedWriter(FileWriter(f))
        val header = StringBuilder("index,time_s")
        for (c in columns) header.append(',').append(c)
        w.write(header.toString()); w.newLine()
        w.flush()
        writer = w; file = f; sampleIndex = 0; sinceFlush = 0; recording = true
        return f
    }

    @Synchronized
    fun write(values: FloatArray) {
        val w = writer ?: return
        val t = sampleIndex / fs
        val sb = StringBuilder()
        sb.append(sampleIndex).append(',').append(String.format(Locale.US, "%.4f", t))
        for (v in values) sb.append(',').append(v)
        w.write(sb.toString()); w.newLine()
        sampleIndex++
        if (++sinceFlush >= flushEvery) {
            try { w.flush() } catch (_: Exception) {}
            sinceFlush = 0
        }
    }

    @Synchronized
    fun flush() { try { writer?.flush() } catch (_: Exception) {} }

    fun currentFile(): File? = file

    @Synchronized
    fun stop(): File? {
        recording = false
        try { writer?.flush(); writer?.close() } catch (_: Exception) {}
        writer = null
        return file
    }
}

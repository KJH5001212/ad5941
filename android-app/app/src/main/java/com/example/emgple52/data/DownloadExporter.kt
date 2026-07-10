package com.example.emgple52.data

import android.content.ContentValues
import android.content.Context
import android.media.MediaScannerConnection
import android.os.Build
import android.os.Environment
import android.provider.MediaStore
import java.io.File

/** Copies a completed CSV file into the device's public Downloads folder. */
object DownloadExporter {

    /** @return a display path on success, or null on failure. */
    fun saveToDownloads(context: Context, src: File): String? {
        return try {
            val bytes = src.readBytes()
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                val resolver = context.contentResolver
                val values = ContentValues().apply {
                    put(MediaStore.Downloads.DISPLAY_NAME, src.name)
                    put(MediaStore.Downloads.MIME_TYPE, "text/csv")
                    put(MediaStore.Downloads.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS)
                    put(MediaStore.Downloads.IS_PENDING, 1)
                }
                val uri = resolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values)
                    ?: return null
                resolver.openOutputStream(uri)?.use { it.write(bytes) }
                values.clear()
                values.put(MediaStore.Downloads.IS_PENDING, 0)
                resolver.update(uri, values, null, null)
                "Download/${src.name}"
            } else {
                @Suppress("DEPRECATION")
                val dir = Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS)
                if (!dir.exists()) dir.mkdirs()
                val out = File(dir, src.name)
                out.writeBytes(bytes)
                MediaScannerConnection.scanFile(context, arrayOf(out.absolutePath), arrayOf("text/csv"), null)
                out.absolutePath
            }
        } catch (e: Exception) {
            null
        }
    }
}

package com.example.emgple52

import android.Manifest
import android.bluetooth.BluetoothAdapter
import android.bluetooth.BluetoothDevice
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.res.ColorStateList
import android.location.LocationManager
import android.provider.Settings
import android.graphics.Color
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.view.View
import android.widget.ArrayAdapter
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.content.FileProvider
import com.example.emgple52.ble.BleManager
import com.example.emgple52.data.DownloadExporter
import com.example.emgple52.data.PstatParser
import com.example.emgple52.data.Recorder
import com.example.emgple52.databinding.ActivityMainBinding
import java.io.File
import java.util.Locale

/**
 * AD5941 potentiostat controller (nRF52832, NUS JSON protocol).
 *  - Sends run parameters (potential fixed 0.5 V, autorange, sample rate, mode) as JSON commands.
 *  - Receives chronoamperometry samples ({"d":[[seq,t_ms,current_nA,range],...]}), plots current
 *    vs time, records CSV, and ACKs received seq so the device frees its lossless buffer.
 *  - Status frames drive the run state + buffered/sync indicator.
 */
class MainActivity : AppCompatActivity(), BleManager.Listener {

    private lateinit var b: ActivityMainBinding
    private lateinit var ble: BleManager
    private val parser = PstatParser()
    private lateinit var recorder: Recorder

    private val ui = Handler(Looper.getMainLooper())
    private val pending = ArrayList<PstatParser.Sample>()
    private val lock = Any()

    private var connected = false
    @Volatile private var running = false
    // Stop 을 누른 뒤, 기기가 아직 stop 을 처리 못 해 잠깐 더 오는 stale "run"
    // 상태 때문에 재조정이 running 을 되살리는 것을 막는 가드. 기기가 idle 을
    // 확인하면 해제된다.
    @Volatile private var userStopping = false
    private var lastAckMs = 0L
    private var recStartMs = 0L
    private var startedAtMs = 0L   // when Start was tapped (grace before trusting "idle")
    // ---- BLE receive diagnostics (shown on screen to localize any data-path issue) ----
    private var rxNotifs = 0L      // notifications received
    private var rxBytes = 0L       // total bytes received
    private var rxSamples = 0L     // samples successfully parsed
    private var lastRxLogMs = 0L   // throttle for RX timing log

    private val rangeLabels = arrayOf(
        "512kΩ", "256kΩ", "128kΩ", "64kΩ", "32kΩ", "16kΩ", "8kΩ", "4kΩ", "2kΩ", "1kΩ", "110Ω"
    )
    private fun rangeLabel(i: Int) = rangeLabels.getOrElse(i) { "?" }

    private data class DeviceItem(val device: BluetoothDevice, var rssi: Int, val name: String?)
    private val devices = ArrayList<DeviceItem>()
    private var scanAdapter: ArrayAdapter<String>? = null
    private var scanDialog: AlertDialog? = null
    private var pendingExport = false

    private val permLauncher = registerForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions()
    ) { r -> if (r.values.all { it }) ensureBtAndScan() else toast("Permissions denied; cannot scan") }

    private val enableBtLauncher = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult()
    ) { if (ble.isBluetoothOn()) doScan() else toast("Please turn on Bluetooth") }

    private val storagePermLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { granted -> if (granted && pendingExport) doExport() else if (!granted) toast("Storage permission required"); pendingExport = false }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        b = ActivityMainBinding.inflate(layoutInflater)
        setContentView(b.root)

        ble = BleManager(this).also { it.listener = this }
        recorder = Recorder(this)

        // Live current-vs-time trace (nA, auto-scale, green).
        b.waveLive.displayScale = 1f
        b.waveLive.fixedRange = 0f
        b.waveLive.showGrid = true
        b.waveLive.unitSuffix = "nA"
        b.waveLive.lineColor = Color.parseColor("#34D399")

        // Run mode -> show/hide relevant fields
        b.rgMode.setOnCheckedChangeListener { _, _ -> updateModeRows() }
        updateModeRows()

        b.btnStartStop.setOnClickListener { if (running) sendStop() else sendStart() }

        b.tabHome.setOnClickListener { /* home */ }
        b.tabDevice.setOnClickListener { requestPermsThenScan() }
        b.tabRecord.setOnClickListener { toggleRecord() }
        b.tabReport.setOnClickListener { export() }
        b.tabMore.setOnClickListener { showInfo() }

        updateRunBtn()
        startUiTicker()
    }

    override fun onDestroy() {
        super.onDestroy(); recorder.stop(); ble.close()
    }

    private fun updateModeRows() {
        b.rowTimed.visibility = if (b.rbTimed.isChecked) View.VISIBLE else View.GONE
        b.rowCycle.visibility = if (b.rbCycle.isChecked) View.VISIBLE else View.GONE
    }

    // ---------------- commands ----------------

    private fun sendStart() {
        android.util.Log.d("BLE", "CMD sendStart (was running=$running canSend=${ble.canSend()})")
        if (!ble.canSend()) { toast("Not connected"); return }
        val rate = b.etRate.text.toString().toIntOrNull()?.coerceIn(1, 100) ?: 10
        val auto = if (b.swAuto.isChecked) 1 else 0
        val mode = when {
            b.rbTimed.isChecked -> "timed"
            b.rbCycle.isChecked -> "cycle"
            else -> "continuous"
        }
        val dur = b.etDur.text.toString().toIntOrNull() ?: 60
        val on = b.etOn.text.toString().toIntOrNull() ?: 5
        val off = b.etOff.text.toString().toIntOrNull() ?: 295
        val cycles = b.etCycles.text.toString().toIntOrNull() ?: 0

        val json = "{\"cmd\":\"start\",\"mode\":\"$mode\",\"rate\":$rate,\"auto\":$auto," +
            "\"dur\":$dur,\"on\":$on,\"off\":$off,\"cycles\":$cycles}"

        parser.beginRun()
        b.waveLive.clearData()
        synchronized(lock) { pending.clear() }
        if (!ble.send(json.toByteArray())) { toast("Send failed"); return }
        userStopping = false
        running = true; startedAtMs = SystemClock.elapsedRealtime(); updateRunBtn()
        if (!recorder.recording) startRecording()
        toast("Started ($mode)")
    }

    private fun sendStop() {
        android.util.Log.d("BLE", "CMD sendStop (was running=$running)")
        ble.send("{\"cmd\":\"stop\"}".toByteArray())
        userStopping = true
        running = false; updateRunBtn()
        if (recorder.recording) stopRecordingAndSave()
        clearLiveView()   // BLE 연결은 유지, 그래프/수신 캐시만 리셋
    }

    /** Stop 후 화면과 수신 캐시를 깨끗이 비운다. BLE 연결은 건드리지 않는다. */
    private fun clearLiveView() {
        synchronized(lock) { pending.clear() }
        parser.reset()                 // 파서 내부 버퍼/seq/state 초기화
        b.waveLive.clearData()
        b.tvCurrent.text = "— nA"
        b.tvRange.text = "—"
        b.tvState.text = "idle"
        b.waveLive.postInvalidateOnAnimation()
    }

    private fun updateRunBtn() {
        b.btnStartStop.text = if (running) "Stop" else "Start"
        val c = if (running) Color.parseColor("#E05260") else Color.parseColor("#2ECC71")
        b.btnStartStop.backgroundTintList = ColorStateList.valueOf(c)
    }

    // ---------------- BleManager.Listener ----------------

    override fun onScanResult(device: BluetoothDevice, rssi: Int, name: String?) {
        val ex = devices.indexOfFirst { it.device.address == device.address }
        if (ex >= 0) devices[ex].rssi = rssi else devices.add(DeviceItem(device, rssi, name))
        scanAdapter?.apply {
            clear(); devices.forEach { add("${it.name ?: "(no name)"}  [${it.device.address}]  ${it.rssi}dBm") }
            notifyDataSetChanged()
        }
    }

    override fun onConnectionStateChanged(connected: Boolean, deviceName: String?) {
        this.connected = connected
        if (connected) {
            scanDialog?.dismiss()
            parser.reset()
            rxNotifs = 0; rxBytes = 0; rxSamples = 0
            b.waveLive.clearData()
            deviceName?.let { b.tvDeviceName.text = it }
            b.tvConnPill.text = "● Connected"
            b.tvConnPill.setTextColor(Color.parseColor("#2ECC71"))
            toast("Connected")
        } else {
            running = false; updateRunBtn()
            b.tvConnPill.text = "● Disconnected"
            b.tvConnPill.setTextColor(Color.parseColor("#8A95A8"))
            b.tvState.text = "disconnected"
            b.dotState.setBackgroundResource(R.drawable.dot_gray)
            if (recorder.recording) stopRecordingAndSave()
            toast("Disconnected")
        }
    }

    override fun onNotifyReady(uuid: String) {
        // BALANCED (~30-50 ms) instead of HIGH (~12 ms): fast enough that 10 Hz data
        // streams smoothly (no "10 s batch"), but draws far less radio power than HIGH.
        // On this power-marginal board, HIGH's extra draw during afe_start's power-up
        // inrush was tipping it into brown-out (PC/bleak used a slow interval -> 5/5 stable).
        ble.requestHighPriority(false)
        ui.postDelayed({ if (ble.canSend()) ble.send("{\"cmd\":\"status\"}".toByteArray()) }, 300)
    }

    override fun onDataReceived(bytes: ByteArray) {
        rxBytes += bytes.size
        rxNotifs++
        val samples = parser.feed(bytes)
        if (samples.isNotEmpty()) {
            rxSamples += samples.size
            synchronized(lock) { pending.addAll(samples) }
        }
        val now = SystemClock.elapsedRealtime()
        if (now - lastRxLogMs >= 1000) {
            lastRxLogMs = now
            android.util.Log.d("BLE", "RXTIME rxSamples=$rxSamples rxNotifs=$rxNotifs")
        }
    }

    override fun onLog(message: String) { android.util.Log.d("BLE", message) }

    // ---------------- UI ticker ----------------

    private fun startUiTicker() {
        ui.post(object : Runnable {
            override fun run() { tick(); ui.postDelayed(this, 33) }
        })
    }

    private fun tick() {
        val drained: List<PstatParser.Sample>
        synchronized(lock) {
            if (pending.isNotEmpty()) { drained = ArrayList(pending); pending.clear() } else drained = emptyList()
        }
        // pending 은 항상 비운다(누적 방지). 그리기·기록은 측정 중(running)일 때만
        // → Stop 후 뒤늦게 온 잔여 프레임은 조용히 폐기되어 화면을 재오염 안 함.
        if (running && drained.isNotEmpty()) {
            for (s in drained) {
                b.waveLive.push(s.currentNa)
                if (recorder.recording) {
                    recorder.writeRaw("${s.seq},${s.tMs},${s.currentNa},${s.range}")
                }
            }
            val last = drained.last()
            b.tvCurrent.text = String.format(Locale.US, "%.3f nA", last.currentNa)
            b.tvRange.text = rangeLabel(last.range)
            b.waveLive.postInvalidateOnAnimation()
        } else if (drained.isNotEmpty()) {
            android.util.Log.d("BLE", "TICK dropped ${drained.size} samples by running-gate (running=false)")
        }

        val now = SystemClock.elapsedRealtime()

        // periodic ACK (frees device lossless buffer) + reconcile run state / status UI
        if (connected && now - lastAckMs >= 500) {
            lastAckMs = now
            // ACK 는 누적식(seq 이하 전부 해제) → 큐가 밀려있으면 건너뛴다.
            // 다음 ACK 가 더 높은 seq 로 커버하므로 손실 없음. ACK 가 큐에 쌓여
            // start/stop 명령을 밀어내고 결국 연결이 끊기던 것을 방지.
            if (parser.maxSeq >= 0 && ble.queueDepth() < 3) ble.send("{\"cmd\":\"ack\",\"seq\":${parser.maxSeq}}".toByteArray())

            // Reconcile the button with the device's reported state.
            // "run"/"rest" -> confirm running. "idle" -> only trust after a grace
            // window, so we don't revert before the device's first status arrives.
            val st = parser.state
            val dev = st == "run" || st == "rest"
            if (dev && !userStopping) {
                if (!running) { android.util.Log.d("BLE", "RECON running->TRUE (st=$st)"); running = true; updateRunBtn() }
            } else if (!dev) {
                if (userStopping) { userStopping = false; android.util.Log.d("BLE", "RECON stop confirmed (idle)") }
                if (running && now - startedAtMs > 5000) {
                    android.util.Log.d("BLE", "RECON running->FALSE (grace expired, st=$st)"); running = false; updateRunBtn()
                }
            }

            b.tvState.text = when (st) {
                "run" -> "measuring (cycle ${parser.cyc})"
                "rest" -> "cycle rest (cycle ${parser.cyc})"
                else -> "idle"
            }
            b.dotState.setBackgroundResource(if (st == "run") R.drawable.dot_green else R.drawable.dot_gray)
            // DIAGNOSTIC: shows exactly where the data path stands.
            //   notif=0            -> notifications not arriving (BLE notify setup)
            //   notif>0 samp=0     -> arriving but not parsed (frame/format)
            //   samp>0             -> received & parsed OK (any issue is display)
            b.tvSync.text = "notif=$rxNotifs samp=$rxSamples tx=${ble.writesDone}/${ble.writesIssued} " +
                "cs=${ble.canSend()} mtu=${ble.currentMtu()}"
        }

        if (recorder.recording) {
            val s = (SystemClock.elapsedRealtime() - recStartMs) / 1000
            b.lblRecord.text = String.format(Locale.US, "%02d:%02d", s / 60, s % 60)
        }
    }

    // ---------------- recording / export ----------------

    private fun toggleRecord() {
        if (recorder.recording) stopRecordingAndSave() else startRecording()
    }

    private fun startRecording() {
        try {
            recorder.startCsv("pstat_", "seq,t_ms,current_nA,range")
            recStartMs = SystemClock.elapsedRealtime()
            b.imgRecord.setColorFilter(Color.parseColor("#E05260"))
            b.lblRecord.setTextColor(Color.parseColor("#E05260"))
        } catch (e: Exception) { toast("Recording failed: ${e.message}") }
    }

    private fun stopRecordingAndSave() {
        val f = recorder.stop()
        b.imgRecord.setColorFilter(Color.parseColor("#8A95A8"))
        b.lblRecord.setTextColor(Color.parseColor("#8A95A8"))
        b.lblRecord.text = "Record"
        if (f != null) {
            val p = DownloadExporter.saveToDownloads(this, f)
            toast(if (p != null) "Saved to $p" else "Saved: ${f.name}")
        }
    }

    private fun showInfo() {
        val msg = "Device: ad5941 (nRF52832 + AD5941)\n" +
            "Technique: chronoamperometry @ +0.5 V (fixed)\n" +
            "Current: autorange (512kΩ..110Ω), reported in nA\n" +
            "Data: {\"d\":[[seq,t_ms,current_nA,range],...]} over NUS\n" +
            "CSV: seq,t_ms,current_nA,range\n\n" +
            "Last STATUS:\n${parser.lastStatus ?: "(none yet)"}"
        AlertDialog.Builder(this).setTitle("Info").setMessage(msg).setPositiveButton("OK", null).show()
    }

    private fun export() {
        if (Build.VERSION.SDK_INT <= Build.VERSION_CODES.P &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.WRITE_EXTERNAL_STORAGE)
            != PackageManager.PERMISSION_GRANTED
        ) { pendingExport = true; storagePermLauncher.launch(Manifest.permission.WRITE_EXTERNAL_STORAGE); return }
        doExport()
    }

    private fun doExport() {
        val f: File? = if (recorder.recording) { recorder.flush(); recorder.currentFile() }
        else getExternalFilesDir(null)?.listFiles { x -> x.name.endsWith(".csv") }?.maxByOrNull { it.lastModified() }
        if (f == null) { toast("Nothing to export yet"); return }
        val path = DownloadExporter.saveToDownloads(this, f)
        toast(if (path != null) "Saved to $path" else "Export failed")
        shareFile(f)
    }

    private fun shareFile(file: File) {
        try {
            val uri: Uri = FileProvider.getUriForFile(this, "$packageName.fileprovider", file)
            val send = Intent(Intent.ACTION_SEND).apply {
                type = "text/csv"; putExtra(Intent.EXTRA_STREAM, uri); addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
            }
            startActivity(Intent.createChooser(send, "Export ${file.name}"))
        } catch (_: Exception) { }
    }

    // ---------------- permissions / scan ----------------

    private fun requiredPerms(): Array<String> =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S)
            arrayOf(Manifest.permission.BLUETOOTH_SCAN, Manifest.permission.BLUETOOTH_CONNECT)
        else arrayOf(Manifest.permission.ACCESS_FINE_LOCATION)

    private fun requestPermsThenScan() {
        val missing = requiredPerms().filter {
            ContextCompat.checkSelfPermission(this, it) != PackageManager.PERMISSION_GRANTED
        }
        if (missing.isEmpty()) ensureBtAndScan() else permLauncher.launch(missing.toTypedArray())
    }

    private fun ensureBtAndScan() {
        if (!ble.isBluetoothOn()) {
            enableBtLauncher.launch(Intent(BluetoothAdapter.ACTION_REQUEST_ENABLE)); return
        }
        if (Build.VERSION.SDK_INT <= Build.VERSION_CODES.R && !locationEnabled()) {
            AlertDialog.Builder(this)
                .setTitle("Turn on Location")
                .setMessage("On this Android version, Bluetooth scanning requires Location (GPS) to be ON. Enable it, then scan again.")
                .setPositiveButton("Open settings") { _, _ ->
                    startActivity(Intent(Settings.ACTION_LOCATION_SOURCE_SETTINGS))
                }
                .setNegativeButton("Cancel", null)
                .show()
            return
        }
        doScan()
    }

    private fun locationEnabled(): Boolean {
        val lm = getSystemService(Context.LOCATION_SERVICE) as? LocationManager ?: return false
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) lm.isLocationEnabled
        else lm.isProviderEnabled(LocationManager.GPS_PROVIDER) ||
            lm.isProviderEnabled(LocationManager.NETWORK_PROVIDER)
    }

    private fun doScan() {
        devices.clear()
        scanAdapter = ArrayAdapter(this, android.R.layout.simple_list_item_1, ArrayList())
        scanDialog = AlertDialog.Builder(this)
            .setTitle("Select device (scanning...)")
            .setAdapter(scanAdapter) { _, which -> if (which < devices.size) ble.connect(devices[which].device) }
            .setNegativeButton("Close") { _, _ -> ble.stopScan() }
            .create()
        scanDialog?.show(); ble.startScan()
        ui.postDelayed({
            if (scanDialog?.isShowing == true && devices.isEmpty()) {
                scanDialog?.setTitle("No devices found")
                toast("No devices found. Make sure Location is ON and the device is powered.")
            }
        }, 12_500)
    }

    private fun toast(msg: String) = Toast.makeText(this, msg, Toast.LENGTH_SHORT).show()
}

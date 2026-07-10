package com.example.emgple52.ble

import android.annotation.SuppressLint
import android.bluetooth.BluetoothAdapter
import android.bluetooth.BluetoothDevice
import android.bluetooth.BluetoothGatt
import android.bluetooth.BluetoothGattCallback
import android.bluetooth.BluetoothGattCharacteristic
import android.bluetooth.BluetoothGattDescriptor
import android.bluetooth.BluetoothManager
import android.bluetooth.BluetoothProfile
import android.bluetooth.BluetoothStatusCodes
import android.bluetooth.le.BluetoothLeScanner
import android.bluetooth.le.ScanCallback
import android.bluetooth.le.ScanResult
import android.bluetooth.le.ScanSettings
import android.content.Context
import android.os.Build
import android.os.Handler
import android.os.Looper
import java.util.UUID

/**
 * BLE connection manager.
 * PLE-52 (nRF52832) does not expose its data service/characteristic UUID, so
 * after connecting we discover the GATT services and auto-pick a notify-capable characteristic.
 * Priority: Nordic UART Service (NUS) TX, otherwise the first Notify/Indicate characteristic.
 */
@SuppressLint("MissingPermission")
class BleManager(private val context: Context) {

    interface Listener {
        fun onScanResult(device: BluetoothDevice, rssi: Int, name: String?)
        fun onConnectionStateChanged(connected: Boolean, deviceName: String?)
        fun onNotifyReady(uuid: String)
        fun onDataReceived(bytes: ByteArray)
        fun onLog(message: String)
    }

    var listener: Listener? = null

    private val adapter: BluetoothAdapter? by lazy {
        (context.getSystemService(Context.BLUETOOTH_SERVICE) as? BluetoothManager)?.adapter
    }
    private var scanner: BluetoothLeScanner? = null
    private var gatt: BluetoothGatt? = null
    private var rxChar: BluetoothGattCharacteristic? = null   // phone -> peripheral (commands)
    private var mtu = 23
    private val main = Handler(Looper.getMainLooper())
    private val writeQueue = ArrayDeque<ByteArray>()   // serialize GATT writes
    private var writing = false
    var writesIssued = 0; private set     // diagnostic: writes handed to the stack
    var writesDone = 0; private set       // diagnostic: onCharacteristicWrite fired
    private var scanning = false
    private val seen = HashSet<String>()

    companion object {
        // Nordic UART Service - the most common guess for nRF52-based serial bridges
        val NUS_SERVICE: UUID = UUID.fromString("6E400001-B5A3-F393-E0A9-E50E24DCCA9E")
        val NUS_TX: UUID = UUID.fromString("6E400003-B5A3-F393-E0A9-E50E24DCCA9E") // peripheral -> phone (notify)
        val NUS_RX: UUID = UUID.fromString("6E400002-B5A3-F393-E0A9-E50E24DCCA9E") // phone -> peripheral (write)
        val CCCD: UUID = UUID.fromString("00002902-0000-1000-8000-00805F9B34FB")
    }

    /** True once the RX (command) characteristic is available. */
    fun canSend(): Boolean = gatt != null && rxChar != null

    /** Pending (unsent) writes in the queue. Used to skip coalescible ACKs when
     *  the link is backed up, so ACKs don't pile up and stall real commands. */
    fun queueDepth(): Int = synchronized(writeQueue) { writeQueue.size }

    /** Negotiated ATT MTU (23 until onMtuChanged). Diagnostic. */
    fun currentMtu(): Int = mtu

    /**
     * Request the phone-side connection interval. HIGH (~11-15 ms) for smooth
     * low-latency streaming while measuring; BALANCED (~30-50 ms) otherwise.
     * The central (phone) is authoritative, so this is the reliable way to
     * avoid the laggy/bursty data caused by a slow connection interval.
     */
    fun requestHighPriority(high: Boolean) {
        val g = gatt ?: return
        val p = if (high) BluetoothGatt.CONNECTION_PRIORITY_HIGH
                else BluetoothGatt.CONNECTION_PRIORITY_BALANCED
        try { g.requestConnectionPriority(p) } catch (_: Exception) {}
    }

    /**
     * Send a command to the device (NUS RX). Enqueued and issued one at a time,
     * waiting for onCharacteristicWrite (with a timeout fallback) before the next.
     * This is what makes writes reliable on Android — issuing back-to-back writes,
     * or writing while the GATT is busy, silently drops them.
     * The firmware's NUS RX only accepts WRITE_WITHOUT_RESPONSE, so we use that.
     */
    fun send(data: ByteArray): Boolean {
        if (gatt == null || rxChar == null) return false
        synchronized(writeQueue) { writeQueue.addLast(data) }
        main.post { pumpWrites() }
        return true
    }

    private val writeTimeout = Runnable { writing = false; pumpWrites() }

    private fun pumpWrites() {
        if (writing) return
        val data = synchronized(writeQueue) {
            if (writeQueue.isEmpty()) return else writeQueue.removeFirst()
        }
        val g = gatt ?: return
        val ch = rxChar ?: return
        writing = true
        val ok = rawWrite(g, ch, data)
        listener?.onLog("WRITE ${ch.uuid} props=0x%02x len=%d -> %s"
            .format(ch.properties, data.size, if (ok) "OK" else "FAIL"))
        if (!ok) {
            writing = false
            main.postDelayed({ pumpWrites() }, 30)   // retry shortly
            return
        }
        writesIssued++
        // Fallback ONLY: with-response writes reliably fire onCharacteristicWrite,
        // which advances the queue. This timeout must be LONGER than the slowest
        // connection interval — a 200 ms timeout fired before the callback at a
        // 1.5 s idle interval, advancing the queue mid-write -> GATT busy -> next
        // write FAILED (dropped the start command). 2 s covers any sane interval.
        main.postDelayed(writeTimeout, 2000)
    }

    private fun onWriteComplete() {
        main.removeCallbacks(writeTimeout)
        writesDone++
        writing = false
        main.post { pumpWrites() }
    }

    private fun rawWrite(
        g: BluetoothGatt, ch: BluetoothGattCharacteristic, data: ByteArray
    ): Boolean {
        // Use WRITE (with response) when supported: Android reliably transmits it
        // and we get real delivery confirmation via onCharacteristicWrite status.
        // No-response writes can report success without actually going over the air.
        val wt = if (ch.properties and BluetoothGattCharacteristic.PROPERTY_WRITE != 0)
            BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT
        else
            BluetoothGattCharacteristic.WRITE_TYPE_NO_RESPONSE
        return try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                g.writeCharacteristic(ch, data, wt) == BluetoothStatusCodes.SUCCESS
            } else {
                @Suppress("DEPRECATION")
                run { ch.writeType = wt; ch.value = data; g.writeCharacteristic(ch) }
            }
        } catch (_: Exception) { false }
    }

    fun isBluetoothOn(): Boolean = adapter?.isEnabled == true

    fun startScan() {
        val a = adapter ?: run { listener?.onLog("No Bluetooth adapter"); return }
        scanner = a.bluetoothLeScanner
        if (scanner == null) { listener?.onLog("Scanner unavailable (Bluetooth off?)"); return }
        seen.clear()
        scanning = true
        val settings = ScanSettings.Builder()
            .setScanMode(ScanSettings.SCAN_MODE_LOW_LATENCY)
            .build()
        scanner?.startScan(null, settings, scanCallback)
        listener?.onLog("Scan started")
        main.postDelayed({ stopScan() }, 12_000)
    }

    fun stopScan() {
        if (!scanning) return
        scanning = false
        try { scanner?.stopScan(scanCallback) } catch (_: Exception) {}
        listener?.onLog("Scan stopped")
    }

    private val scanCallback = object : ScanCallback() {
        override fun onScanResult(callbackType: Int, result: ScanResult) {
            val dev = result.device
            val addr = dev.address ?: return
            if (seen.add(addr)) {
                val name = result.scanRecord?.deviceName
                    ?: try { dev.name } catch (_: SecurityException) { null }
                main.post { listener?.onScanResult(dev, result.rssi, name) }
            }
        }

        override fun onScanFailed(errorCode: Int) {
            listener?.onLog("Scan failed: $errorCode")
        }
    }

    fun connect(device: BluetoothDevice) {
        stopScan()
        listener?.onLog("Connecting: ${device.address}")
        gatt = device.connectGatt(context, false, gattCallback, BluetoothDevice.TRANSPORT_LE)
    }

    fun disconnect() {
        gatt?.disconnect()
    }

    private fun cleanup() {
        try { gatt?.close() } catch (_: Exception) {}
        gatt = null
        rxChar = null
        mtu = 23
        main.removeCallbacks(writeTimeout)
        writing = false
        synchronized(writeQueue) { writeQueue.clear() }
    }

    fun close() {
        stopScan(); disconnect(); cleanup()
    }

    private val gattCallback = object : BluetoothGattCallback() {

        override fun onConnectionStateChange(g: BluetoothGatt, status: Int, newState: Int) {
            when (newState) {
                BluetoothProfile.STATE_CONNECTED -> {
                    listener?.onLog("Connected -> refresh cache + MTU")
                    // Clear Android's cached GATT database for this device. After many
                    // re-flashes the cached characteristic handles can be stale, so writes
                    // land on the wrong handle and never reach the firmware (notify may still
                    // work if its handle happens to match). Refresh forces a fresh discovery.
                    refreshDeviceCache(g)
                    main.post { listener?.onConnectionStateChanged(true, safeName(g.device)) }
                    main.postDelayed({ g.requestMtu(247) }, 200)
                }
                BluetoothProfile.STATE_DISCONNECTED -> {
                    listener?.onLog("Disconnected (status=$status)")
                    main.post { listener?.onConnectionStateChanged(false, null) }
                    cleanup()
                }
            }
        }

        override fun onMtuChanged(g: BluetoothGatt, mtu: Int, status: Int) {
            this@BleManager.mtu = mtu
            listener?.onLog("MTU=$mtu -> discovering services")
            g.discoverServices()
        }

        override fun onServicesDiscovered(g: BluetoothGatt, status: Int) {
            if (status != BluetoothGatt.GATT_SUCCESS) {
                listener?.onLog("Service discovery failed: $status"); return
            }
            // 1st choice: NUS TX
            var target: BluetoothGattCharacteristic? =
                g.getService(NUS_SERVICE)?.getCharacteristic(NUS_TX)

            // 2nd choice: first Notify/Indicate-capable characteristic
            if (target == null) {
                outer@ for (svc in g.services) {
                    for (ch in svc.characteristics) {
                        val p = ch.properties
                        if (p and BluetoothGattCharacteristic.PROPERTY_NOTIFY != 0 ||
                            p and BluetoothGattCharacteristic.PROPERTY_INDICATE != 0
                        ) {
                            target = ch; break@outer
                        }
                    }
                }
            }

            // Command (write) characteristic: NUS RX, or fall back to the first
            // write-capable characteristic if the exact UUID isn't found.
            rxChar = g.getService(NUS_SERVICE)?.getCharacteristic(NUS_RX)
            if (rxChar == null) {
                outer2@ for (svc in g.services) {
                    for (ch in svc.characteristics) {
                        val p = ch.properties
                        if (p and BluetoothGattCharacteristic.PROPERTY_WRITE != 0 ||
                            p and BluetoothGattCharacteristic.PROPERTY_WRITE_NO_RESPONSE != 0
                        ) { rxChar = ch; break@outer2 }
                    }
                }
            }
            listener?.onLog(
                if (rxChar != null) "RX (command) ready: ${rxChar!!.uuid}"
                else "No writable characteristic - commands unavailable"
            )

            if (target == null) {
                listener?.onLog("No notify-capable characteristic found")
                return
            }
            enableNotify(g, target)
            val uuid = target.uuid.toString()
            main.post { listener?.onNotifyReady(uuid) }
        }

        // API <= 32 (deprecated, but still called on older devices)
        @Deprecated("Deprecated in API 33")
        override fun onCharacteristicChanged(g: BluetoothGatt, ch: BluetoothGattCharacteristic) {
            @Suppress("DEPRECATION")
            val v = ch.value ?: return
            listener?.onDataReceived(v)
        }

        // API 33+
        override fun onCharacteristicChanged(
            g: BluetoothGatt, ch: BluetoothGattCharacteristic, value: ByteArray
        ) {
            listener?.onDataReceived(value)
        }

        override fun onCharacteristicWrite(
            g: BluetoothGatt, ch: BluetoothGattCharacteristic, status: Int
        ) {
            listener?.onLog("onCharWrite ${ch.uuid} status=$status")
            onWriteComplete()
        }
    }

    private fun enableNotify(g: BluetoothGatt, ch: BluetoothGattCharacteristic) {
        g.setCharacteristicNotification(ch, true)
        val cccd = ch.getDescriptor(CCCD)
        if (cccd == null) {
            listener?.onLog("No CCCD - cannot enable notify (${ch.uuid})")
            return
        }
        val enable = if (ch.properties and BluetoothGattCharacteristic.PROPERTY_NOTIFY != 0)
            BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE
        else
            BluetoothGattDescriptor.ENABLE_INDICATION_VALUE

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            g.writeDescriptor(cccd, enable)
        } else {
            @Suppress("DEPRECATION")
            run { cccd.value = enable; g.writeDescriptor(cccd) }
        }
        listener?.onLog("Notify enabled: ${ch.uuid}")
    }

    /** Force-clear Android's cached GATT services for this device (hidden API). */
    private fun refreshDeviceCache(g: BluetoothGatt) {
        try {
            val ok = g.javaClass.getMethod("refresh").invoke(g) as? Boolean
            listener?.onLog("gatt cache refresh: $ok")
        } catch (e: Exception) {
            listener?.onLog("gatt refresh unavailable: ${e.message}")
        }
    }

    private fun safeName(device: BluetoothDevice): String? =
        try { device.name } catch (_: SecurityException) { null }
}

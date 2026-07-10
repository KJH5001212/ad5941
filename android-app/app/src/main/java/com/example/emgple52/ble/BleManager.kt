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

    /**
     * Send a command to the device (NUS RX). Chunked to the negotiated MTU,
     * WRITE_TYPE_NO_RESPONSE (pipelined, no ACK wait). Returns false if not ready.
     */
    fun send(data: ByteArray): Boolean {
        val g = gatt ?: return false
        val ch = rxChar ?: return false
        val maxChunk = (mtu - 3).coerceAtLeast(20)
        var off = 0
        var ok = true
        while (off < data.size) {
            val n = minOf(maxChunk, data.size - off)
            ok = writeChunk(g, ch, data.copyOfRange(off, off + n)) && ok
            off += n
        }
        return ok
    }

    private fun writeChunk(
        g: BluetoothGatt, ch: BluetoothGattCharacteristic, chunk: ByteArray
    ): Boolean {
        return try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                g.writeCharacteristic(
                    ch, chunk, BluetoothGattCharacteristic.WRITE_TYPE_NO_RESPONSE
                ) == BluetoothStatusCodes.SUCCESS
            } else {
                @Suppress("DEPRECATION")
                run {
                    ch.writeType = BluetoothGattCharacteristic.WRITE_TYPE_NO_RESPONSE
                    ch.value = chunk
                    g.writeCharacteristic(ch)
                }
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
    }

    fun close() {
        stopScan(); disconnect(); cleanup()
    }

    private val gattCallback = object : BluetoothGattCallback() {

        override fun onConnectionStateChange(g: BluetoothGatt, status: Int, newState: Int) {
            when (newState) {
                BluetoothProfile.STATE_CONNECTED -> {
                    listener?.onLog("Connected -> requesting MTU")
                    main.post { listener?.onConnectionStateChanged(true, safeName(g.device)) }
                    g.requestMtu(247) // larger MTU = more samples per notification
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

            // Command (write) characteristic: NUS RX. Enables app -> device commands.
            rxChar = g.getService(NUS_SERVICE)?.getCharacteristic(NUS_RX)
            if (rxChar == null) {
                listener?.onLog("No NUS RX (6E400002) - commands unavailable")
            } else {
                listener?.onLog("RX (command) characteristic ready")
            }

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

    private fun safeName(device: BluetoothDevice): String? =
        try { device.name } catch (_: SecurityException) { null }
}

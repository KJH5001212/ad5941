# AD5941 Potentiostat

nRF52832 + AD5941 3-electrode chronoamperometry potentiostat — firmware and Android control app.

Applies a fixed **+0.5 V** bias to the working electrode and measures **nA-level** current, streaming it over BLE to a phone app that drives run parameters and plots current vs time.

## `firmware/` — nRF52832 (Zephyr / NCS v3.4)

Drives the AD5941 analog front-end (potentiostat + LPTIA + ADC) over SPI.

- **Chronoamperometry** at fixed +0.5 V, **autorange** current (512 kΩ … 110 Ω, ~1 nA to µA).
- **Run modes**: continuous / timed (N s) / cycle (measure `on` s / rest `off` s, repeat).
- **BLE (Nordic UART Service)**: JSON commands (`start`/`stop`/`config`/`ack`/`status`); streams
  `{"d":[[seq,t_ms,current_nA,range],...]}`.
- **Lossless offline buffering**: samples are kept in a ring buffer with sequence numbers; the app
  ACKs received seq, and on reconnect the device re-transmits everything unacked — nothing is lost.
- **Power-aware BLE**: fast connection interval while measuring, slow (1.5 s) at rest (iOS-compatible,
  5 s supervision timeout).
- Vendored ADI AD5940 driver library under `src/ad5940lib/` (do not edit).

Build: NCS v3.4, `west build -b nrf52dk/nrf52832 firmware -d firmware/build`.
Flash (J-Link, low speed for signal integrity): `firmware/flash.jlink`.

## `android-app/` — Kotlin / Android

Scans and connects to the `ad5941` peripheral, sends run parameters, and visualizes results.

- Parameter form: potential (fixed 0.5 V), autorange, sample rate, run mode + cycle timing.
- Live **current-vs-time** graph, run/buffer/sync status, CSV recording + export to Downloads.
- Sends periodic `ack` so the device frees its lossless buffer; de-duplicates re-transmits by seq.

Build: Android Studio, or `cd android-app && ./gradlew :app:assembleDebug`.

## Status

Firmware and app build clean; AD5941 ADIID verified on hardware (0x4144). End-to-end BLE streaming
validation and dummy-cell current verification in progress.

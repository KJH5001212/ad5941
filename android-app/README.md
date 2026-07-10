# EMG PLE-52 (Android)

Real-time EMG acquisition app: ADS1292 -> PLE-52 (BLE) -> Android (Galaxy).
Receives the BLE stream, applies filtering, displays the filtered waveform, and
exports CSV.

## Minimal UI

- **Scan/Connect** - scan for BLE devices and connect to the selected one.
- **Export** - copy the current recording to the public **Downloads** folder and
  open a share sheet. The CSV contains `index, time_s, raw, filtered` for the
  active channel.
- **Filtered waveform** - the filtered active channel (CH_A), shown full screen.

Recording starts automatically on connect and stops on disconnect (auto-saved to
Downloads). Filters are always on. The screen is locked to landscape.

## Device data format (reverse-engineered)

- The stream is a repeating **37-byte block**.
- Block = **5 samples + 1 counter byte + "ENDSTA" (45 4E 44 53 54 41)**. The
  counter increments by one per block.
- Sample = **[CH_A 3B][CH_B 3B]**, 24-bit big-endian signed. CH_A is the active
  channel; CH_B is pinned at 0x7FFFFF (unused).

The parser runs in delimiter-sync mode: it splits the stream on "ENDSTA" and
parses 6-byte samples between delimiters, so it stays aligned regardless of how
BLE packets are chunked. To target a different device, edit the defaults in
`FrameParser.kt`.

## Theme + metrics (this build)

Dark "EMG Monitor" dashboard, single channel, portrait:
device status card, Live EMG card (uV grid), Signal Metrics (RMS, iEMG, median
frequency, SNR), RMS trend, recording bar, Scan/Connect + Export.

Metrics (computed on the filtered active channel CH_A):
- RMS (uV), iEMG (uV-s), median frequency (Hz, 512-pt FFT).
- SNR (dB): RMS-envelope + baseline-threshold segmentation, RMS-ratio definition.
  A ~100 ms sliding RMS envelope is computed; over the last ~10 s the noise floor
  is the mean+SD of the lowest 20% of the envelope; threshold = noiseMean + 3*SD;
  SNR = 20*log10(mean active RMS / mean rest RMS). The live RMS readout turns
  green during active bursts. Tune k / noise percentile in SnrEngine.kt.

## Signal / filtering

Measured EMG energy spans ~20-150 Hz with strong 60 Hz mains interference, so the
default filters are:

- High-pass 20 Hz
- Low-pass 150 Hz
- Band-stop 59-61 Hz (center 60 Hz, Q = 30)

Cutoffs are constants in `FilterChain.kt`. A 0.5-20 Hz pass band would remove most
of the EMG, so use the wider band for EMG work; the 60 Hz notch is only meaningful
when the low-pass cutoff is above 60 Hz.

## Build

This archive is an Android Studio project (source), not a prebuilt APK.

1. Open the `EmgPle52` folder in Android Studio.
2. Gradle sync (AGP 8.7.3 / Kotlin 2.0.21 / Gradle 8.9).
3. Connect a Galaxy device over USB and Run, or Build > Build APK(s).

minSdk 26 (Android 8.0), targetSdk 35.

## Project layout

```
app/src/main/java/com/example/emgple52/
  MainActivity.kt           // UI, permissions, auto-record, export
  ble/BleManager.kt         // BLE scan/connect, auto characteristic discovery, notify
  data/FrameParser.kt       // bytes -> channel samples (delimiter-sync parser)
  data/Recorder.kt          // CSV recording
  data/DownloadExporter.kt  // copy CSV to the public Downloads folder
  dsp/Biquad.kt             // RBJ biquad IIR
  dsp/FilterChain.kt        // HPF/Notch/LPF chain
  ui/WaveformView.kt        // real-time waveform view
```

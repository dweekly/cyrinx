# Cyrinx Android HIL

This is an Android HIL companion app for `cyrinx` with:

- Kotlin transport core compatible with `cyrinx` framing/CRC/fragment/ACK behavior
- Acoustic PHY bridge (preamble + D-CSS header/body)
- Duplex audio backend (`AudioRecord` + `AudioTrack`)
- ADB-automatable controls for fully scripted test runs

The portable C implementation is the canonical wideband bulk-PHY decoder.
Android currently uses the C decoder on the host after tokenized PCM capture;
it does not yet bind that decoder through JNI. `BulkDemod.kt` is retained as a
legacy on-device control decoder for its one fixed profile, not as a co-equal
source of PHY behavior.

## Build + Install

```bash
cd /Users/dew/dev/cyrinx/Apps/HIL/android
./gradlew assembleDebug
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

## ADB Automation Commands

The activity supports intent commands via `--es cmd ...`.

```bash
adb shell am start -n com.dweekly.cyrinxhil/com.dweekly.cyrinxhil.MainActivity --es cmd start --es role slave
adb shell am start -n com.dweekly.cyrinxhil/com.dweekly.cyrinxhil.MainActivity --es cmd stop
adb shell am start -n com.dweekly.cyrinxhil/com.dweekly.cyrinxhil.MainActivity --es cmd send_be
adb shell am start -n com.dweekly.cyrinxhil/com.dweekly.cyrinxhil.MainActivity --es cmd send_rel
adb shell am start -n com.dweekly.cyrinxhil/com.dweekly.cyrinxhil.MainActivity --es cmd receive
adb shell am start -n com.dweekly.cyrinxhil/com.dweekly.cyrinxhil.MainActivity --es cmd refresh
adb shell am start -n com.dweekly.cyrinxhil/com.dweekly.cyrinxhil.MainActivity --es cmd beacon
```

### Scenario Mode

```bash
adb shell am start -n com.dweekly.cyrinxhil/com.dweekly.cyrinxhil.MainActivity \
  --es cmd scenario \
  --es role slave \
  --ei sample_rate_hz 48000 \
  --ei band_start_hz 9000 \
  --ei band_end_hz 14000 \
  --ei dcss_symbol_samples 1024 \
  --ef sync_threshold 0.30 \
  --ef tx_gain 0.05 \
  --ei duration_sec 30 \
  --ei send_interval_ms 600
```

### Built-in PHY self-test

```bash
adb shell am start -n com.dweekly.cyrinxhil/com.dweekly.cyrinxhil.MainActivity \
  --es cmd self_test \
  --ei band_start_hz 9000 \
  --ei band_end_hz 14000
```

### Offline decode fixture

Generate a Swift PHY waveform and decode it on Android without speakers/mics:

```bash
swift run cyrinx-example-android-hil \
  --fixture-wave /tmp/cyrinx_wave_f32le.bin \
  --fixture-payload-text "fixture-mac-to-android" \
  --sample-rate 48000 --band-start 9000 --band-end 14000 \
  --dcss-symbol-samples 1024 --sync-threshold 0.30

adb push /tmp/cyrinx_wave_f32le.bin /data/local/tmp/cyrinx_wave_f32le.bin
adb shell run-as com.dweekly.cyrinxhil cp /data/local/tmp/cyrinx_wave_f32le.bin files/cyrinx_wave_f32le.bin
adb shell am start -n com.dweekly.cyrinxhil/com.dweekly.cyrinxhil.MainActivity \
  --es cmd decode_file \
  --es wave_path /data/user/0/com.dweekly.cyrinxhil/files/cyrinx_wave_f32le.bin \
  --ei sample_rate_hz 48000 --ei band_start_hz 9000 --ei band_end_hz 14000 \
  --ei dcss_symbol_samples 1024 --ef sync_threshold 0.30
```

Check logs:

```bash
adb logcat -d -s CyrinxHILAndroid
```

### Raw capture and same-device calibration

`rec_pcm` supports `unprocessed`, `camcorder`, `mic`, and `voice_recognition` sources. A request for
two logical channels does not prove that two microphone capsules are exposed. While recording, the
app logs the routed device, active microphones, direct/processed channel mappings, positions, and
declared frequency-response endpoints. Raw captures must still be checked for exact duplication and
effective rank.

`playrec_pcm` records continuously while playing a preloaded PCM16 file. It exists for
one-speaker-at-a-time, same-device characterization; the transmit waveform must contain its own
alignment markers. The command reports Android monotonic audio timestamps as provenance, but those
timestamps do not replace received-marker correlation and do not by themselves measure propagation
delay. This primitive is not yet qualified for unsupervised physical use: the host wrapper lacks a
fail-closed phone-volume transaction, playback peak ceiling, and realized playback-route check.

```bash
adb push /tmp/calibration-stereo-s16le.pcm /data/local/tmp/calibration.pcm
adb shell am start -n com.dweekly.cyrinxhil/.MainActivity \
  --es cmd playrec_pcm \
  --es path /data/local/tmp/calibration.pcm \
  --es out_name calibration-capture.pcm \
  --es request_id calibration-left-001 \
  --ei sample_rate_hz 48000 --ei input_channels 2 --ei output_channels 2 \
  --ei pre_roll_ms 500 --ei post_roll_ms 750 --es source camcorder
```

This command does not change media volume. The caller must set a reviewed level explicitly and
restore it afterward. To characterize two logical speakers, run separate files with one stereo
column identically zero in each acquisition; never infer physical speaker identity from the channel
label alone. Use a unique shell-safe `request_id` for every command and retain only lines carrying
that token; do not clear the device-wide log buffer.

### Bulk-PHY benchmark decode

`bulk_decode` reports headline goodput only when the caller supplies the first scheduled frame's
chirp position in capture-sample coordinates. The origin must come from independent timing or a
separate synchronization marker; deriving it from decoded payload identity would let reordered
frames receive credit. Without `schedule_origin_sample`, the app emits `goodput=REFUSED` and only
labels content-based attribution as diagnostic.

For the five-frame, 48 kHz flagship schedule, pass the exact gap and trailing pad as samples:

```bash
adb shell am start -n com.dweekly.cyrinxhil/.MainActivity \
  --es cmd bulk_decode \
  --es request_id baseline-decode-001 \
  --es path /data/user/0/com.dweekly.cyrinxhil/files/final_m2a.pcm \
  --ei channels 2 --ef f_lo 1100 --ef f_hi 23000 \
  --ei n_sym 64 --ei n_payloads 5 --ei payload_seed_base 1000 \
  --ei schedule_origin_sample 33600 --ei slot_tolerance_samples 480 \
  --ei gap_samples 12000 --ei trailing_pad_samples 16000
```

The sample origin above is illustrative, not a reusable calibration. The primary denominator is
always all scheduled frame slots plus all four gaps; missed endpoint frames cannot shorten it.
`gross_goodput` additionally includes `trailing_pad_samples`.

`bulk_decode` is the fixed CP768/p8/16-QAM/r3/4 Kotlin control decoder. It does
not implement CP96, pilot spacing 16 or 64, 64-QAM, r2/3, pilot-local LLR
reliability, automatic microphone selection, or two-microphone MRC. Use it to
reproduce the legacy control profile only.

"Cyrinx 2.0" names the canonical C receiver and fast-profile generation, not
only the earlier Moto r5/6 profile. On the Pixel 7a, the CP96/p16/64-QAM/r2/3
profile measured 65.875 kbps while retaining the accepted benchmark's
12,000-sample (0.25 s) inter-frame gap, versus 36.571 kbps for the accepted
benchmark. It recovered 4,215/4,280 blocks and won all eight order-balanced
pairs, but failed the predeclared strict reliability gate against the more
conservative baseline.

A separately labeled CP96/p64/64-QAM/r2/3, 96-symbol zero-gap research class
reached 69.652 kbps mean (65.731–72.641 kbps across runs), 1.9045x the
36.571 kbps result. It recovered 6,129/6,760 blocks (90.666%), won 8/8 pairs,
and also failed the strict reliability gate. Do not compare that number
directly with the gap-preserving flagship or describe it as almost tripled.

For both new measurements, the Android app captured two-channel Pixel audio;
a frozen build of the portable C library decoded the capture on the host. The
Kotlin decoder did not produce either result on-device. Integrated ultrasonic
operation and a lower-rate, pleasant-audible mode remain future work.

## macOS Peer CLI

Use the paired CLI endpoint from this repo:

```bash
swift run cyrinx-example-android-hil --role master --duration 20 --send-interval-ms 500 --reliable-every 4 --band-start 9000 --band-end 14000

# Tuned robust-mode run:
swift run cyrinx-example-android-hil --role master --duration 20 --band-start 9000 --band-end 14000 --dcss-symbol-samples 1024 --sync-threshold 0.30
```

Receiver-only mode:

```bash
swift run cyrinx-example-android-hil --role master --duration 20 --rx-only --band-start 9000 --band-end 14000
```

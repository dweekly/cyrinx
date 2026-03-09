# Cyrinx Android HIL

This is an Android HIL companion app for `cyrinx` with:

- Kotlin transport core compatible with `cyrinx` framing/CRC/fragment/ACK behavior
- Acoustic PHY bridge (preamble + D-CSS header/body)
- Duplex audio backend (`AudioRecord` + `AudioTrack`)
- ADB-automatable controls for fully scripted test runs

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

Check logs:

```bash
adb logcat -d -s CyrinxHILAndroid
```

## macOS Peer CLI

Use the paired CLI endpoint from this repo:

```bash
swift run cyrinx-example-android-hil --role master --duration 20 --send-interval-ms 500 --reliable-every 4 --band-start 9000 --band-end 14000
```

Receiver-only mode:

```bash
swift run cyrinx-example-android-hil --role master --duration 20 --rx-only --band-start 9000 --band-end 14000
```

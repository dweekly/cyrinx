# Cyrinx Hardware-in-the-Loop Apps

This folder contains a paired macOS + iOS SwiftUI test harness for live MacBook Pro <-> iPhone trials.

## Generate the Xcode project

```bash
./scripts/hil-generate.sh
```

This creates `/Users/dew/dev/cyrinx/Apps/HIL/CyrinxHIL.xcodeproj` from `project.yml`.

## Run workflow

1. Open `CyrinxHIL.xcodeproj` in Xcode.
2. Build and run `CyrinxHILMac` on the MacBook Pro.
3. Build and run `CyrinxHILiOS` on a physical iPhone (not simulator).
4. On both apps pick opposite roles (`Master` on one side, `Slave` on the other).
5. Press `Start`, then `Send Probe` repeatedly.
6. Use `Receive Once` and `Refresh Diagnostics` to validate transport and audio-path activity.

## What this validates now

- Session lifecycle on both devices.
- `transportBackend = .appleAudioScaffold` startup path.
- Outbound frame dispatch into the audio scaffold (`txFrameCount` / `txByteCount`).
- Input callback activity (`rxCallbackCount`) and event stream plumbing.

## Current limits

- No full OFDM/D-CSS live demodulation yet.
- The macOS scaffold emits deterministic ultrasonic tone bursts from outgoing frames.
- The iOS scaffold uses `AVAudioSession + RemoteIO` and records callback activity, but does not yet decode packets into `receive()`.

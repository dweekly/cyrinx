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
5. Select `Sample Rate` (`48 kHz` or `96 kHz`) on both sides.
6. Press `Start`, then `Send Probe (BE)` repeatedly for non-blocking link probing.
7. Use `Send Probe (Reliable)` only when you explicitly want ACK/timeout behavior.
8. Use `Receive Once`, `Refresh Diagnostics`, and `Probe Local Audio` to validate transport and negotiated route rates.
9. Check diagnostics for `coreRx` increasing above `0` on both sides to confirm decoded inbound frames.

## 96 kHz Smoke Workflow

```bash
./scripts/hil-96k-smoke.sh --open
```

This command:
- probes default Mac input/output sample-rate capability (`artifacts/bench/audio-rates-mac.json`)
- regenerates the HIL Xcode project
- builds both `CyrinxHILMac` and `CyrinxHILiOS`
- prints manual on-device validation steps for 96 kHz mode.

## What this validates now

- Session lifecycle on both devices.
- `transportBackend = .appleAudioScaffold` startup path.
- Outbound frame dispatch into acoustic PHY waveform generation (`txFrameCount` / `txByteCount`).
- Input callback activity and RX demodulation path into `receive()` (`rxCallbackCount`).
- Negotiated sample-rate diagnostics (`configuredHz`, `inHz`, `outHz`) at runtime.
- macOS input-rate mismatch handling: inbound audio is resampled into modem rate when route input is not `configuredHz`.
- Cross-platform compatibility test profile band: HIL currently uses `18.5-21.0 kHz` to stay below 44.1 kHz Nyquist limits.

## Current limits

- Live route sample rates are platform/route dependent: `configuredHz=96000` may negotiate to lower observed `inHz/outHz` on some routes.
- Ultrasonic frequency response still varies by hardware and placement; use repeated runs and compare diagnostics + delivery metrics.

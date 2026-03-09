# Cyrinx Samples

These samples are intended as copyable invocation patterns for agents and developers.

## 1) Basic Loopback

Demonstrates:
- create two sessions
- link in-memory transport
- send one stream message with `.fin`
- receive metadata (`streamID`, `priority`, `flags`)

```bash
swift run cyrinx-example-loopback
```

## 2) Multiplexed Streams

Demonstrates:
- send interleaved payloads on multiple `streamID`s
- use different priorities
- consume and inspect stream metadata

```bash
swift run cyrinx-example-multiplex
```

## 3) Large Payload / Fragmentation

Demonstrates:
- send 4096-byte payload
- verify transport-level fragmentation/reassembly
- validate payload integrity with checksum

```bash
swift run cyrinx-example-large-payload
```

## 4) Deterministic Simulation Benchmark

Demonstrates:
- deterministic channel profile replay
- machine-readable benchmark JSON output
- profile-driven ARC behavior observation

```bash
swift run cyrinx-sim-bench --profile quiet --out artifacts/bench/sim-quiet.json
```

## 5) macOS <-> Android HIL CLI

Demonstrates:
- live macOS acoustic endpoint for Android HIL app interop
- periodic best-effort + reliable probe traffic
- streaming diagnostics and inbound payload logs

```bash
swift run cyrinx-example-android-hil --role master --duration 30 --send-interval-ms 700 --reliable-every 4
# robust tuning knobs:
swift run cyrinx-example-android-hil --role master --duration 30 --dcss-symbol-samples 1024 --sync-threshold 0.30
# offline fixture waveform for Android decode_file:
swift run cyrinx-example-android-hil --fixture-wave /tmp/cyrinx_wave_f32le.bin --fixture-payload-text "fixture-mac-to-android"
```

## Notes

- These are transport/simulation examples using `CyrinxSession.linkInMemory`.
- They do not require real audio hardware.
- For live device testing, use the paired macOS+iOS HIL scaffold in `/Users/dew/dev/cyrinx/Apps/HIL/README.md`.

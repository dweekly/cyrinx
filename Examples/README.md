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

## Notes

- These are transport/simulation examples using `CyrinxSession.linkInMemory`.
- They do not require real audio hardware.
- For live device testing, use the paired macOS+iOS HIL scaffold in `/Users/dew/dev/cyrinx/Apps/HIL/README.md`.

# cyrinx

`cyrinx` is an adaptive ultrasonic transport prototype optimized for close-range desktop-to-phone links (1-2 ft) in the 18.5-23.5 kHz band.

This repository currently provides:

- A C core (`CCyrinx`) with a stable C ABI
- A native Swift wrapper (`Cyrinx`)
- Frame codec with bit-packed headers, CRC16/CRC32C, fragmentation/reassembly
- Half-duplex ping-pong MAC with ACK and selective retransmission policy hooks
- Adaptive Rate Control (ARC) gear state machine
- Stream-multiplexed transport API (`stream_id`, priority, `FIN`/`RST` flags)
- In-memory linked transport for deterministic tests without audio hardware
- PHY utility module with Zadoff-Chu generation, CFO estimation, and dynamic CP selection
- Apple audio backend scaffold (`RemoteIO` on iOS, `AVAudioEngine` on macOS)
- Deterministic OFDM/D-CSS PHY stub interfaces, including stateful sequential chunk APIs
- Golden-vector and chunked-sequence PHY tests for deterministic behavior

## Implemented Protocol Model

- `G1 Discovery`: ZC preamble + sync/CFO control path
- `G2 Robust`: D-CSS fallback mode
- `G3 Turbo`: OFDM mode (`QPSK`, `16QAM`, `64QAM experimental`)
- OFDM constants:
  - FFT size: `1024`
  - Subcarrier spacing: `46.875 Hz`
  - Active carriers: `106`
  - Default CP: `96 samples` (~2 ms @ 48 kHz)

## Stream Model

- `stream_id = 0` is reserved for control frames.
- Applications send on stream IDs `1...4095`.
- Each received payload includes stream metadata:
  - `stream_id`
  - `priority` (`0...3`)
  - `flags` (`FIN`, `RST`)

## Apple-Targeted Configuration Hooks

`cyrinx_config_t` includes policy/config controls to support the Apple-specific deployment profile:

- `sample_rate_hz` (`48k` default)
- `tx_gain_cap` (`0.70` default)
- `spectral_leakage_limit_dbfs`
- `ofdm_cp_samples_default` / `ofdm_cp_samples_min`
- `enable_sensor_assisted_arc`
- `enable_dynamic_cp`
- `enable_sfbc_static_mode`

## Build and Test

```bash
swift test
```

## Sample Programs

Sample runnable programs live in `/Users/dew/dev/cyrinx/Examples/README.md`.

```bash
swift run cyrinx-example-loopback
swift run cyrinx-example-multiplex
swift run cyrinx-example-large-payload
swift run cyrinx-sim-bench --profile quiet --out artifacts/bench/sim-quiet.json
./scripts/hil-generate.sh --open
```

## Linting and Formatting

```bash
./scripts/format.sh
./scripts/format-check.sh
./scripts/lint.sh
./scripts/check.sh
```

- `format.sh`: applies Swift and C formatting in place.
- `format-check.sh`: fails if any Swift/C file is not formatted.
- `lint.sh`: runs `swiftlint` (strict mode) and `shellcheck` for scripts.
- `check.sh`: full gate (`format-check` + `lint` + `swift test`).
- `bench-sim.sh`: deterministic simulation benchmark JSON output under `artifacts/bench/`.
- `hil-generate.sh`: generates paired macOS+iOS hardware-in-the-loop app project under `Apps/HIL/`.

## Current Scope and Next Steps

This implementation is protocol-complete for simulation/in-memory transport, but not yet a full real-time acoustic modem.

Planned next layers:

1. Replace stub PHY blocks with vDSP-backed OFDM/D-CSS implementations
2. Complete live frame demodulation path into `cyrinx_ingest_frame`
3. Hardware-in-the-loop channel calibration for MacBook Pro <-> iPhone Pro Max
4. Security envelope integration in host app (Encrypt-then-MAC payloads)
5. Optional C++/Rust backend behind the same public C ABI and Swift API

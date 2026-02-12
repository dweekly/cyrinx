# cyrinx

`cyrinx` is an adaptive ultrasonic transport prototype optimized for close-range desktop-to-phone links (1-2 ft) in the 18.5-23.5 kHz band.

This repository currently provides:

- A C core (`CCyrinx`) with a stable C ABI
- A native Swift wrapper (`Cyrinx`)
- Frame codec with bit-packed headers, CRC16/CRC32C, fragmentation/reassembly
- Half-duplex ping-pong MAC with ACK and selective retransmission policy hooks
- Adaptive Rate Control (ARC) gear state machine
- In-memory linked transport for deterministic tests without audio hardware
- PHY utility module with Zadoff-Chu generation, CFO estimation, and dynamic CP selection

## Implemented Protocol Model

- `G1 Discovery`: ZC preamble + sync/CFO control path
- `G2 Robust`: D-CSS fallback mode
- `G3 Turbo`: OFDM mode (`QPSK`, `16QAM`, `64QAM experimental`)
- OFDM constants:
  - FFT size: `1024`
  - Subcarrier spacing: `46.875 Hz`
  - Active carriers: `106`
  - Default CP: `96 samples` (~2 ms @ 48 kHz)

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

## Current Scope and Next Steps

This implementation is protocol-complete for simulation/in-memory transport, but not yet a full real-time acoustic modem.

Planned next layers:

1. Real audio I/O integration (RemoteIO / AVAudioSession measurement path)
2. vDSP-backed OFDM/D-CSS modulator and demodulator blocks
3. Hardware-in-the-loop channel calibration for MacBook Pro <-> iPhone Pro Max
4. Security envelope integration in host app (Encrypt-then-MAC payloads)
5. Optional C++/Rust backend behind the same public C ABI and Swift API

# Bulk-PHY golden vectors

Fresh as of 2026-06-10. The cross-implementation **contract** for the wideband
bulk PHY (docs/PUBLICATION.md PR 1.1). The Python modem
(`scratch/hw20k/modem.py`) is the semantic oracle; the portable-C core and the
Swift/Kotlin bindings must reproduce these stage outputs at the tolerance the
manifest records per artifact:

- **exact** — byte-for-byte. The deterministic integer pipeline: `DetRng`, CRC,
  conv-encode, puncture, the PRBS pads, the interleave permutation, the
  interleaved bits, and the final decoded payload. A different FFT is no excuse
  for a mismatch here.
- **float** — within `float_abs_tol` (1e-5) of the float32 reference. FFT/IFFT-
  derived values (QAM constellation points, the TX waveform). Backends differ by
  ~ULPs, so closeness is asserted, not equality.
- **input** — not asserted. The receive waveform `rx_wave` fed to the RX port;
  the RX-side contract is that decoding it reproduces `decoded_payload` (exact)
  and recovers all `decode_blocks_total` blocks. `rx_wave` is the TX `wave`
  through a fixed deterministic multipath channel (`rx_channel_taps` — echoes
  within the CP, no noise) so the receiver must actually estimate/equalize H.

## Layout

- `manifest.json` — `format`, `float_abs_tol`, and per-case `config` + `artifacts`
  (each with `stage`, `file`, `tolerance`, `dtype`, `shape`, `bytes`, `sha256`).
  The `config` also carries RX expectations (`rx_channel_taps`,
  `rx_pre_pad`/`rx_post_pad`, `decode_blocks_ok`/`_total`, `pad_fill_bits_len`).
- `<case>/<stage>.bin` — little-endian binary blobs. Bit arrays are one `uint8`
  per bit (0/1); `complex64` is interleaved float32 `(re, im)`; `float32` floats.
- `../../CGoldenVectors/golden_manifest.h` — a generated C-consumable copy of the
  manifest, so the portable-C port can locate/size artifacts without a JSON
  parser. The `CGoldenVectors` test-support target loads against it.

Cases use the real bulk-PHY parameters (NFFT 2048, CP 768) with the smallest
`n_sym` that still carries one 256-byte CRC block, so every code path is
exercised by a small frame:

- `qpsk_r12` — QPSK, rate 1/2.
- `qam16_r34` — 16-QAM, rate 3/4.
- `qpsk_r23` — QPSK, rate 2/3 — exercises the PRBS capacity-fill branch
  (non-empty `pad_fill_bits`; structurally ≤1 bit by design — the modem sizes
  the payload to fill capacity, so the fill only absorbs the rounding remainder).

## Regenerate / verify

```bash
# from repo root, using the project venv
.venv/bin/python scratch/hw20k/golden_vectors.py emit     # regenerate (.bin + golden_manifest.h)
.venv/bin/python scratch/hw20k/golden_vectors.py verify   # recompute -> match manifest
swift test --filter CyrinxGoldenVectorTests               # Swift + C loader integrity, RX/fill coverage
```

Regenerating should be a no-op (the vectors are deterministic). If `emit`
changes a hash, a TX pipeline stage changed — update the C/Swift/Kotlin ports to
match, and note the change in `docs/publication-journal.md`.

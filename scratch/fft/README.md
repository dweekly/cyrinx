# FFT / OFDM parity spikes

Fresh as of 2026-06-10. Standalone validators kept per the "ship the spike"
rule — small reproducible programs that pin down FFT and OFDM behavior across
implementations. They are **directly relevant to the portable-C bulk-PHY port**
(see [../../docs/PUBLICATION.md](../../docs/PUBLICATION.md) PR 1.1): the C core
will use its own FFT (e.g. KISS FFT) which must agree, within float tolerance,
with vDSP/Accelerate and with the numpy reference used by `scratch/hw20k`.

- `vdsp_dft_size8.swift` — minimal vDSP `DiscreteFourierTransform` forward DFT
  at N=8; the smallest reproducible vDSP sanity check.
- `FftComparison.swift` — hand-rolled Cooley-Tukey vs vDSP, compares outputs.
- `FftForwardParity.swift` — forward-transform parity (sign/scaling/bit-reversal
  conventions) between Cooley-Tukey and vDSP.
- `TestFFT.java` — bit-reversal + radix-2 FFT in Java, the Android/JVM-side
  reference for the same conventions (mirrors the Kotlin `DFFT` in `BulkDemod`).

Run (Swift): `swift <file>.swift`. Run (Java): `javac TestFFT.java && java TestFFT`.

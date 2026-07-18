# Spike 8b result: bracketing estimate does not clear the offline gate

Date: 2026-07-18  
Maturity: BENCH-SPIKE, offline only  
Claim class: `non-claimable-terminal-oracle`

## Result

The preregistered terminal-symbol oracle did **not** justify an OTA terminal-
training experiment. The best late-half-only intervention, direct complex
endpoint interpolation, recovered 441 of the start-only receiver's 938 invalid
blocks (47.03%), below the 50% proceed threshold. It simultaneously regressed
613 previously valid blocks, for a net loss of 172 blocks. Aggregate late-
quartile payload EVM worsened by 1.88 dB rather than improving by the alternate
3 dB threshold.

The full-frame version recovered 603/938 previously invalid blocks (64.29%),
but regressed 1,137 valid blocks and reduced strict verified recovery from
7,662 to 7,128 blocks. Its late-quartile EVM worsened by 2.28 dB and middle-
half EVM worsened by 1.13 dB. It therefore does not demonstrate that the
terminal estimate predicts the channel midpoint.

| Estimator / intervention | Verified blocks | Scheduled goodput | Recovered incumbent losses | Regressed incumbent wins | Late-quartile EVM change |
|---|---:|---:|---:|---:|---:|
| start-only | 7,662/8,600 | 66.102 kbps | — | — | reference |
| complex linear, late half only | 7,490/8,600 | 64.618 kbps | 441/938 (47.03%) | 613 | **-1.88 dB** |
| complex linear, full frame | 7,128/8,600 | 61.495 kbps | 603/938 (64.29%) | 1,137 | **-2.28 dB** |
| log-magnitude/unwrapped-phase, late half | 6,019/8,600 | 51.928 kbps | 243/938 (25.91%) | 1,886 | **-4.78 dB** |
| log-magnitude/unwrapped-phase, full frame | 4,170/8,600 | 35.976 kbps | 283/938 (30.17%) | 3,775 | **-4.15 dB** |
| significant taps, late half only | 76/8,600 | 0.656 kbps | 0/938 | 7,586 | **-8.26 dB** |
| significant taps, full frame | 0/8,600 | 0 | 0/938 | 7,662 | **-8.26 dB** |

Negative dB values mean worse EVM. The significant-tap result rejects only the
preregistered incomplete-band/CP-window projection; it is not evidence that
every CIR-domain estimator must fail.

## Corpus and provenance audit

The ledger-bound manifest SHA-256 is
`0f81dc4dce591c47e7fb1aeefeb635ac49de2ce01fbf0fd7805326446668af9e`.
It contains eight complete candidate runs and eight paired controls, all with
five 128-symbol zero-gap frames. The inventory verified 16 stereo capture
hashes, 16 transmit-waveform hashes, and 80 expected-payload hashes. Candidate
payload bytes deterministically regenerated every recorded frequency-domain
symbol and transmit frame. Float32 playback regeneration was bit-identical
except for one sample differing by `5.820766e-11`; the analyzer records that
roundoff instead of claiming universal byte identity.

The independent start-only Python implementation reproduced the source C
receiver's complete 8,600-position ordered block-validity map with zero
mismatches. This also reproduced the tracked 7,662/8,600 result and
66.102179286 kbps scheduled goodput.

## Oracle and leakage boundary

The terminal estimate uses the final eight known transmitted data symbols.
That is a legitimate offline bound because the expected payload files are
hash-bound to the campaign plan and regenerate the recorded transmit PCM. It
is not a realizable receiver: payload values unavailable to a peer were used
to estimate the endpoint and evaluate EVM.

Recorded chirp timing, known-sync fine timing, synchronization-only MRC noise
weights, MRC receiver choice, and per-symbol pilot common-phase/timing
corrections were fixed before forming the terminal estimate. Expected bytes
did not participate in those operations, estimator selection, or stopping.
Tests mutate expected payload bytes after front-end construction and verify
that the frozen front-end is unchanged.

Because the frame-wide interleaver distributes every codeword through all 128
symbols, block index is not chronological. “Late-half recovery” therefore
means blocks recovered when only symbols 64–127 receive the candidate
equalizer; it does not mean high-numbered CRC blocks.

## Exact accounting

Each candidate run schedules 2,201,600 payload bits over 1,424,320 samples at
48 kHz: 29.673333333 s. Canonical gross time adds 16,000 trailing samples:
1,440,320 samples or 30.006666667 s. Each retained acquisition contains
1,665,919 samples or 34.706645833 s; this is a separate capture-span diagnostic,
not the campaign gross denominator. Session goodput is unavailable because the
retained burst artifacts exclude discovery, sounding, buffering, and decode
wall time.

With payload fixed, appending one 2,144-sample terminal OFDM trainer per frame
would lower the error-free scheduled ceiling from 74.194563 to 73.640317 kbps.
Two appended trainers would lower it to 73.094290 kbps. Replacing one or two
data symbols instead would reduce capacity from 215 blocks/frame to 213 or 212
and yield respective ceilings of 73.504381 or 73.159290 kbps. These are
counterfactual geometry costs, not measured candidate goodput.

## Stop reason and next gate

No preregistered late-half estimator reached either offline threshold, and the
best full interpolation failed the midpoint-prediction check. The retained
data are adequate, so this is an empirical stop rather than a missing-evidence
stop. The generated OTA plan is marked `blocked-by-offline-oracle-gate`; no
playback should be launched from it.

A future revisit should first preregister a disjoint-symbol endpoint estimator
that preserves cross-microphone phase and demonstrates midpoint prediction on
synthetic time-varying channels and retained replay. Until then, shorter frames
or a frequency-staggered known-pilot lattice are better-supported follow-ups
than adding terminal trainers.

Machine-readable evidence:

- [`inventory.json`](inventory.json)
- [`results.json`](results.json)
- [`ota-acquisition-plan.json`](ota-acquisition-plan.json)
- [`PREREGISTRATION.md`](PREREGISTRATION.md)

# Replay simulator: first plan

> **Provenance.** Written by an AI agent on 2026-11-11 at the owner's request ("make
> sure we have written it up in the roadmap and perhaps have taken a first swing at a
> plan"). Nothing in this plan is built. It narrows the staged plan in the
> [simulator exploration](acoustic-simulation-exploration-2026-11-10.md) to a first
> deliverable, using what the 2026-11-10 desk sessions retained. Thresholds and
> scope here are proposed by an agent, not yet confirmed.

Stage: prototype (a `scratch/` tool, maturity BENCH-SPIKE, supports no product claim).

## In plain English

Every question this project asked on 2026-11-10 (which microphone, which speaker, how
long a guard, how dense a signal) was answered by playing sound across a desk and
counting what decoded. That is slow, it needs the hardware set up and the room quiet,
and it only ever tests the one arrangement on the desk.

A replay simulator plays the modem's signal through *recordings of how a real room
answered*, on a computer. It can try a thousand settings overnight. It cannot invent
a room nobody measured, and an earlier experiment showed that a made-up room gives
confidently wrong answers. So the plan is to replay only rooms this project has
measured, and to trust the simulator on a kind of question only after it has
reproduced the real results of that kind.

Until 2026-11-10 this could not start, because the room measurements had been lost.
That evening's sessions kept everything: sweeps in both directions, both phone
microphones, each laptop speaker separately, and thirty sets of recordings of real
transmissions whose decode results are known. Those recordings are the answer key the
simulator has to match.

No simulator exists yet. This document says what to build first and how to tell
whether it can be trusted.

## Why

The owner's questions are now arrangement questions, and each needs many trials:

- which phone microphones to use, which depends on how the phone is lying or held;
- which Mac speaker, or what delay or phase between the two;
- whether two independent streams are worth building;
- how short a guard each of these allows.

Each live trial costs about half a minute and one physical arrangement. A replay
costs under a second of processor time per frame (exploration, "Scale and
reproducibility") and can run on trogdor. A different tool that screened candidates
equally well would serve; what matters is that candidates are screened on measured
channels before hardware time is spent, and that a simulated number is never read as
a measurement.

## What the 2026-11-10 sessions made available

All in `~/dev/cyrinx-artifacts/garage/`, mirrored to `trogdor:cyrinx-artifacts/garage/`.
One device pair (MacBook Pro M4, Pixel 7a), one desk position about 0.95 m apart,
phone lying face up, nothing moving.

| Retained | Files | What it gives the simulator |
|---|---|---|
| Swept-sine captures, both directions, with room tone | `crosscal-desk-*.npz` (four sessions) | Impulse response per direction; Mac to phone on both phone microphones; noise at session level |
| Each Mac speaker to each phone microphone, one recording | `speakermatrix-desk-evening-*.npz` | The four-path matrix, phase-coherent, with a self-check |
| Real transmissions with known results | `ladder-desk-*.npz` / `.jsonl` (thirty ladder recordings, each holding several cells) | Answer key: blocks decoded per cell, per microphone, per speaker arrangement |
| Clock offset | in the ladder records | About 27.7 ppm, sign by direction |
| Two-way ranging | `rangefind-desk-evening-*` | Path length, to check the impulse response's first arrival |

This satisfies the exploration's stage 1 ("Retain Our Rooms") for this one cell. The
two anchors that stage named, Garage One-Foot and Long-Guard Desk, are still not
retained: those captures are gone. The plan below replaces them with anchors from the
desk cell and says so, rather than waiting for a garage session.

## The demo

One command replays a named ladder cell through a named measured channel and prints
simulated blocks beside the blocks hardware decoded for the same cell:

```
.venv/bin/python scratch/sim/replay.py \
    --channel ~/dev/cyrinx-artifacts/garage/crosscal-desk-evening-run1-20261110T214407.npz \
    --answers ~/dev/cyrinx-artifacts/garage/ladder-desk-evening-repeat4-*.jsonl
```

## Build order

1. **Channel extraction.** Impulse responses (500 ms horizon) from the retained sweeps,
   per direction and per phone microphone, and the four-path matrix from the speaker
   matrix capture. Stored beside the captures with a hash and the session metadata.
   `scratch/garage/acquire.py` and `speaker_matrix.py` already do the deconvolution;
   this step pins a file format.
2. **Linear replay.** Convolve the transmit waveform, add the session's recorded room
   tone at its recorded level, resample by the clock offset, decode with the same
   scoring the ladder uses. Every output row carries `evidence_class:
   simulated:measured-channel`, the channel hash, the codec build hash and a seed.
   The research receiver (`scratch/garage/clock_offset.py`) and the shipped decoder
   are both selectable, because the desk answer key has results for both.
3. **Two microphones and two speakers.** Replay through both microphones' responses
   from one sweep, and through each speaker arrangement the ladder supports
   (`both`, `left`, `right`, `delay`).
4. **Sweep runner.** A list of conditions, one appended result row each, resumable,
   with done/total and an estimate of time remaining. Runs on trogdor.

## Gates

Each is a command that passes or fails. A check unlocks only its own kind of
question. Free parameters (noise level, clock offset) come from the session's own
room tone and records, and are not adjusted to make a check pass. Thresholds are
frozen in the test file before the first run against the answer key.

| Check | Hardware answer (live record, 2026-11-10) | Pass if the replay gives | Unlocks |
|---|---|---|---|
| **Bit-Exact Loopback** | digital loopback decodes every block | every block, same bytes, on macOS and trogdor | running anything |
| **Shipped Decoder Fails** | shipped decoder: 0 blocks on every run of the evening five-run cells | 0 blocks on those cells | trusting that the replay carries the clock offset and echo that defeat the shipped fit |
| **Desk Guard Ladder** | 16-QAM r1/2, research receiver, both mics: 390/390 at 171 ms, 520/520 at 85 ms, 116/116 on one run at 43 ms, 29/116 at 21 ms, 0/110 at 16 ms | 95% of blocks or more at the first three guards, 40% or less at the last two | guard-length screening |
| **Density Ladder** | 64-QAM r3/4 867/870; 256-QAM r1/2 778/780; 256-QAM r3/4 57/702 (both speakers) | the first two at 95% of blocks or more, the third at 30% or less | modulation and rate screening |
| **Microphone Ordering** | 64-QAM r1/2, 171 ms: mic 0 423/580, mic 1 562/580, both 580/580 | same ordering; each within 15 points of measured | microphone-selection questions, for this pose only |
| **Speaker Ordering** | 256-QAM r2/3: right 531, delay 484, both 428, left 405 of 624 | right and delay above both, left not above both | speaker-arrangement and two-stream screening |
| **Held-Out Position** | a new capture at a different position or phone orientation, predictions committed before the session | decode or fail agrees in 7 of 8 cells | any claim beyond the desk cell |

`pytest scratch/sim -q` runs the first six from retained captures. The seventh needs
a hardware session.

The Density Ladder is the check most likely to fail. The exploration records that a
linear model is optimistic about dense modulation, because speaker distortion is not
in an impulse response. A failure there is a finding to record in
`docs/NEGATIVE_FINDINGS.md`, and it means rate questions stay on hardware until a
distortion model passes its own check.

## What it cannot answer, and the captures that would widen it

The retained channels are one pose. Replay cannot say which microphone is better with
the phone face down, in a hand, or in a pocket; it can only replay poses that were
measured. Microphone choice is the clearest case (see the live record, section 3):
on 2026-11-10 the phone's bottom-edge microphone heard the Mac about 16 dB below the
one at the top, with the phone face up. Earlier sessions had the phone face down, and
the relation there was not measured.

The cheapest way to widen the simulator is a pose sweep on hardware, about five
minutes per pose: one cross-calibration sweep, one speaker-matrix capture, and one
short ladder as that pose's answer key. Proposed poses: face up, face down, propped
upright, handheld, each at the desk position. The phone's accelerometer reading
should be stored with each capture so that orientation is a recorded fact and can
later be tested as a predictor; the test app does not report it yet.

## Out of scope

- Geometric or synthetic rooms as a basis for any decision (the exploration's
  false positive at a 43 ms guard).
- Other people's room recordings as channels.
- Speaker distortion, phase noise above 18 kHz, motion within a frame, and operating
  system audio processing. Each is a later step with its own check.
- Any throughput, range or "works in a room" claim from simulated rows.
- Changes to the shipped decoder, the profile registry, or `cyrinx-sim-bench`.
- Folding this into C3-13's planned PCM-mode simulator. That is the right long-term
  home; this prototype should be written so its channel stage can move there.

## Hardening (what a later stage would have to undo)

- Lives in `scratch/sim/` with research-receiver dependencies, not in the library.
- Channel files are `.npz` with an ad hoc schema.
- `scripts/check-claims.sh` cannot yet enforce that a line citing a simulated run
  says so; the exploration proposes a rule.

## Open questions for the owner

1. **Which question first?** Recommended default: speaker arrangement and guard
   length at the desk cell, because the answer key for both already exists.
2. **Is a pose sweep worth a hardware session before any simulator code?**
   Recommended default: yes, alongside the first build, since it also answers the
   microphone question directly.
3. **Scratch prototype or C3-13 from the start?** Recommended default: scratch
   prototype, moved once the first six checks pass.

# PRD vs. As-Built: Cyrinx Acoustic Link

Fresh as of 2026-06-09. Contrasts the original research brief ("Cyrinx: A
High-Fidelity Adaptive Ultrasonic Transport Protocol," PRD/research report,
Feb 2026) with the system that actually achieved the measured goodput goal
([docs/ACOUSTIC_BULK_PHY.md](ACOUSTIC_BULK_PHY.md)).

## Headline

| | PRD proposal | As-built (measured) |
|---|---|---|
| Band | 18.5–23.5 kHz (5 kHz, inaudible) | 1.1–23 kHz Mac→phone; 0.6–17 kHz phone→Mac (audible by design) |
| Target rate | "exceeding 4 kbps in optimal conditions"; QPSK ≈4, 16-QAM ≈8 kbps effective | **36.6 kbps** Mac→Pixel / **27.3 kbps** Pixel→Mac, byte-verified OTA |
| Receiver hardware | iPhone 16 Pro Max | Pixel 7a (decodes on-device) |
| OFDM geometry | FFT 1024, ~106 carriers, CP 96 (2 ms) | FFT 2048, ~818–935 used bins, CP 768 (16 ms) |
| FEC | Raptor/LDPC (rateless) + Hybrid ARQ | K=7 convolutional r3/4 + interleaver + per-symbol LLR weighting |
| MAC | TDD ping-pong, ACK w/ channel state report, gear state machine | Open-loop streaming bulk frames; MCS chosen from measured channel |
| Channel tracking | ZC-preamble CFO estimate at sync time | Comb pilots every 8th bin, per-symbol iterative slope/CPE fit |

The delivered rate is 4.5–9× the PRD's own "optimal" projection. One change
bought most of that: **abandoning the inaudibility constraint** (per the
revised goal) widened the channel from 5 kHz to ~16–22 kHz. Capacity scales
with bandwidth; no amount of cleverness inside 5 kHz reaches 20 kbps in the
phone→Mac direction, because the measured channel above 18 kHz is 10 dB SNR
falling to 4.6 dB — the Pixel speaker and Mac mic can't deliver the
ultrasonic band the PRD assumed. The PRD's hardware-response section
extrapolated from MacBook tweeter and iPhone MEMS-mic data; the actual
phone (Pixel 7a) breaks its premise on the return path.

## What the PRD got right

- **OFDM + adaptive QAM as the core**, with wideband modulation to survive
  comb-filter nulls. Correct and load-bearing; the multipath null structure
  it predicted is real (we measured two dominant arrivals ~19 samples apart
  and deep frequency-selective fading).
- **The OS audio DSP is the main platform enemy.** The PRD's analysis of
  VPIO/noise-suppression hostility on iOS mapped directly onto Android:
  `AudioSource.UNPROCESSED` is the Android analog of
  `AVAudioSessionModeMeasurement`, and processed sources would have wrecked
  QAM phase. Its warning that gain control reacts too slowly at packet
  onset is also visible in our data (first frame of each run has elevated
  EVM).
- **Preamble-gated decoding** (cheap detector wakes heavy DSP): the chirp
  matched filter plus suppression-window peak picking is exactly this.
- **EVM + SNR as the link metrics**, half-duplex as the duplexing model,
  48 kHz native sampling, and soft-decision decoding all carried through.
- **Volume/nonlinearity caution was half-right**: it predicted full-volume
  harmonic "ghost tones." Measured: EVM is level-independent across 20 dB
  of drive — speaker protection DSP, not static nonlinearity, governs; at
  our operating point neither limited the link.

## What the PRD got wrong (each cost real debugging time)

1. **Cyclic prefix sized 8× too small.** "CP ≈ 2 ms (96 samples) is
   sufficient to cover reflections from walls up to ~0.6 m" treats the
   channel as one desk bounce. Measured delay spread (−30 dB) is 10–22 ms.
   ISI from the long reverb tail was failure cause #1; CP 768 (16 ms) plus
   ISI-aware LLR weighting fixed it.
2. **No pilot structure.** The PRD's only phase tracking is CFO estimation
   from the doubled ZC preamble at sync time. With −24.6 ppm sample-clock
   skew between the two devices, phase error accumulates continuously and
   frame-long coherent demodulation is impossible without per-symbol
   tracking. Comb pilots + per-symbol slope/CPE fitting were mandatory.
   (The PRD worried about Doppler from picking the phone up — a transient —
   and missed the always-present clock skew.)
3. **Mono-speaker assumption.** It models "the MacBook speaker" as one
   source. Driving both speakers coherently wrecks the link (the right
   speaker arrives 3× weaker with decorrelated phase at a phone on the left
   palm rest). Left-speaker-only transmit was failure cause #2.
4. **Stream lifecycle effects absent.** The macOS output chain fades the
   final ~10 ms when a stream ends, killing the last OFDM symbol — and with
   a frame-wide interleaver, every FEC block with it. In-stream trailing
   silence is now part of the TX contract.
5. **FEC sophistication aimed at the wrong layer.** Raptor/LDPC rateless
   coding is heavier machinery than needed at 25–45 dB SNR; a classic
   punctured convolutional code is ample and ports to Kotlin in an
   afternoon. The actual coding subtlety the PRD never anticipated:
   **overconfident LLRs from one corrupted symbol poison Viterbi through
   the interleaver**. Per-symbol pilot-EVM noise weighting (soft erasures)
   was the single highest-leverage coding fix, and incidentally delivers
   the burst-noise immunity the PRD wanted rateless codes for.
6. **The chatty MAC is why the predecessor stack measured 0.27 kbps.** The
   PRD's TDD ping-pong with per-frame ACK/channel-state reports, 200 ms
   data frames, and a gear state machine was implemented by earlier
   sessions — and its turnaround gaps, tiny payloads, and ACK timeouts
   consumed >97% of airtime. For bulk transfer, long streaming frames with
   open-loop MCS selection (closed-loop adaptation can be added per-session
   rather than per-frame) is the right shape.
7. **Apple-only scoping.** Pinning the spec to vDSP/ANE/RemoteIO made it
   blind to the actual receiver (Android). The portable lesson survived;
   the implementation strategy chapter did not. The ANE wake-word detector
   remains unbuilt and unnecessary at desktop power budgets.

## What the goal change removed

The PRD optimized for *inaudible, zero-configuration UX* (guard band below
18.5 kHz, RRC shaping to prevent audible bleed, "creepy factor"
transparency). The revised goal — maximize *measured* goodput, any audible
frequency allowed — traded all of that away. A production system wanting
inaudibility returns to the PRD's 18.5–23.5 kHz band and should expect
roughly: Mac→phone ~15–25 kbps possible in principle (that band measured
36–40 dB SNR Mac→Pixel), phone→Mac only ~1–3 kbps (4.6–10 dB SNR) — i.e.,
**the PRD's symmetric-rate ambition is physically unreachable in the
ultrasonic band with this phone hardware**; asymmetric design would be
required.

Also out of scope vs. the PRD: encryption (the predecessor stack's
X25519/CTR/HMAC envelope exists on the old PHY and is not yet integrated
with the bulk PHY), discovery/wake-up, and rate-adaptation hysteresis.

## The meta-lesson

The PRD's roadmap began "Phase 1 (Simulation)." Every failure cause above —
ISI scale, speaker asymmetry, stream-end fade, capture silencing, LLR
poisoning — is invisible to simulation and was found only by demodulating
real captures with discriminating experiments
([ACOUSTIC_BULK_PHY.md §5](ACOUSTIC_BULK_PHY.md)). The predecessor effort's
"22 kbps" claim was a capacity formula; the channel supported ~135× what
that stack measured. Specs should budget most of their schedule for the
gap between the channel model and the channel.

# The decoder's pilot timing fit was the main off-contact failure; a blind receiver decodes the 2.5 ft captures

Offline analysis, 2026-11-10 evening, of the recordings retained from the
[2.5 ft range ladder](range-ladder-2026-11-10.md) made earlier the same day. No new
audio was played or recorded: the MacBook's lid was closed and the phone was off adb.
Tools are in `scratch/garage/`; replay results are retained beside the captures in
`~/dev/cyrinx-artifacts/garage/` as `ladder-*.blind.json`.

## In plain English

This afternoon's session found that the fast kind of signal can work with the phone
two and a half feet from the laptop, but only when a person told the decoder how far
apart the two devices' clocks run, and only on the laptop after the fact. Even then,
whole frames were lost at random, and several promising settings "did not hold".

Looking at why one such frame failed turned up a second, larger fault. For every
symbol, the decoder estimates a small timing shift from known reference tones spread
across the band. The way it makes that estimate is fragile when there are many tones
across a wide band: a small error gets multiplied by the width of the band, and the
decoder ends up "correcting" a shift that is not there. That twists the two ends of the
band in opposite directions and the frame is lost. This fault is in the decoder, not in
the room or the hardware. It reproduces in simulation with nothing but added hiss.

Two things were built, as research code beside the shipped decoder, which is unchanged:

1. **A steadier timing estimate** (the "coherent fit" below) that uses all the
   reference tones together.
2. **A receiver that measures the clock difference itself**, from the two training
   symbols at the start of each frame, corrects for it, and then decodes. Nobody tells
   it anything.

Replaying this afternoon's recordings through both, with nothing hinted:

- Every setting that was repeated five times now decodes every block or all but one,
  in both directions. The best laptop-to-phone setting carries 17.5 thousand bits per
  second (389 of 390 blocks). The best phone-to-laptop setting doubles to 12.6 thousand
  (280 of 280 blocks; it was 145 of 280 this afternoon).
- The two settings recorded this afternoon as "did not hold" now hold.
- Recordings made **after the laptop lid was closed**, which decoded nothing this
  afternoon, decode 229 of 234 blocks at full volume.
- Single recordings of denser settings that mostly failed this afternoon now mostly
  decode, up to about 21 thousand bits per second. One recording each, so these are
  leads.

What this is not. It is a replay of recordings, not a live link. It is one desk
position on one evening. The steadier estimate lives in a research build and is not in
the shipped decoder; putting it there changes the decoder's published behaviour and is
a decision for the project owner. Whether it also helps the touching-distance link is
shown only in simulation so far.

> **Correction to the range-ladder record.** That record's first conclusion was that
> clock-offset correction is "the largest single lever found today". It is not. The
> pilot timing fit is. With the coherent fit and **no** clock correction, QPSK decodes
> full-band in both directions on the same captures (209 of 210 blocks on the repeated
> downlink cell that decoded 0 as transmitted). Clock correction is still needed for
> 16-QAM and denser settings on the downlink. The record's statement that the failed
> captures held 9 to 16 dB of usable signal stands, and is now explained.

> **Correction to the range-ladder record.** "The other two single-run leads did not
> hold" described the decoder, not the channel. Downlink 16-QAM at 4–23 kHz is 314 of
> 350 blocks blind (157 hinted); uplink 16-QAM full band is 280 of 280 (145 hinted).

## Method, and what was tuned on what

The coherent fit was developed while looking at one capture, run 2 of the downlink
cell `16qam-full-g171-n16k` (16-QAM r1/2, 300–23000 Hz, NFFT 16384, 171 ms guard). Its
two search constants (search four main-lobe half-widths either side of the shipped
estimate, eight steps per lobe, three refinements) were written before any other
capture was replayed and were not changed afterwards. Every other capture in the tables
below was decoded once with those constants. The clock estimator's constants (search
range ±150 ppm, two passes) were likewise fixed before the replay.

Scoring is unchanged from the live session: a block counts only if its CRC passes and
its bytes equal the payload scheduled at that position; rates are verified payload over
the whole scheduled burst, gaps and trailing silence included. The receiver never sees
the payload.

Five columns appear below:

| Column | Meaning |
|---|---|
| Live | As transmitted, shipped decoder (this afternoon's record) |
| Hinted | Resampled by an offset chosen from a supplied list by lowest pilot EVM, shipped fit (this afternoon's record) |
| Fit only | No clock correction, coherent fit |
| Blind clock | Clock offset estimated from the frame and removed, shipped fit |
| Both | Blind clock correction and the coherent fit |

Reproduce with:

```sh
scratch/garage/build_clock_dylib.sh
CYRINX_BULK_CODEC_PATH=scratch/garage/libcyrinxclock.dylib \
  .venv/bin/python scratch/garage/blind_replay.py ~/dev/cyrinx-artifacts/garage/ladder-desk-2p5ft-repeat*.jsonl
```

## 1. What the shipped fit does wrong

After equalizing a data symbol, the decoder compares each pilot with its known value
and fits a straight line of phase against frequency: an offset and a slope. A slope is
what a small timing slide looks like. The shipped fit
(`cyrinx_fit_pilot_phase`, mirrored in `modem.py:fit_pilot_phase`) takes the slope from
the weighted average phase step between neighbouring pilots.

On the development capture, resampled to remove the clock offset so that the true
slide is close to zero, the shipped fit reported per-symbol slides of −0.05, −0.99,
−0.81, −0.67, −0.87 and −0.71 samples for the six data symbols of one frame, together
with offsets of +0.09, +1.99, +1.60, +1.31, +1.71 and +1.36 rad. A real delay cannot
produce that pair: a delay's phase line passes through zero at zero frequency, and
these lines pass through zero near 15 kHz, in the middle of the band's pilot weight.
That is the signature of a slope error that the offset term then re-centres. At a
one-sample slope error the lower band edge is rotated by about 1.9 rad and the upper by
about 1 rad the other way. Pilot EVM on those
symbols was 0.65 to 0.93 and the frame decoded 0 of 39 blocks.

Why the average phase step is fragile: with about 970 pilots across the band, an error
of one milliradian in the average step is nearly one radian by the far end of the band.
The coherent fit starts from the shipped estimate and then searches near it for the
slope at which all pilots add up in phase, which uses the full width of the band as its
lever instead of one pilot spacing.

On the same frame the coherent fit reported slides of −0.001 to −0.028 samples and
offsets within ±0.02 rad, pilot EVM of 0.19 to 0.24, and 39 of 39 blocks. With no clock
correction at all it reported −1.02, −1.71, −2.39, −3.08, −3.74 and −4.42 samples, a
straight line of 0.69 samples per symbol, which is what 28 ppm across a 24,576-sample
symbol predicts. So it tracks a real slide correctly; the frame still fails
uncorrected at 16-QAM (EVM about 0.9) because the stretch within each long symbol
remains.

The shipped fit's errors also explain this afternoon's erratic behaviour. Whole frames
flipped between decoding and failing when the supplied clock correction changed by
1 ppm, which moves samples by a few hundredths of a sample across a frame:

| Hinted correction (run 2, shipped fit) | Frame 1 | Frame 2 |
|---|---|---|
| +27.0 ppm | 39 / 39 | 39 / 39 |
| +27.5 ppm | 0 / 39 | 25 / 39 |
| +28.0 ppm | 0 / 39 | 7 / 39 |
| +29.0 ppm | 32 / 39 | 32 / 39 |

With the coherent fit the same frames decode 39 / 39 at every one of those settings.

### It reproduces in simulation with white noise only

Generated frames, added white Gaussian noise, no echo, no clock offset, six payload
seeds per row, same C decoder with the fit switched:

| Frame | SNR | Shipped fit | Coherent fit |
|---|---|---|---|
| 16-QAM r1/2, 300–23000 Hz, NFFT 16384 | 20 dB | 234 / 234 | 234 / 234 |
| | 14 dB | 111 / 234 | 234 / 234 |
| | 11 dB | 0 / 234 | 227 / 234 |
| | 9 dB | 0 / 234 | 32 / 234 |

So the fit costs this frame shape more than 3 dB before any room is involved. A
synthetic 40-tap echo gave the same ordering (14 dB: 1 / 234 against 233 / 234).

## 2. Blind clock estimate

A clock offset slides the second training symbol against the first by
`ppm × 1e-6 × (NFFT + CP)` samples. `cyrinx_research_estimate_clock_offset` runs the
decoder's own synchronization, takes the ratio of the two training-symbol channel
estimates, and searches for the slide that best explains its phase ramp. The decoder
wrapper estimates, resamples with a 128-tap Kaiser-windowed sinc, estimates the
remainder once more, resamples from the original, and decodes.

Across every decoded frame of the lid-open captures the estimate had a median of
+27.7 ppm on the downlink (135 frames; 5th to 95th percentile +25.2 to +29.9) and
−27.7 ppm on the uplink (105 frames; −29.4 to −26.2). This afternoon's independent
whole-frame fit gave +27.7 to +29.0 and −27.2 to −27.6. Single frames are noisy: the
extremes were +19.4, +34.2 and −37.4 ppm. The coherent fit absorbs an error of that
size; the shipped fit does not, which is why "Blind clock" alone is worse than
"Hinted" below.

The new resampler is flat to within 0.02 dB from 1 to 23 kHz (tested), where this
afternoon's 32-tap one drooped up to 1.4 dB at 23 kHz.

## 3. Repeated cells, five runs each

Downlink (Mac speaker to phone microphone):

| Cell | Live | Hinted | Fit only | Blind clock | Both | Rate, both |
|---|---|---|---|---|---|---|
| 16-QAM r1/2, 300–23000 Hz, NFFT 16384, 171 ms | 0 / 390 | 373 | 78 | 242 | **389 / 390** | 17.5 kbps |
| 16-QAM r1/2, 4–23 kHz, NFFT 8192, 85 ms | 0 / 350 | 157 | 159 | 84 | **314 / 350** | 15.0 kbps |
| 16-QAM r1/2, 300–23000 Hz, NFFT 8192, 171 ms | 0 / 320 | 286 | 291 | 280 | **320 / 320** | 14.4 kbps |
| 16-QAM r1/2, 8–23 kHz, NFFT 8192, 85 ms | 7 / 270 | 258 | 256 | 222 | **270 / 270** | 12.9 kbps |
| QPSK r1/2, 300–23000 Hz, NFFT 8192, 85 ms | 0 / 210 | 159 | 209 | 122 | **210 / 210** | 10.0 kbps |
| 16-QAM r3/4, 16–23 kHz, NFFT 4096, 85 ms | 55 / 150 | 143 | 143 | 143 | **149 / 150** | 7.3 kbps |

Uplink (phone speaker to Mac microphone):

| Cell | Live | Hinted | Fit only | Blind clock | Both | Rate, both |
|---|---|---|---|---|---|---|
| 16-QAM r1/2, 300–17000 Hz, NFFT 16384, 171 ms | 0 / 280 | 145 | 181 | 129 | **280 / 280** | 12.6 kbps |
| QPSK r1/2, 300–17000 Hz, NFFT 16384, 171 ms | 30 / 140 | 140 | 136 | 140 | **140 / 140** | 6.3 kbps |
| QPSK r1/2, 300–17000 Hz, NFFT 8192, 171 ms | 81 / 110 | 108 | 105 | 104 | **110 / 110** | 5.0 kbps |
| QPSK r1/2, 300–17000 Hz, NFFT 16384, 341 ms | 30 / 110 | 110 | 106 | 110 | **110 / 110** | 4.3 kbps |
| QPSK r1/2, 300–12000 Hz, NFFT 8192, 171 ms | 79 / 80 | 80 | 80 | 80 | **80 / 80** | 3.6 kbps |

Per-run blocks under "Both" were identical across all five runs for every cell except
three: the top downlink cell (78, 78, 78, 78, 77), the 16–23 kHz cell (30, 30, 29, 30,
30) and the 4–23 kHz cell (69, 69, 68, 38, 70). That last run lost most of one frame
and was not investigated.

## 4. Single recordings: leads, not results

One recording each, from this afternoon's second and third ladders. Shown where
"Both" differs materially from "Hinted".

| Direction | Cell | Hinted | Both | Rate, both |
|---|---|---|---|---|
| Down | 64-QAM r1/2, 300–23000 Hz, NFFT 8192, 171 ms | 41 / 96 | 92 / 96 | 20.7 kbps |
| Down | 16-QAM r3/4, 300–23000 Hz, NFFT 8192, 171 ms | 25 / 96 | 86 / 96 | 19.4 kbps |
| Down | 64-QAM r1/2, 8–23 kHz, NFFT 8192, 85 ms | 31 / 82 | 77 / 82 | 18.4 kbps |
| Down | 16-QAM r1/2, 300–23000 Hz, NFFT 8192, 85 ms | 20 / 84 | 70 / 84 | 16.7 kbps |
| Down | 16-QAM r3/4, 8–23 kHz, NFFT 8192, 85 ms | 25 / 82 | 68 / 82 | 16.2 kbps |
| Down | QPSK r3/4, 300–23000 Hz, NFFT 8192, 85 ms | 21 / 62 | 61 / 62 | 14.6 kbps |
| Down | 16-QAM r1/2, 12–23 kHz, NFFT 4096, 43 ms | 2 / 42 | 30 / 42 | 7.4 kbps |
| Up | 16-QAM r1/2, 300–17000 Hz, NFFT 8192, 171 ms | 22 / 46 | 46 / 46 | 10.4 kbps |
| Up | QPSK r3/4, 300–17000 Hz, NFFT 16384, 171 ms | 5 / 42 | 42 / 42 | 9.5 kbps |
| Up | 16-QAM r1/2, 300–12000 Hz, NFFT 16384, 171 ms | 0 / 40 | 40 / 40 | 9.0 kbps |
| Up | QPSK r1/2, 300–17000 Hz, NFFT 16384, 85 ms | 11 / 38 | 38 / 38 | 8.2 kbps |
| Up | QPSK r1/2, 300–17000 Hz, NFFT 8192, 85 ms | 4 / 30 | 30 / 30 | 7.2 kbps |

The uplink rows at an 85 ms guard decode against a measured 99 to 110 ms echo, so the
guard no longer has to cover the whole −10 dB spread on that link.

From the first ladder (transmitted before any correction was considered), full-band
QPSK with **no clock correction** and the coherent fit: downlink 85 ms 42 / 42 and
171 ms 32 / 32 (both 0 live); uplink 85 ms 30 / 30 (0 live) and 171 ms 22 / 22 (14
live).

## 5. Lower volume, and the lid-closed recordings

This afternoon's volume test was recorded as inconclusive: results were erratic and the
session ended when the lid was closed. Replayed with both changes, three runs each,
downlink cell `16qam-full-g171-n16k` (78 blocks per run):

| Condition | Transmit amplitude | Hinted | Both | Per run |
|---|---|---|---|---|
| Lid open | 0.25 | 78 / 234 | 197 / 234 | 78, 44, 75 |
| Lid open | 0.125 | 71 / 234 | 226 / 234 | 71, 78, 77 |
| Lid open | 0.06 | 58 / 234 | 153 / 234 | 76, 77, 0 |
| Lid closed | 0.5 | 0 / 234 | 229 / 234 | 78, 75, 76 |
| Lid closed | 0.25 | 0 / 234 | 100 / 234 | 25, 32, 43 |
| Lid closed | 0.125 | 0 / 234 | 0 / 234 | 0, 0, 0 |

The link works at one eighth of the afternoon's amplitude, and with the lid shut (about
16 dB less level and a 104 ms spread) at full amplitude. The lid-open rows are not
monotonic in amplitude (0.25 is worse than 0.125) and include a whole run at zero.
Those runs overlapped other load on the machine, as recorded this afternoon; no
threshold is claimed. Uplink at 0.25: 81 / 84; at 0.125: 74 / 84; at 0.06: 0 / 84. The
uplink amplitudes did not scale the received level this afternoon, so those rows do not
measure what they name.

## 6. Does this bear on the touching-distance link? Simulation only

The accepted near-field campaign missed its reliability gate (98.48% of blocks against
a 99.97% baseline; Negative Finding 4 and `docs/ACOUSTIC_BULK_PHY.md`). The same fit
runs there. In white noise only, 24 payload seeds per row:

| Frame | SNR | Shipped fit | Coherent fit |
|---|---|---|---|
| 64-QAM r2/3, CP 96, pilots/16, 64 symbols, 1100–23000 Hz (the accepted profile's shape) | 22 dB | 100.00% | 100.00% |
| | 20 dB | 97.39% | 99.77% |
| | 19 dB | 84.31% | 97.31% |
| | 18 dB | 47.39% | 85.09% |
| 16-QAM r3/4, CP 768, pilots/8, 64 symbols (the control shape) | 16 dB | 96.61% | 99.94% |
| | 14 dB | 32.94% | 95.28% |
| | 13 dB | 1.28% | 62.17% |

That is about 1 dB at the accepted profile's threshold and about 2 dB at the control
shape's. It is a hypothesis about the gate, not a result: the real channel is not white
noise, and the campaign's retained captures were not found on this Mac to replay. If
they exist, replaying them through `goodput_reanalysis.py` with the coherent fit is the
direct test.

## What was built

| File | What it is |
|---|---|
| `scratch/garage/clock_offset.c` | Research C: clock estimate, resampler, blind decode wrapper, the coherent pilot fit and a switch. It textually includes the codec source so it reuses the codec's own synchronization; nothing is added to the public headers |
| `scratch/garage/build_clock_dylib.sh` | Builds it with the codec into `libcyrinxclock.dylib` |
| `scratch/garage/clock_offset.py` | ctypes binding |
| `scratch/garage/blind_replay.py` | The five-column replay above |
| `scratch/garage/test_clock_offset.py` | 16 tests on generated frames, including the white-noise reproduction |
| `Sources/CCyrinx/cyrinx_bulk.c` | The two call sites of the pilot fit now name it through a macro that defaults to the shipped fit. A normal build preprocesses to the same code as before |

The research library decodes exactly as the codec does until the fit is switched; the
switch is process-wide and is meant for offline replay.

## What did not work, or was not done

- Nothing here is a live link. Captures were replayed on the Mac.
- Guards of 16 ms decode nothing in any column. Full-band 43 ms guards decode a
  minority of blocks at best (downlink QPSK 33 / 42 and 8 / 42 on two recordings;
  uplink 0 / 32).
- "Blind clock" with the shipped fit is worse than "Hinted" on most cells. The clock
  estimate alone is not the fix.
- One run of the 4–23 kHz downlink cell lost most of a frame (38 of 70) and one
  lid-open low-volume run decoded nothing; neither was investigated.
- The per-frame clock estimate is noisy (extremes 8 to 10 ppm from the median). The
  offset is a fixed property of a device pair, so averaging it across frames or caching
  it should be better; not built.
- The clock estimate searches ±150 ppm and was only exercised near ±28 ppm on hardware
  and at 0, ±28 and 90 ppm on generated frames.
- The afternoon's "341 ms silence after the chirp" captures cannot be replayed with
  this library: they were transmitted by a variant build with a different frame layout.
  They decode 0 here, including the cell that decoded 22 / 22 live.
- The Python reference was not given the coherent fit as a tracked change; it was
  monkey-patched during diagnosis only. The afternoon's open Python-versus-C
  disagreement on two uplink captures was not revisited.
- Only the phone's first microphone was used.
- Decoding cost was not measured. The coherent fit evaluates about 120 trial slopes
  per symbol across all pilots; the resampler is a 128-tap filter over the capture.
- One position, one evening, nothing moving, one device pair.

## Where this points

Proposed by the agent that did the analysis; not yet reviewed.

1. **Decide whether the coherent fit goes into the shipped decoder.** It changes what
   the decoder returns on existing captures, so it is a new receiver semantics version:
   golden vectors, the Python reference, the Kotlin port and the receiver contract all
   move with it. The evidence so far is one desk session replayed, plus simulation.
2. **Replay the near-field campaign captures with it**, if they are retained anywhere.
   That is the cheapest test of whether it closes the reliability gate.
3. **Run the distance ladder live with this receiver**, starting from denser settings:
   the single-recording leads above reach about 21 kbps down and 10 to 12 kbps up, and
   85 ms guards now work full-band in both directions.
4. Then the items the range-ladder record already listed: two-microphone combining and
   stronger coding, against a decoder that is no longer the limit.

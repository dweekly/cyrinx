# Off-contact range ladder at 2.5 ft: longer guards and clock correction carry data

Session record, 2026-11-10, about 17:33 to 18:40 local. MacBook Pro M4 and Pixel 7a,
both stationary on a desk about 2.5 ft (76 cm) apart, Pixel over Wi-Fi adb. Mac output
volume 56%, phone media volume 25/25, transmit amplitude 0.5. Every capture is retained
in `~/dev/cyrinx-artifacts/garage/` (names below); tools are in `scratch/garage/`.

## In plain English

Until today the project's record said that the fast kind of signal cannot work once the
phone is lifted off the laptop: a foot of air gave zero data, and the only thing that
got through was a very slow fallback. This session repeated that failure at two and a
half feet with the recordings kept this time, and then found that two changes make the
fast kind of signal work at that distance:

1. **Leave a much longer pause between symbols**, so the room's echo has died down
   before the next symbol is read. The standard setting leaves 16 ms. The settings that
   worked leave 85 ms or 171 ms.
2. **Correct for the two devices' clocks running at slightly different speeds.** The
   Mac and the Pixel disagree by about 28 parts per million. That is tiny, but over a
   multi-second frame it is enough to make the receiver lose track. Stretching the
   recording by that amount before decoding fixes it.

With both, the laptop-to-phone direction delivered about 17 thousand bits per second of
checked data at its best setting, and the phone-to-laptop direction about 6 thousand,
each repeated five times. For comparison, the slow fallback is 138 bits per second, and the touching-
through-cloth record is about 66 thousand.

What this is not: the clock correction was applied on the laptop after each recording,
not inside a live receiver; it is one desk position on one evening with nothing moving;
and the rates count a two-frame burst, not a sustained transfer. It shows the signal can
be decoded there, not that a finished product does so.

> **Correction to a recorded finding.** Negative Finding 13 says coherent CP-OFDM
> "cannot link in a reverberant desk geometry, at ANY MCS". At 2.5 ft, with a longer
> guard and clock correction, it carries verified payload in both directions. The
> finding's own measurements were not wrong; what it did not have was the clock
> correction, without which the long-guard cells fail here too.

## What was run

| Step | Tool | Retained as |
|---|---|---|
| Channel sweeps, both directions, twice | `crosscal.py` | `crosscal-desk-2p5ft-run{1,2}-*.npz` |
| Conservative coherent probe and Mac self-loop control | `linkprobe.py` | `linkprobe-desk-2p5ft-*.npz` |
| Echo length per frequency band | `band_spread.py` | `crosscal-*.bands.json` |
| First ladder: guard length and band, as transmitted | `ladder.py --set first` | `ladder-desk-2p5ft-{down,up}-*` |
| Same cells with a 341 ms silence after the chirp | variant build, see below | `ladder-desk-2p5ft-chirpguard341ms-*` |
| Signal present in each capture, bin by bin | `genie_sinr.py` | `ladder-*.genie.json` |
| Replay of first-ladder captures with clock correction | `resample_replay.py` | `ladder-*.resample.json` |
| Slide between symbols versus stretch within a symbol | `slide_experiment.py` | printed only |
| Second ladder, clock-corrected scoring | `ladder.py --set second --ppm …` | `ladder-desk-2p5ft-pass2-*` |
| Five repeats of the best cells | `ladder.py --repeat 5` | `ladder-desk-2p5ft-repeat-*` |
| Third ladder, single exploratory runs, then repeats | `ladder.py --set third` | `ladder-desk-2p5ft-pass3-*`, `-repeat3-*` |

"Down" is Mac speaker to phone microphone; "up" is phone speaker to Mac microphone.
Every cell is two frames with independently seeded payloads, a declared 250 ms gap and
0.4 s of trailing silence. A block counts only if its bytes match the payload scheduled
at that position. Rates are verified payload bytes over the whole scheduled burst,
gaps and tail included.

## 1. The September failure reproduces, with a passing control

| Link (conservative QPSK r1/2, CP 768 = 16 ms) | Chirp peak/mean | Best EVM | Ordered blocks |
|---|---|---|---|
| Mac → Pixel, 300–23000 Hz | 121.1 | 1.011 | 0 / 52 |
| Pixel → Mac, 300–17000 Hz | 71.0 | 2.1 | 0 / 38 |
| Mac → Mac (control) | 139.8 | 0.112 | 52 / 52 |

Strong-tap delay spread at −10 dB, two runs each: Mac → Pixel 44.8 and 50.3 ms;
Pixel → Mac 98.8 and 110.0 ms. September's one-foot readings were 48.3 and 82.3 ms.

## 2. The echo is shorter at high frequencies, on the downlink

`band_spread.py` band-limits the sweep capture and the room tone with the same mask and
runs them through the unchanged deconvolution and delay-spread readout. Run 1, −10 dB
spread:

| Band | Mac → Pixel mic 0 | Mac → Pixel mic 1 | Pixel → Mac |
|---|---|---|---|
| 300–23000 Hz | 44.6 ms | 53.9 ms | 98.8 ms |
| 2–4 kHz | 97.4 | 97.2 | 108.1 |
| 4–8 kHz | 99.3 | 101.1 | 99.9 |
| 8–12 kHz | 62.7 | 62.0 | 88.0 |
| 12–16 kHz | 51.5 | 46.3 | 72.0 |
| 16–20 kHz | 32.1 | 42.9 | 52.1 |
| 20–23 kHz | 30.2 | 35.8 | 47.7 (low SNR) |

Run 2 agrees within about 15% per band. The full-band figure understates the echo in
the 2–8 kHz band, where it is about 100 ms in both directions, and overstates it above
16 kHz on the downlink. The uplink 20–23 kHz row has an impulse-response peak only
about 20 dB over noise and should not be relied on.

## 3. First ladder, as transmitted (no clock correction)

Downlink, one run per cell:

| Cell | Band | Guard | Blocks | Verified rate |
|---|---|---|---|---|
| QPSK | 300–23000 | 16 ms | 0 / 48 | 0 |
| QPSK | 300–23000 | 43 ms | 0 / 42 | 0 |
| QPSK | 300–23000 | 85 ms | 0 / 42 | 0 |
| QPSK | 300–23000 | 171 ms | 0 / 32 | 0 |
| QPSK | 16–23 kHz | 16 ms | 0 / 14 | 0 |
| QPSK | 16–23 kHz | 43 ms | 12 / 12 | 2.95 kbps |
| QPSK | 16–23 kHz | 85 ms | 10 / 10 | 2.43 kbps |
| 16-QAM | 16–23 kHz | 85 ms | 20 / 20 | 4.87 kbps |
| QPSK | 12–23 kHz | 43 ms | 18 / 20 | 4.43 kbps |
| QPSK | 8–23 kHz | 85 ms | 21 / 26 | 5.01 kbps |

Uplink: QPSK 300–17000 Hz at a 171 ms guard decoded 14 / 22 (3.15 kbps) and BPSK
10 / 10 (2.25 kbps); 12–17 kHz QPSK at 171 ms decoded 6 / 6. Everything at 16 ms and
43 ms decoded nothing.

So a longer guard alone already works in a narrow upper band on the downlink and, with
a 171 ms guard, partly on the uplink. Full-band downlink decodes nothing at any guard,
with EVM near 1.0, which is the pattern Negative Finding 13 recorded.

## 4. The failed captures hold plenty of signal

`genie_sinr.py` regenerates each transmitted waveform from its seed, fits one complex
gain per frequency bin across the whole frame, and counts what is left as interference
plus noise. That is the best any receiver with a fixed channel estimate could do on
that capture. It first fits and removes one clock-offset value per frame; without that
step it reports nonsense (cells that decoded 12 / 12 scored −20 dB), which is how the
clock offset was noticed.

| Downlink cell (all decoded 0 blocks live) | Median SINR | Fitted clock offset |
|---|---|---|
| QPSK full band, 16 ms guard | 3.4 dB | +29.0 ppm |
| QPSK full band, 43 ms guard | 9.3 dB | +28.9 ppm |
| QPSK full band, 85 ms guard | 13.5 dB | +27.8 ppm |
| QPSK full band, 171 ms guard | 16.1 dB | +27.7 ppm |

The fitted offset is +27.7 to +29.0 ppm on every downlink capture and −27.2 to
−27.6 ppm on every uplink capture, as a fixed clock difference between two devices
should be. Fitting the gain separately on each half of a frame gains only 0.2 to
0.6 dB, so the channel was not changing within a frame.

The garage plan's premise that a failed long-echo capture holds nothing for better
decoding to recover does not hold here: at an 85 ms guard the capture has about
13 dB of usable signal-to-interference and the decoder still returned zero blocks.

## 5. Correcting the clock offset rescues them

`resample_replay.py` resamples the retained capture by a candidate offset and decodes
it with the unmodified C codec. Replaying at 0 ppm reproduces the live block counts
exactly. First-ladder downlink captures:

| Cell | Live | +28 ppm | −28 ppm (wrong sign) |
|---|---|---|---|
| QPSK full band, 85 ms | 0 / 42 | 41 / 42 | 0 / 42 |
| QPSK full band, 171 ms | 0 / 32 | 32 / 32 | 0 / 32 |
| QPSK 8–23 kHz, 85 ms | 21 / 26 | 26 / 26 | 0 / 26 |
| 16-QAM 16–23 kHz, 85 ms | 20 / 20 | 20 / 20 | 1 / 20 |
| QPSK full band, 43 ms | 0 / 42 | 0 / 42 | 0 / 42 |
| QPSK full band, 16 ms | 0 / 48 | 0 / 48 | 0 / 48 |

The wrong-sign column is the negative control: it makes working cells fail. Short
guards stay at zero with or without correction, so both changes are needed.

### Why the decoder needs it: partly identified

A clock offset does two things to a long symbol. Between symbols it slides each one a
fraction of a sample against the last, which is a phase ramp across frequency. Within a
symbol it stretches the waveform, which leaks energy between neighbouring bins.
Resampling removes both. `slide_experiment.py` removes only the first, in a patched
copy of the Python reference receiver: it measures the ramp between the two training
symbols, undoes it before averaging them, and pre-rotates each data symbol. One frame
per cell, downlink:

| Cell | As captured | Slide fix only | Resampled +28 ppm |
|---|---|---|---|
| QPSK full band, 171 ms | 0 / 16, EVM 1.36 | 15 / 16, EVM 0.64 | 16 / 16, EVM 0.33 |
| QPSK full band, 85 ms | 0 / 21, EVM 1.46 | 0 / 21, EVM 0.95 | 18 / 21, EVM 0.71 |
| QPSK 8–23 kHz, 85 ms | 9 / 13, EVM 0.72 | 13 / 13, EVM 1.02 | 13 / 13, EVM 0.49 |

Three things follow. The slide measured between the two training symbols is +0.335,
+0.450 and +0.350 samples against +0.344, +0.459 and +0.344 predicted from 28 ppm, so
the offset is confirmed by a second, independent method. The failure is present from
the first data symbol (EVM about 1.4 to 1.6 on every symbol as captured), so it is not
a tracker gradually losing lock; the two training symbols are averaged and differenced
as if nothing moved between them, and on a 256 to 341 ms symbol something has. And the
ramp correction alone recovers most of the 171 ms cell but not the 85 ms cell, so the
within-symbol effect matters too. Resampling in the time domain is the fix to build;
the frequency-domain shortcut is not enough.

This was run on the Python reference, downlink only. On two uplink captures the Python
reference and the C codec disagreed with each other (Python decoded nothing where C
decoded 14 / 22), which was not investigated, so no uplink conclusion is drawn from it.

## 6. Second ladder and repeats, clock-corrected

From here each capture is decoded at several candidate offsets and the one with the
lowest pilot EVM is scored. That choice uses only known pilots, never the payload.

Downlink, five runs each:

| Cell | Blocks | Per-run blocks | Mean verified rate |
|---|---|---|---|
| 16-QAM r1/2, 8–23 kHz, NFFT 8192, 85 ms guard | 258 / 270 | 51, 54, 51, 54, 48 | 12.3 kbps |
| 16-QAM r1/2, 300–23000 Hz, NFFT 8192, 171 ms guard | 286 / 320 | 33, 64, 61, 64, 64 | 12.9 kbps |
| QPSK r1/2, 300–23000 Hz, NFFT 8192, 85 ms guard | 159 / 210 | 42, 35, 21, 40, 21 | 7.6 kbps |
| 16-QAM r3/4, 16–23 kHz, NFFT 4096, 85 ms guard | 143 / 150 | 29, 25, 30, 30, 29 | 7.0 kbps |

Uplink, five runs each:

| Cell | Blocks | Per-run blocks | Mean verified rate |
|---|---|---|---|
| QPSK r1/2, 300–17000 Hz, NFFT 16384, 171 ms guard | 140 / 140 | 28 ×5 | 6.3 kbps |
| QPSK r1/2, 300–17000 Hz, NFFT 8192, 171 ms guard | 108 / 110 | 22, 22, 22, 22, 20 | 4.9 kbps |
| QPSK r1/2, 300–12000 Hz, NFFT 8192, 171 ms guard | 80 / 80 | 16 ×5 | 3.6 kbps |

Without correction the same repeat captures decoded 7 / 270, 0 / 320, 0 / 210 and
55 / 150 on the downlink, and 30 / 140, 81 / 110 and 79 / 80 on the uplink. The
300–12000 Hz uplink cell is the one setting that works nearly as well uncorrected.

Several downlink runs lost exactly one of the two frames (21 of 42, 33 of 64). Whether
that is acquisition of the second frame or something in the channel was not examined.

The 16–23 kHz cell is worth noting separately: it sits almost entirely above the range
most adults hear, and it carried 7.0 kbps.

Single-run cells from the second ladder that did not hold: 16-QAM full band at 85 ms
(20 / 84), 16-QAM r3/4 full band at 171 ms (25 / 96), 64-QAM full band at 171 ms
(41 / 96), and uplink 16-QAM at 171 ms (22 / 46 full band, 3 / 32 below 12 kHz).

### Third ladder: single exploratory runs, then repeats of the best

One run per cell, clock-corrected, around the cells that had repeated well:

| Direction | Cell | Blocks | Verified rate |
|---|---|---|---|
| Down | 16-QAM r1/2, 300–23000 Hz, NFFT 16384, 171 ms | 77 / 78 | 17.3 kbps |
| Down | 16-QAM r1/2, 4–23 kHz, NFFT 8192, 85 ms | 67 / 70 | 16.0 kbps |
| Down | 16-QAM r1/2, 8–23 kHz, NFFT 16384, 85 ms | 61 / 68 | 13.2 kbps |
| Down | 16-QAM r1/2, 12–23 kHz, NFFT 8192, 85 ms | 40 / 40 | 9.5 kbps |
| Down | 16-QAM r1/2, 8–23 kHz, NFFT 8192, 171 ms | 42 / 42 | 9.5 kbps |
| Down | 64-QAM r1/2, 8–23 kHz, NFFT 8192, 85 ms | 31 / 82 | 7.4 kbps |
| Down | 16-QAM r3/4, 8–23 kHz, NFFT 8192, 85 ms | 25 / 82 | 6.0 kbps |
| Down | 16-QAM r1/2, 12–23 kHz, NFFT 4096, 43 ms | 2 / 42 | 0.5 kbps |
| Up | 16-QAM r1/2, 300–17000 Hz, NFFT 16384, 171 ms | 51 / 56 | 11.5 kbps |
| Up | QPSK r1/2, 300–17000 Hz, NFFT 16384, 341 ms | 22 / 22 | 4.3 kbps |
| Up | QPSK r1/2, 300–8000 Hz, NFFT 16384, 171 ms | 12 / 12 | 2.7 kbps |
| Up | QPSK r1/2, 300–17000 Hz, NFFT 16384, 85 ms | 11 / 38 | 2.4 kbps |
| Up | QPSK r3/4, 300–17000 Hz, NFFT 16384, 171 ms | 5 / 42 | 1.1 kbps |
| Up | 16-QAM r1/2, 300–12000 Hz, NFFT 16384, 171 ms | 0 / 40 | 0 |

Five repeats of the three most promising, plus one long-guard uplink cell:

| Direction | Cell | Blocks | Per-run blocks | Mean verified rate |
|---|---|---|---|---|
| Down | 16-QAM r1/2, 300–23000 Hz, NFFT 16384, 171 ms | 373 / 390 | 68, 78, 75, 76, 76 | 16.8 kbps |
| Down | 16-QAM r1/2, 4–23 kHz, NFFT 8192, 85 ms | 157 / 350 | 26, 44, 49, 10, 28 | 7.5 kbps |
| Up | 16-QAM r1/2, 300–17000 Hz, NFFT 16384, 171 ms | 145 / 280 | 15, 29, 21, 52, 28 | 6.5 kbps |
| Up | QPSK r1/2, 300–17000 Hz, NFFT 16384, 341 ms | 110 / 110 | 22 ×5 | 4.3 kbps |

The full-band NFFT 16384 downlink cell held and is the best repeated result of the
session. The other two single-run leads did not hold: the 4–23 kHz downlink cell and
16-QAM on the uplink each lost about half their blocks over five runs. That is why
single runs in this record are leads and not results. Uncorrected, these same captures
decoded 0 / 390, 0 / 350, 0 / 280 and 30 / 110.

## 7. A longer silence after the chirp: inconclusive

The literature review suggested the 42.7 ms silence between the acquisition chirp and
the training symbols lets the chirp's own echo corrupt channel estimation. A variant
build with that silence set to 341 ms (`CYRINX_BULK_GUARD` 16384, built outside the
repo) was run on the first-ladder cells, without clock correction. Downlink full band
still decoded zero at every guard. Uplink QPSK at 171 ms went from 14 / 22 to 22 / 22
and the downlink 16–23 kHz cell's EVM from 0.68 to 0.47. These are single runs against
a later result that clock correction alone changes far more, so this neither confirms
nor rules out the chirp-echo effect.

## What did not work or was not done

- Nothing was decoded by a live receiver with clock correction; the correction is an
  offline step on real captures.
- The offline resampler is a 32-tap windowed sinc. It is flat to 20 kHz and droops above
  (about 0.2 dB at 22 kHz, up to 1.4 dB at 23 kHz), so corrected results are slightly
  pessimistic at the top of the downlink band.
- Guards of 16 ms and 43 ms decode nothing full-band, corrected or not.
- Higher-order or higher-rate cells mostly failed, as listed above.
- Only the phone's first microphone was used for decoding. `crosscal.py` retained both;
  `ladder.py` does not yet.
- One geometry, one evening, nothing moving, quiet room. No handheld, no second
  position, no other device pair, no sustained transfer, no reverse-control channel.
- The September one-foot cell itself was not re-measured; this is 2.5 ft.
- The mechanism by which a 28 ppm offset defeats the decoder is only partly identified (section 5), and only on the downlink.
- The Python reference and the C codec disagree on two uplink captures; not investigated.
- The MFSK floor was not run in this position.

## Where this points

Proposed by the agent that ran the session; not yet reviewed.

1. **Put clock-offset correction in the receiver.** Estimate it from pilots or from the
   spacing of successive chirps, and resample before the FFT. It is a fixed property of
   a device pair, so it can be cached. This is the largest single lever found today.
2. **Resolve the Python-versus-C disagreement on the uplink captures**, which are
   retained, before trusting either as the reference for this regime.
3. **Add long-guard geometries** (NFFT 8192 or 16384, 85 to 171 ms guard) as research
   configurations, and band choice by direction: upper band on a laptop-to-phone link.
4. **Re-run the distance ladder** with these settings, including 10 cm, one foot and
   beyond 2.5 ft, and a handheld cell, before claiming any range.
5. Then revisit stronger coding and two-microphone combining, which the literature
   review ranks next, against a baseline that now decodes.

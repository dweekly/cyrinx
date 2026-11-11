# Live at 2.5 ft: the blind receiver, both phone microphones, ranging, and the two-speaker matrix

Session record, 2026-11-10, about 21:40 to 22:40 local. MacBook Pro M4 and Pixel 7a
on a desk, in the same position as the afternoon's
[range ladder](range-ladder-2026-11-10.md) to within about half an inch (owner's
statement), lid open, Pixel on Wi-Fi adb. Mac output volume 56%, phone media volume
25/25, transmit amplitude 0.5 unless stated. Every capture is retained in
`~/dev/cyrinx-artifacts/garage/` under the names below.

## In plain English

Earlier this evening a [replay of recordings](pilot-fit-and-blind-clock-correction-2026-11-10.md)
suggested that a repaired decoder could carry far more data across two and a half feet
of air than the project had recorded. This session tested that with the devices
actually playing and listening, and then tried three further ideas.

1. **The repaired receiver works live.** With nothing told to it, it decoded 389 of 390
   blocks from laptop to phone (17.5 thousand bits per second) and 280 of 280 from
   phone to laptop (12.6 thousand), each over five runs. The shipped decoder returned
   zero on every one of these.
2. **Using both of the phone's microphones roughly doubles the laptop-to-phone rate.**
   The phone has two microphones. With the phone lying face up, the one the project
   has always decoded with heard the laptop's direct sound about six times more
   faintly than the other. That is a fact about this placement, not about the
   microphones: face down or in a hand it may reverse.
   Combining the two, the link carried **39 thousand bits per second** (867 of 870
   blocks over five runs), and a second setting 35 thousand (778 of 780). For
   comparison, the project's touching-distance record is about 66 thousand, and this
   morning the recorded figure for any distance off contact was zero.
3. **The two devices can measure how far apart they are.** Each plays chirps while both
   listen; the distance falls out without any shared clock. It read 93.4 cm on one
   phone microphone and 99.6 cm on the other, each repeating to about a millimetre.
4. **The laptop's two speakers partly cancel each other at the phone.** They have been
   playing the same signal. At about one frequency in ten, the phone receives a quarter
   or less of what the better speaker alone would deliver. Aligning the two speakers'
   phase per frequency would recover that; sending two separate streams (2x2 MIMO)
   has a theoretical ceiling about 60% above one stream here, not double. Tried
   directly on settings at the edge of working, the right speaker alone got more data
   through than both together in all twelve paired runs.

What this is not. It is one desk position on one evening with nothing moving. The
receiver is research code beside the shipped decoder and decodes on the laptop from
recordings pulled off the phone seconds after they are made; it is not a streaming
receiver on the phone. The rates count a two-frame burst. The two-speaker results are
measurements of the channel and theoretical bounds, not a working transmitter.

## What was run

| Step | Tool | Retained as |
|---|---|---|
| Channel sweeps, both directions | `crosscal.py` | `crosscal-desk-evening-run1-*` |
| Denser settings, single runs, blind receiver | `ladder.py --set fourth --blind` | `ladder-desk-evening-pass4-{down,up}-*` |
| Five repeats of three leaders per direction | `ladder.py --repeat 5` | `ladder-desk-evening-repeat4-{down,up}-*` |
| Two-way ranging, three runs | `rangefind.py` | `rangefind-desk-evening-*` |
| Path from each Mac speaker to each phone microphone | `speaker_matrix.py` | `speakermatrix-desk-evening-*` |
| Denser still, both microphones scored | `ladder.py --set fifth`, `--set sixth` | `ladder-desk-evening-pass{5,6}-down-*` |
| Five repeats of the two densest that worked | `ladder.py --set sixth --repeat 5` | `ladder-desk-evening-repeat6-down-*` |

Scoring is the afternoon's: a block counts only if its CRC passes and its bytes equal
the payload scheduled at that position; rates are verified payload over the whole
scheduled two-frame burst. "Blind" means the research receiver of
`scratch/garage/clock_offset.c`: clock offset estimated from each frame's training
symbols, coherent pilot fit. "Live" columns below are the shipped decoder on the same
capture.

## 1. The echo reads longer than in the afternoon, in the same position

| −10 dB spread | Afternoon (two runs) | Evening (one run) |
|---|---|---|
| Mac → Pixel | 44.8, 50.3 ms | 78.4 ms |
| Pixel → Mac | 98.8, 110.0 ms | 123.3 ms |

The phone moved by about half an inch at most. So the single-number spread for "one
position" varies by tens of milliseconds between sessions, and should not be read as a
property of a geometry to that precision.

## 2. The blind receiver, live, first phone microphone

Five runs each. The shipped decoder scored 0 on every run in this table.

| Direction | Cell | Blind blocks | Per run | Rate |
|---|---|---|---|---|
| Down | 16-QAM r1/2, 300–23000 Hz, NFFT 16384, 171 ms | 389 / 390 | 78, 78, 77, 78, 78 | 17.5 kbps |
| Down | 16-QAM r1/2, 300–23000 Hz, NFFT 16384, 85 ms | 459 / 520 | 103, 99, 94, 98, 65 | 19.9 kbps |
| Down | 64-QAM r1/2, 300–23000 Hz, NFFT 16384, 171 ms | 423 / 580 | 84, 89, 107, 112, 31 | 19.0 kbps |
| Up | 16-QAM r1/2, 300–17000 Hz, NFFT 16384, 171 ms | 280 / 280 | 56 ×5 | 12.6 kbps |
| Up | 16-QAM r3/4, 300–17000 Hz, NFFT 16384, 171 ms | 353 / 430 | 69, 70, 68, 68, 78 | 15.9 kbps |
| Up | QPSK r1/2, 300–17000 Hz, NFFT 16384, 85 ms | 190 / 190 | 38 ×5 | 8.2 kbps |

The first and fourth rows repeat the replay result (389 / 390 and 280 / 280) with new
transmissions. Single-run uplink leads from the same session: 64-QAM r1/2 at 171 ms
64 / 86; QPSK at a 43 ms guard 29 / 38.

Offline test suites were run on the Mac during some of the downlink repeats. The dips
in runs four and five are not explained by that: the same captures decode in full on
the other microphone (next section).

## 3. The phone's two microphones are not alike, and combining them is the large gain

The ranging and speaker-matrix captures show the Mac's direct sound at the phone's
first microphone (channel 0 of the `UNPROCESSED` stereo capture, the one every earlier
decode used) about 16 dB below the second, and arriving after it: on channel 0 the
strongest arrival of a Mac sweep is a reflection 9.5 ms later than channel 1's direct
sound, and one comparably strong follows 17 ms later.

**Which microphone is which (added 2026-11-11).** The phone's own description of its
microphones, logged by the test app at each recording
(`MicrophoneInfo`), is: channel 0 is `builtin_mic_1`, address `bottom`, 5.8 mm from
the bottom edge; channel 1 is `builtin_mic_2`, address `back`, 145.6 mm up, at the top
of the phone. This is the manufacturer's metadata and was not checked against the
hardware. The phone was lying face up on the table throughout.

**How the phone was lying (owner, 2026-11-11).** Face up, with its top edge pointing
towards the laptop and its bottom edge, with a USB-C charging cable plugged in,
pointing away. So the stronger channel is the microphone at the end nearer the
laptop, and the weaker one is at the far end, facing away, beside the cable. That
agrees in sign with the ranging in section 4, which put channel 0 about 6 cm farther
from the Mac than channel 1; the phone reports the two microphones 14 cm apart along
its length, so the phone's axis was not pointing straight at the speaker, or channel
0's weak first arrival was timed late, or both. The 16 dB difference is more than
the extra distance explains (about 0.6 dB for 99.6 cm against 93.4 cm). Shadowing by the phone
body and the direction the bottom port faces are the likely causes; neither was
tested.

**This result is specific to that pose (owner, 2026-11-11).** Which microphone hears
the far end better depends on how the phone is lying or held. Many earlier sessions
had the phone face down, where the relation between the two was never measured, so
nothing here says the earlier choice of channel 0 was wrong for those sessions, and
nothing says channel 1 is the one to prefer. The rule that follows is to make no
assumption: record every available microphone and choose or combine from what each
hears. The accelerometer can report orientation, which is worth recording with each
capture and testing as a predictor, but it does not replace listening. The test app
does not report it yet.

The codec already has a two-microphone maximal-ratio combiner
(`cyrinx_bulk_demodulate2`). `cyrinx_research_demodulate2_clock_corrected` estimates
the clock offset on one microphone, resamples both, and hands the pair to it.

Replaying the section 2 downlink repeats from the retained stereo recordings:

| Cell (5 runs) | Mic 0 | Mic 1 | Both combined | Rate, both |
|---|---|---|---|---|
| 16-QAM r1/2, 171 ms | 389 / 390 | 390 | 390 | 17.6 kbps |
| 64-QAM r1/2, 171 ms | 423 / 580 | 562 | 580 | 26.1 kbps |
| 16-QAM r1/2, 85 ms | 459 / 520 | 515 | 520 | 22.6 kbps |

Neither microphone is always the better one (on a later 64-QAM run mic 0 decoded 107
of 116 and mic 1 84), so choosing one is not the answer; combining is.

### Denser settings, live, both microphones

All 300–23000 Hz, NFFT 16384. "Both" synchronizes on mic 1; the ladder also scores
synchronizing on mic 0 and reports whichever has the lower pilot EVM, which never sees
the payload. In all ten repeat runs that was "both, synchronized on mic 1".

Five runs each:

| Cell | Mic 0 | Mic 1 | Both | Per run, both | Rate, both |
|---|---|---|---|---|---|
| 64-QAM r3/4, 171 ms guard | 0 / 870 | 20 | **867 / 870** | 174, 173, 174, 173, 173 | 39.0 kbps |
| 256-QAM r1/2, 171 ms guard | 22 / 780 | 220 | **778 / 780** | 154, 156, 156, 156, 156 | 35.0 kbps |

Synchronizing on mic 0 instead gave 385 / 870 and 649 / 780, so which microphone
carries synchronization matters; the pilot-EVM choice picked the better one each time.

Single runs, both microphones, the arrangement with the lower pilot EVM (leads, not
results):

| Cell | Both | Rate |
|---|---|---|
| 64-QAM r2/3, 171 ms | 156 / 156 | 35.1 kbps |
| 256-QAM r2/3, 171 ms | 155 / 208 | 34.9 kbps |
| 256-QAM r3/4, 171 ms | 22 / 234 | 5.0 kbps |
| 64-QAM r1/2, 85 ms | 156 / 156 | 33.8 kbps |
| 16-QAM r3/4, 85 ms | 155 / 156 | 33.6 kbps |
| 256-QAM r1/2, 85 ms | 125 / 208 | 27.1 kbps |
| 64-QAM r3/4, 85 ms | 38 / 234 | 8.2 kbps |
| 64-QAM r1/2, 43 ms | 140 / 174 | 30.7 kbps |
| 16-QAM r1/2, 43 ms | 116 / 116 | 25.4 kbps |
| 16-QAM r1/2, 43 ms, NFFT 8192 | 104 / 104 | 24.8 kbps |
| 64-QAM r3/4, 43 ms | 0 / 262 | 0 |
| 16-QAM r1/2, 21 ms, NFFT 8192 | 29 / 116 | 6.9 kbps |
| 64-QAM r1/2, 21 ms | 2 / 174 | 0.5 kbps |
| 16-QAM r1/2, 16 ms, NFFT 4096 | 0 / 110 | 0 |

So a 43 ms guard, which decoded nothing full-band in any earlier record, carries
16-QAM in full with two microphones; 21 ms and below still does not work.

## 4. Two-way ranging

`rangefind.py` plays rising chirps (3–16 kHz, 40 ms, every 0.37 s) from the Mac and
falling chirps (every 0.5 s) from the phone while both record. For a pair of chirps,
the gap between them as heard on the Mac minus the same gap as heard on the phone is
twice the flight time; neither device's start time or the clock offset enters
(Peng et al., "BeepBeep", SenSys 2007).

| Phone microphone | Run 1 | Run 2 | Run 3 | Pairs per run | Spread within a run |
|---|---|---|---|---|---|
| Channel 1 | 93.5 cm | 93.4 cm | 93.4 cm | 4, 3, 5 | 0.2 to 1.9 mm |
| Channel 0 | 99.7 cm | 99.6 cm | 99.6 cm | 5, 3, 4 | 0.1 to 1.8 mm |

Limits on what that number is:

- It is the mean of two acoustic paths (Mac speaker to phone microphone, phone speaker
  to Mac microphone), not a case-to-case distance.
- It adds half the sum of each device's speaker-to-own-microphone distance, **assumed**
  at 5 cm (Mac) and 2 cm (phone) and not measured. A combined uncertainty of about
  ±4 cm is a judgment, not a measurement. The two channels differ by 6.2 cm, which is
  the right order for two microphones at opposite ends of a phone.
- Speed of sound was taken at 20 °C; room temperature was not measured (1 °C is
  about 0.17%, 1.6 mm here).
- No tape measure was put to it. The session's nominal "about 2.5 ft" is 76 cm.
- On channel 0 the direct sound is weaker than later reflections, so its arrival is
  found by looking up to 30 ms before the strongest peak for the first peak at half its
  height. On generated recordings this recovers 10 cm to 2 m within 1 cm, including
  with a stronger reflection 1.5 ms late and a 28 ppm clock offset.

Getting this to run needed a fix to the Android HIL app: its simultaneous
play-and-record command (`playrec_pcm`) required the output track to report
`STATE_INITIALIZED` before its buffer was written, but a static track reports
`STATE_NO_STATIC_DATA` until then, so the command failed every time
(`playrec_pcm failed: … init record=1 track=2`). The check now rejects only an
uninitialized track. The fixed debug build was installed on the Pixel. This is also
the capture primitive the stopped Rank 11 path-matrix acquisition depends on.

## 5. The two-speaker path matrix

`speaker_matrix.py` plays one stereo buffer: a 1 s sweep on the left speaker, then the
right, then both, then the left again; the phone records both microphones. The clock
correction is the value that makes the repeated left sweep agree with the first
(+27.25 ppm here). Two checks then passed on both microphones: the "both" sweep equals
left plus right to within 2% of its energy, and the repeated left equals the first to
within 3%.

A second recording established what the ladder's mono playback does: its response
matches "both speakers, same signal" to within 0.5% of energy on both microphones (and
differs from left-only or right-only by 22 to 46%). **Every downlink in this project's
garage sessions has been both speakers playing the same signal.**

| | Mic 0 | Mic 1 |
|---|---|---|
| Right speaker, mean power relative to left | +0.7 dB | +1.2 dB |
| Both, same signal | +2.9 dB | +3.4 dB |
| Both, phase aligned per frequency for this microphone | +6.0 dB | +6.2 dB |
| Bins where same-signal is 6 dB or more below the better single speaker | 10.2% | 10.9% |
| Bins more than 10 dB below the median: left / right / same / aligned | 7.2 / 7.7 / 6.9 / 2.2% | 7.7 / 7.9 / 7.8 / 2.7% |

Playing the same signal on both speakers gains the 3 dB of doubled power and nothing
more, and in about a tenth of the band it is worse than one speaker. Phase alignment
would add about 3 dB on top and cut the deep fades by about two thirds. Alignment
needs the receiver to report the channel to the transmitter; this was not built.

Shannon bounds, in bits per bin averaged over 500–22800 Hz. Noise is **assumed** equal
and white on both microphones, set so the median bin from the left speaker to mic 0
has 16 dB (the afternoon's measured median). Every arrangement with both speakers has
the same budget, each speaker at full amplitude: the same signal on both, one stream
with the right speaker's phase turned per frequency, or one independent stream per
speaker. These are ceilings for comparing arrangements, not predictions of decoder
rates.

| Arrangement | Bits per bin |
|---|---|
| Left speaker only, mic 0 / mic 1 / both mics | 5.16 / 6.33 / 7.22 |
| Both speakers same signal, mic 0 / mic 1 / both mics | 6.05 / 7.36 / 8.23 |
| Both speakers, phase aligned per frequency, both mics | 9.29 |
| Two independent streams, one per speaker, both mics | 13.15 |

Phase alignment is about 13% above today's arrangement with both microphones, and two
streams about 60% above it, not double;
the median ratio between the matrix's two singular values is 10.8 dB. One measurement,
one position. A 2x2 link would also need per-speaker training symbols and a joint
detector, neither of which exists.

## 6. Speaker arrangements compared by decoded blocks

Added about 23:10 to 23:40, same position. `ladder.py --speakers` sends each cell
through a stereo stream with a named arrangement of the Mac's two speakers: both with
the same signal (what the mono stream does), left only, right only, or both with the
right delayed by 8 samples (0.17 ms). Arrangements alternate within each repeat. Cells
were chosen to be marginal, so that a difference shows. Blind receiver, both phone
microphones, the lower-pilot-EVM arrangement of the two; three runs each.

| Cell | Both, same signal | Left only | Right only | Right delayed |
|---|---|---|---|---|
| 256-QAM r2/3, 171 ms guard | 428 / 624 (140, 148, 140) | 405 (121, 133, 151) | **531** (172, 169, 190) | 484 (144, 165, 175) |
| 64-QAM r1/2, 43 ms guard | 391 / 522 (117, 135, 139) | 356 (111, 121, 124) | **497** (166, 168, 163) | 449 (152, 148, 149) |
| 256-QAM r3/4, 171 ms guard | 57 / 702 (17, 23, 17) | not run | **311** (89, 134, 88) | not run |
| 64-QAM r3/4, 85 ms guard | 123 / 702 (28, 47, 48) | not run | **339** (137, 107, 95) | not run |

The right speaker alone decoded more blocks than both speakers with the same signal in
all twelve paired runs, while radiating half the acoustic power. Delaying one speaker
by 8 samples also beat the same signal in all six of its paired runs. The left speaker
alone did not: it was below the same signal in five of six. So in this placement the
same signal on both speakers is worse than the right speaker alone and worse than a
delayed pair, which agrees with section 5's measurement that it gains only the doubled
power and cancels in about a tenth of the band.

Verified rates on these marginal cells with the right speaker alone were 39.8 kbps
(256-QAM r2/3, 85% of blocks) and 36.3 kbps (64-QAM at a 43 ms guard, 95% of blocks).
Neither is as reliable as section 3's 64-QAM r3/4 at 39.0 kbps and 99.7% of blocks,
which was sent with both speakers and was not repeated with the right alone.

What this does not show: that the right speaker is better in general. Which speaker is
closer to or better aimed at the phone is a property of this placement. Each cell is
three runs. Transmit level was not equalised between one speaker and two.

## 7. The phone turned end for end (2026-11-11, morning)

In plain terms: the owner turned the phone 180 degrees in the same spot, so its bottom
edge faced the laptop, and unplugged its cable. The two microphones swapped which was
nearer, as expected, and the settings that decoded almost every block the night before
now decoded about a fifth. A heater was running and the laptop fan came on during the
run, so this is not a clean test of pose.

| Measurement | Evening, top edge toward laptop | Morning, rotated |
|---|---|---|
| Range, microphone 0 (bottom) / 1 (top) | 99.6 / 93.4 cm | 95.1 / 102.1 cm (three runs) |
| Direct sound, microphone 0 / 1 | -51.4 / -40.4 dB | -47.3 / -47.6 dB |
| In-band signal in the ladder, 0 / 1 | -54.6 / -49.4 dB | -54.4 / -51.5 dB |
| In-band noise before the frame, 0 / 1 | -90 / -89.5 dB | -85 to -90 / -84 to -89 dB |
| 64-QAM r3/4, both microphones | 867/870 | 169/870 |
| 256-QAM r1/2, both microphones | 778/780 | 197/780 |
| Either microphone alone, both cells | 0 to 220 | 0 |
| Lowest pilot EVM per run, both microphones | 0.09 to 0.11 | 0.12 to 0.40 |

Five runs per cell, same transmit settings (amplitude 0.5, Mac volume 56%, phone media
volume 25). The other ten cells of the set decoded 0 to 100 blocks each. One 64-QAM run
decoded 166/174 and one 256-QAM run 114/156; the rest were near zero.

What can and cannot be said:

- The nearer microphone follows the nearer end. The ranges swapped, and the 11 dB
  direct-sound gap between microphones closed to nothing. This supports the owner's
  rule that neither microphone is to be assumed better.
- The decode loss began with the first run at 10:29, ten minutes before the laptop
  fan came on, so the fan did not cause the early failures. This direction is laptop
  to phone, so the fan is not near the listener.
- Louder steady noise does not by itself explain it. Signal over noise was still 31
  to 37 dB, and the quietest run (noise -89.6 / -88.8 dB) decoded 0/156 while a
  noisier one decoded 114/156.
- Not separated: the pose, the heater (noise, and moving air that could change the
  path during a frame; untested), and the phone running on battery. The estimated
  clock offset read 28.5 to 30.6 ppm against 27.1 to 28.4 the evening before.

To separate them: repeat the two cells in this pose with the heater off, then turn
the phone back and repeat with the heater on. Captures:
`ladder-desk-rotated-repeat6-down-20261111T102846`, `rangefind-desk-rotated-20261111T102530`,
`crosscal-desk-rotated-run1-20261111T102735`, `speakermatrix-desk-rotated-20261111T102757`.

The same morning a Homebrew update removed Python 3.14 while the ladder was running.
The run completed; `.venv` was repointed at Python 3.15.2 and its packages reinstalled
(numpy 2.5.4, scipy 1.18.1). The garage tests pass on it (107 passed).

## What was built

| File | What it is |
|---|---|
| `scratch/garage/rangefind.py`, `test_rangefind.py` | Two-way ranging and its analysis; `--replay` re-analyses a retained capture |
| `scratch/garage/speaker_matrix.py`, `test_speaker_matrix.py` | The 2x2 path-matrix measurement, its self-checks and summary |
| `scratch/garage/clock_offset.c`, `.py` | Adds the two-microphone clock-corrected decode |
| `scratch/garage/ladder.py` | Keeps every phone microphone, scores each and both combined with the blind receiver, reports the lowest-pilot-EVM choice; fifth and sixth cell sets |
| `scratch/garage/blind_replay.py` | `score` accepts a second microphone |
| `Apps/HIL/android/.../MainActivity.kt` | The `playrec_pcm` state check |

## What did not work, or was not done

- Guards of 21 ms and 16 ms decode little or nothing even with two microphones.
- 256-QAM r3/4 and 64-QAM r3/4 at 85 ms and 43 ms failed.
- The uplink has one microphone path here (the Mac capture is mono), so nothing in
  section 3 applies to it; its best repeated rate is 12.6 kbps, with 15.9 kbps at 82%
  of blocks.
- No sustained transfer, no retransmission, no reverse-control channel; two-frame bursts.
- Nothing moved, nothing was handheld, no second position or device pair.
- The range reading was not checked against a tape measure, and the self-distances are
  assumed.
- Which physical microphone is channel 0 was not established, nor why its direct path
  is so weak in this placement; it may be specific to this placement.
- Noise was not measured per microphone for the Shannon bounds.
- For the two-speaker findings, single-speaker and delayed arrangements were compared
  by decoded blocks (section 6), three runs each on marginal cells. Phase alignment
  with feedback and two-stream transmission were not built. The best reliable cell was
  not re-run with the right speaker alone.
- The first version of the Shannon table gave the phase-aligned and two-stream rows
  half the transmit power of the same-signal row, understating them (8.44 and 11.27);
  review caught it and the table above uses one budget throughout.
- The first ranging attempt and the first speaker-matrix analysis both produced wrong
  output before their self-checks caught it (chirp pairs matched one period apart;
  the sweep anchor on the loudest instead of the first sweep). The retained captures
  were re-analysed after the fixes; the tests cover both cases.
- The Android fix was built and exercised on the Pixel; no Android unit test covers it.
- Decoding cost was not measured.

## Where this points

Proposed by the agent that ran the session; not yet reviewed.

1. **Two-microphone combining belongs in any off-contact receiver**, with the
   synchronization microphone chosen by pilot EVM. It is the largest gain of the day
   after the pilot fit.
2. **Stop playing the same signal on both speakers.** Section 6 shows one speaker, or
   a small delay on one, decoding more than both together here. Which speaker is
   placement-specific, so the choice should come from a measurement such as section 5's
   or from pilot EVM on a probe frame. Re-run the best reliable cells that way. Phase
   alignment with feedback is next; two streams last.
3. **Use ranging as an input to configuration**: at a known distance the guard and
   band could be chosen before the first data frame. Check it against a tape measure
   and measure the self-distances first.
4. The standing items: promote the pilot fit (owner's decision), replay the near-field
   campaign with it, and repeat at other distances and handheld.

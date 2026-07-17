# iOS HIL: Bulk PHY parity + iPhone 17 Pro Max OTA results

Fresh as of 2026-06-10. Status: audible-band bulk PHY brought to parity with
the Android HIL app and measured over the air on an iPhone 17 Pro Max
(iPhone18,2) tethered to a MacBook Pro M4. Companion to
[ACOUSTIC_BULK_PHY.md](ACOUSTIC_BULK_PHY.md) (Pixel 7a results) and
[ULTRASONIC_BAND.md](ULTRASONIC_BAND.md) (inaudible-band findings).

Code: `Apps/HIL/iOS/App/{BulkDemod,IOSAudio,HILAutomation}.swift`,
`Apps/HIL/iOS/App/CyrinxHILiOSApp.swift`; harness
`scratch/hw20k/{ios_harness,ios_ota_test,ios_phase_coherence}.py`;
validator `scratch/hw20k/validate_swift_decode.swift`.

## 1. Headline measured results

Physical setup: iPhone 17 Pro Max face-up on a soft cloth on the MacBook Pro
left palm rest (same geometry as the Pixel 7a runs); both devices at maximum
volume; normal office ambient. 48 kHz PCM16, NFFT 2048, CP 768, 16-QAM rate
3/4, 5 frames per direction (21.0 s span each).

| Direction | Band | Decoder | Frame-attributed blocks verified | Goodput |
|---|---|---|---|---|
| Mac → iPhone | 1.1–23 kHz | **on the iPhone** (BulkDemod.swift) | 375/375 (96,000 B) | **36.57 kbps** |
| iPhone → Mac | 0.6–11 kHz | on the Mac (modem.py) | 173/175 (44,288 B) | **16.87 kbps** |

Goodput uses frame-attributed position verification: each counted block is
CRC32-valid **and** byte-identical to the transmitted DetRng PRBS at the same
position within the uniquely attributed frame (block j == payload position j),
divided by the span
from the first frame's chirp to the last frame's final data sample. Preambles,
sync symbols, pilots, FEC, CRCs, and inter-frame gaps all count against it.
The historical campaign did not additionally bind that attributed frame to a
scheduled-stream slot, so it is not silently relabeled as the later Cyrinx 2.0
strict scheduled-slot contract.

The Mac→iPhone number matches the Pixel 7a (36.571 kbps) almost exactly — the
iPhone mic + Mac left speaker in this geometry behave equivalently. The
iPhone→Mac number is lower than the Pixel's 27.3 kbps because the iPhone
speaker is band-limited (see §4): its usable coherent band is roughly half as
wide (≈11 kHz vs ≈17 kHz).

Reproduce:
```
.venv/bin/python3 scratch/hw20k/ios_ota_test.py m2i   # on-device decode
.venv/bin/python3 scratch/hw20k/ios_ota_test.py i2m   # Mac-side decode
```

## 2. Device-control mechanism (the iOS analog of `adb am`)

iOS has no `adb am`, so the app is driven over USB with **devicectl + process
environment variables**:

1. The Mac harness launches the app with a JSON env dictionary:
   `xcrun devicectl device process launch --terminate-existing
   --environment-variables '{"CYRINX_CMD":"rec_pcm", ...}' <bundle-id>`.
2. On launch the app reads `CYRINX_*` from `ProcessInfo.environment`
   (`HILAutomation.runIfRequested`), runs exactly one primitive on a background
   queue, and writes the capture PCM + a `result.json` summary into its
   Documents container.
3. The harness pushes/pulls files with
   `xcrun devicectl device copy {to,from} --domain-type appDataContainer
   --domain-identifier <bundle-id> --source Documents/<name>`. File sharing is
   enabled via `UIFileSharingEnabled` + `LSSupportsOpeningDocumentsInPlace`.
4. The harness polls `result.json` for `ok:true` and the expected `done` log
   line, then pulls the PCM.

Primitives (`CYRINX_CMD`): `rec_pcm`, `play_pcm`, `bulk_decode`, `noop`. See
the header of `HILAutomation.swift` for the full env-key contract.

Console logs are *also* emitted via `NSLog`/`print`, but the `result.json` +
embedded `log` array is the reliable scriptable channel; we do not depend on
`devicectl device console`.

### Gotchas discovered
- **The iPhone must be awake/unlocked.** `devicectl process launch` is denied
  with `FBSOpenApplicationServiceErrorDomain error 1 / Locked` on a locked
  device, and iOS silences mic capture / throttles audio render when the app is
  not foreground — the analog of Android's top-visibility mic-silencing. The
  app sets `isIdleTimerDisabled = true` and shows a foreground status view
  during automation; the operator must keep the screen unlocked.
- **`devicectl` has no file-delete.** To avoid reading a stale `result.json`,
  the harness overwrites it with a `{"ok":false,"stale":true}` placeholder
  (`ios_clear_result`) before each command.
- Capture uses `AVAudioSession` category `.playAndRecord`, **mode
  `.measurement`** (disables AGC/EQ/processing — the iOS analog of Android
  `UNPROCESSED`; AGC/NS would wreck QAM). Measured hardware input rate is
  **48 kHz** on this device; the tap output is converted to the requested
  rate/channels via `AVAudioConverter`. Playback uses `.playback` +
  `overrideOutputAudioPort(.speaker)`.

## 3. Swift decoder is bit-compatible with the Python reference

`BulkDemod.swift` is a faithful port of the modem.py RX path (and its Kotlin
sibling), validated before trusting any OTA number — exactly how the Kotlin
port was validated. On an **identical** digital-loopback capture
(`/tmp/ref_capture.pcm`, generated by modem.py, mild AWGN):

| Decoder | blocks verified | EVM |
|---|---|---|
| Python `modem.demodulate_frame` | 75/75 ordered | 0.0223 |
| Swift `BulkDemod.decodeCapture` | 75/75 ordered | 0.0223 |

Identical to 4 decimal places, confirming the FFT (numpy-irfft conventions),
splitmix64 `DetRng`, Gray 16-QAM max-log LLR, frame-wide deinterleave,
depuncture, K=7 (171,133) Viterbi traceback, and CRC32 all match. Reproduce:
```
.venv/bin/python3 scratch/hw20k/validate_swift_decode.swift   # see header for swiftc cmd
```
(The validator links `BulkDemod.swift` directly — it is pure Foundation, no
UIKit/AVFoundation — so the exact source the app ships is what gets checked.)

Note: the Swift port uses **within-frame positional** verification (block j ==
payload position j of the attributed frame), tightening the looser
set-membership check in the original Kotlin port. It still does not establish
the later referee's scheduled-stream slot identity.

## 4. iPhone speaker is the binding constraint (audible roll-off + ultrasonic incoherence)

The iPhone 17 Pro Max **microphone** is excellent (Mac→iPhone hits the same
36.6 kbps as the Pixel). The **speaker** is the bottleneck in both bands.

**Audible roll-off.** Spectral probe of an iPhone→Mac capture (data region,
0.6–17 kHz drive):

| Band | rx level |
|---|---|
| 0.6–2 kHz | +2.3 dB |
| 2–6 kHz | +5.5 dB |
| 6–10 kHz | −2.7 dB |
| 10–14 kHz | −18.4 dB |
| 14–17 kHz | −33.3 dB |

Driving the full 0.6–17 kHz band (the Pixel's profile) collapses the link
(EVM 0.3–0.9, ~32% of blocks). Confining to **0.6–11 kHz** — where the
transducer radiates coherently — gives EVM ~0.1 and 173/175 blocks. This is a
transducer limit, not a code bug.

**Ultrasonic phase incoherence (decisive measurement, mirrors
ULTRASONIC_BAND.md).** Single-tone, 2 s, iPhone speaker → Mac mic, 96 kHz,
STFT phase of the fundamental detrended, std reported:

| Tone | Drive amp | Phase-jitter std | rx_rms | Interpretation |
|---|---|---|---|---|
| 8 kHz | 0.9 | 0.006 rad | 0.065 | clean, coherent |
| 19.5 kHz | 0.9 | 9.9 rad | 0.009 | INCOHERENT |
| 19.5 kHz | 0.4 | 11.5 rad | 0.009 | INCOHERENT |
| 22 kHz | 0.9 | 31.4 rad | 0.010 | INCOHERENT |
| 22 kHz | 0.4 | 25.5 rad | 0.009 | INCOHERENT |

The iPhone 17 Pro Max speaker radiates ultrasonic *power* (~7× weaker than at
8 kHz) but with scrambled phase — even more uniformly incoherent than the
Pixel 7a (which was intermittent at 19.5 kHz). So, like the Pixel, an
inaudible (≥18 kHz) **uplink from the iPhone** cannot carry coherent OFDM/QAM;
it would need a non-coherent (energy/MFSK/OOK) modulation at much lower rate.
The Mac→iPhone inaudible *downlink* should work (MacBook tweeter is coherent to
24 kHz, iPhone mic captures it), but was not separately re-measured here; the
audible Mac→iPhone link already exercises bins to 23 kHz at EVM ~0.11.

Reproduce: `.venv/bin/python3 scratch/hw20k/ios_phase_coherence.py`

## 5. Status and limitations

Measured experiment, one device pair, one geometry. The iOS bulk PHY is not
integrated into the public Swift/C transport API; receive is on-device batch
decode (no streaming RX); MCS is open-loop. The iPhone→Mac band was tuned by
hand to the transducer (0.6–11 kHz); a closed-loop per-bin SNR survey would set
it automatically. The optional inaudible Mac→iPhone downlink was not separately
measured.

# Replay the November 22 file transfer experiment

The six bench transfers sent the same 65,536-byte file to the Pixel and Moto with up to one, two or four data frames per cumulative acoustic ACK. Each forward recording is signed 16-bit little-endian stereo PCM at 48 kHz. The return recordings are mono float32 from the Mac. The campaign-specific source and original ledgers are in the local archive. From the repository root on macOS, with Clang, NumPy and SciPy available, run

```sh
.venv/bin/python scratch/garage/file_transfer/replay.py \
  ../cyrinx-artifacts/garage/ack-schedules-20261122-a \
  --report artifacts/bench/ack-spacing-replay.json
```

The script builds host copies of the two research programs used on Android. For each timed turn, the splitter acquires the first chirp and extracts the scheduled frame windows. The receiver accumulates the file and renders the cumulative ACK. The verifier decodes that synthesized ACK and the separately recorded return signal. It checks the sender's retries and ACK decisions, compares every successfully received bitmap with the reconstructed phone state, and checks the final file byte for byte against the source and its on-device digest. Elapsed time comes from the hardware ledger. Offline processing time contributes no transferred bytes.

One additional offline receiver experiment considers two consecutive failed ACK recordings when the sender retried exactly the same chunk indices. It aligns both windows by their chirps and supplies them to the existing two-input research decoder as independent observations. Its candidate must carry the matching session token and a CRC-valid bitmap consistent with the sender's known and outstanding chunks. The reconstructed receiver state independently scores its result. The report also counts what the combiner produces on adjacent captures with known different receiver states. These checks use the original sender schedule throughout. A new live policy that changes the schedule requires hardware measurement.

The native utilities are short-lived bench executables. Each replay uses a fresh temporary working directory. Their fixed frame, file, block and preamble sizes belong to this experiment.

## Repeat sweep and controlled acoustic losses

The repeat campaign records which single or combined ACK actually advanced the sender. Replay checks that decision, the retry list, file digest and receiver state on every turn. Its two tested spacings can be selected explicitly.

```sh
.venv/bin/python scratch/garage/file_transfer/replay.py \
  ../cyrinx-artifacts/garage/ack-repeat-20261122-a --batches 1 4 \
  --report artifacts/bench/ack-repeat-replay.json
.venv/bin/python scratch/garage/file_transfer/control_probe.py --digital
.venv/bin/python scratch/garage/file_transfer/control_probe.py \
  --replay ../cyrinx-artifacts/garage/ack-repeat-20261122-a/controls
```

For a new controlled acoustic trial, use `control_probe.py --record NEW_DIRECTORY --serial SERIAL_A --serial SERIAL_B`. The phones play test ACKs to the Mac. The second and third frames in the sequence have complementary frequency losses applied to their training and data. One pair changes its cumulative state, one pair has a stale session token, and two slots contain silence. The report includes each single decode and the combined result. A positive control carries the complete test bitmap without an imposed loss.

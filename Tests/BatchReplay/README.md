# Batch replay on macOS and Android

The runner calls the public C batch decoder directly, through the shipping Swift BatchPHY wrapper, and through Kotlin/JNI. Swift uses an internal initializer solely to load the test geometries. Registered profiles also have a public lookup by ID. The Kotlin process loads a host JNI library and runs with JNI checks enabled. All three receive the same little-endian float32 capture. The C decoder owns synchronization, receiver selection and block validity. An optional NDK lane runs the same Kotlin probe under ART on each named arm64 device. The host and Android JNI lanes also encode and decode a seeded payload on each of the five registered profiles.

From the repository root on macOS, with Clang, Swift, JDK 21 and Kotlin installed, run the public fixtures with the project virtual environment. Set `KOTLINC` to the compiler executable if it is outside PATH and the usual Android Studio location.

```sh
.venv/bin/python scripts/check-batch-replay.py --digital-only
.venv/bin/python scripts/check-batch-replay.py
```

The full run requires NumPy in the virtual environment and the original local recordings. It looks for them in `$CYRINX_GARAGE_ARTIFACTS`, then `~/dev/cyrinx-artifacts/garage`, as the capture tools do; `--archive` names another directory. Its inputs and frozen expectations are in `Tests/Fixtures/conformance/november-batch.json`. Both commands write `artifacts/conformance/november-batch.json`. Use `--report` for distinct output paths. A requested recording that is absent, a digest mismatch, a binding failure or an unexpected decision fails the run. So does a list in which a case lacks its own name, names both a checkout file and a recording, or carries a field its source does not use, such as a dropout on a recording or a research expectation on a profile other than the control geometry. The report's tier says whether any recording was read. CI runs the digital subset.

## Android runtime

On the Mac bench, set ANDROID_HOME to the installed SDK or use the default ~/Library/Android/sdk. The device lane uses NDK 27.2.12479018, CMake 4.1.2, SDK platform 35 and Build Tools 37.0.0. ANDROID_NDK_HOME, CYRINX_ANDROID_CMAKE and CYRINX_ANDROID_BUILD_TOOLS override their respective selections.

```sh
.venv/bin/python scripts/check-batch-replay.py --android SERIAL_A --android SERIAL_B
```

Each named device receives a temporary DEX archive, the arm64 JNI library and one fixture at a time. ART runs the Kotlin entry point under the adb shell account. The runner compares its binary result against the direct host C result and records the device fingerprint. It removes its temporary files after the run. The NDK lane uses KISS. The JNI binding uses Java-owned arrays and holds native pointers only for the duration of a batch call. Invalid profile IDs and layouts raise IllegalArgumentException. Native decode failures raise IllegalStateException. This probe exercises Android runtime interoperability without invoking microphone or speaker APIs.

## What each binding reports

Every binding writes one record per decode, laid out in `Packet.h`: the status, the profile identity, the selected receiver and reason, total and valid block counts, clipping and non-finite evidence counts, payload bytes produced, logical samples decoded, the EVM and the two pilot residuals, then the ordered mask and the payload. A capture the decoder rejects is a result too: its record is the status alone.

The runner compares each record with the list field by field. Status, identity, decisions, counts, the mask and the valid bytes compare exactly. The three floating-point metrics compare against a value and a tolerance declared for that fixture. A metric the decoder reports as not evaluated is written as `inf` and must match as such. Bindings that share one C build must also produce byte-identical records. The Android lane and the sanitized build are separate builds, so they are held to the same exact decisions and to the metric tolerances.

On the KISS lane the direct C probe is also built with AddressSanitizer and UndefinedBehaviorSanitizer and run on every fixture. A report from either ends the run.

Three generated faults sit beside the dropout case: a capture cut shorter than one frame, which every binding must reject with the invalid-argument status; a capture holding one not-a-number sample, counted as non-finite evidence; and a capture overdriven eightfold and clamped, counted as clipping evidence.

## Negative controls

`check-negative-controls.py` changes one field of the Swift record for the dropout fixture, five times: a payload byte, a mask position, a count, an identity byte, and the EVM by more than its tolerance. Each run must fail and name the backend, binding, fixture and field. `check-mask-order.py` reverses the mask inside the Kotlin source instead, and `check-manifest-validation.py` covers malformed lists.

## Regenerating expectations

`--observe` records what every lane reports without comparing it. Counts and metric values in the list were taken from the KISS direct C record of such a run on 2026-11-22, across the host builds, the sanitized build and both phones. The widest difference in any metric between those builds was 2e-12, and each tolerance is 1e-9. Decisions are never taken from the decoder: a new mask must be checked against the transmitter's bytes, as the generated faults were against the golden payload.

## Reading the results

The runner checks profile identity, receiver selection, selection reason, ordered CRC mask and the SHA-256 of the concatenated valid 256-byte blocks against fixed expectations. Within each FFT backend it also compares all returned payload bytes across bindings, including bytes in invalid slots. Mask strings are in payload order, with the first slot on the left. Comparing only counts would miss a reversed or shifted mask. Broader device coverage and streaming remain open.

The digital inputs come from the existing independent golden-vector generator. The dropout case sets the first eight samples of each 512-sample period to zero, including in the preamble. The two-microphone cases align the golden channels to their common length and interleave them. They exercise complementary microphone frequency responses in both orders and two copies of the primary microphone. The duplicate case should select the primary and recover zero blocks. The complementary pair should select maximum-ratio combining and recover all four.

The two November 10 windows come from the Pixel downlink desk capture at 48 kHz. The manifest pins the original NPZ, array key, zero-based start, length and the exact float32 window. The source bytes came from the ladder's NumPy generator with seed 106, with consecutive draws for the two frames. Expected valid-byte hashes were checked against those original source blocks. The test geometries live in Profiles.h and use private fixture IDs.

The November 22 windows are the first control frames of the Pixel and Moto reverse-link probes recorded by the Mac microphone. The windows include 3,072 samples before the acquired chirp. The source and PCM hashes capture the conversion from the recorded NumPy array to little-endian float32. The transmitted payloads use the original probe seeds 221100 and 221110. Each window has two expected outcomes. The shipped batch decoder recovers zero blocks and the separate research C decoder with blind clock correction and coherent pilot fitting recovers all four. The verifier supplies expected bytes only after decoding. The research results use KISS and are reported separately from cross-binding parity.

To change a fixture, retain its prior manifest, document the capture or digital transformation, and independently verify the new mask's valid bytes against the transmitter. Comparing several bindings to one another alone cannot establish that the decoder recovered the right data.

# Batch replay on macOS and Android

The runner calls the public C batch decoder directly, through the shipping Swift BatchPHY wrapper, and through Kotlin/JNI. Swift uses an internal initializer solely to load the test geometries. Registered profiles also have a public lookup by ID. The Kotlin process loads a host JNI library and runs with JNI checks enabled. All three receive the same little-endian float32 capture. The C decoder owns synchronization, receiver selection and block validity. An optional NDK lane runs the same Kotlin probe under ART on each named arm64 device. The host and Android JNI lanes also encode and decode a seeded payload on each of the five registered profiles.

From the repository root on macOS, with Clang, Swift, JDK 21 and Kotlin installed, run the public fixtures with the project virtual environment. Set `KOTLINC` to the compiler executable if it is outside PATH and the usual Android Studio location.

```sh
.venv/bin/python scripts/check-batch-replay.py --digital-only
.venv/bin/python scripts/check-batch-replay.py --archive ../cyrinx-artifacts/garage
```

The full run requires NumPy in the virtual environment and the original local recordings. Its inputs and frozen expectations are in `Tests/Fixtures/conformance/november-batch.json`. Both commands write `artifacts/conformance/november-batch.json`. Use `--report` for distinct output paths. A requested recording that is absent, a digest mismatch, a binding failure or an unexpected decision fails the run. CI runs the digital subset.

## Android runtime

On the Mac bench, set ANDROID_HOME to the installed SDK or use the default ~/Library/Android/sdk. The device lane uses NDK 27.2.12479018, CMake 4.1.2, SDK platform 35 and Build Tools 37.0.0. ANDROID_NDK_HOME, CYRINX_ANDROID_CMAKE and CYRINX_ANDROID_BUILD_TOOLS override their respective selections.

```sh
.venv/bin/python scripts/check-batch-replay.py --android SERIAL_A --android SERIAL_B
```

Each named device receives a temporary DEX archive, the arm64 JNI library and one fixture at a time. ART runs the Kotlin entry point under the adb shell account. The runner compares its binary result against the direct host C result and records the device fingerprint. It removes its temporary files after the run. The NDK lane uses KISS. The JNI binding uses Java-owned arrays and holds native pointers only for the duration of a batch call. Invalid profile IDs and layouts raise IllegalArgumentException. Native decode failures raise IllegalStateException. This probe exercises Android runtime interoperability without invoking microphone or speaker APIs.

## Reading the results

The runner checks profile identity, receiver selection, selection reason, ordered CRC mask and the SHA-256 of the concatenated valid 256-byte blocks against fixed expectations. Within each FFT backend it also compares all returned payload bytes across bindings, including bytes in invalid slots. Mask strings are in payload order, with the first slot on the left. Comparing only counts would miss a reversed or shifted mask. This check covers discrete outputs. Field-by-field metric tolerances, broader device coverage and streaming remain open.

The digital inputs come from the existing independent golden-vector generator. The dropout case sets the first eight samples of each 512-sample period to zero, including in the preamble. The two-microphone cases align the golden channels to their common length and interleave them. They exercise complementary microphone frequency responses in both orders and two copies of the primary microphone. The duplicate case should select the primary and recover zero blocks. The complementary pair should select maximum-ratio combining and recover all four.

The two November 10 windows come from the Pixel downlink desk capture at 48 kHz. The manifest pins the original NPZ, array key, zero-based start, length and the exact float32 window. The source bytes came from the ladder's NumPy generator with seed 106, with consecutive draws for the two frames. Expected valid-byte hashes were checked against those original source blocks. The test geometries live in Profiles.h and use private fixture IDs.

The November 22 windows are the first control frames of the Pixel and Moto reverse-link probes recorded by the Mac microphone. The windows include 3,072 samples before the acquired chirp. The source and PCM hashes capture the conversion from the recorded NumPy array to little-endian float32. The transmitted payloads use the original probe seeds 221100 and 221110. Each window has two expected outcomes. The shipped batch decoder recovers zero blocks and the separate research C decoder with blind clock correction and coherent pilot fitting recovers all four. The verifier supplies expected bytes only after decoding. The research results use KISS and are reported separately from cross-binding parity.

To change a fixture, retain its prior manifest, document the capture or digital transformation, and independently verify the new mask's valid bytes against the transmitter. Comparing several bindings to one another alone cannot establish that the decoder recovered the right data.

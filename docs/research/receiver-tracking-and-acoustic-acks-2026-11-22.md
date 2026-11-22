# Receiver tracking and acknowledged acoustic file transfers

## In plain English

A Mac sent a different random 64 KiB file to each of two Android phones. Each phone listened, decoded pieces of its file locally, and played a short sound telling the Mac which pieces it had saved. The Mac used the sound it recorded to decide what to send next. A USB-connected harness scheduled the turns. The final file on each phone was checked against the original.

Reprocessing old recordings also showed where two proposed receiver changes help. Estimating changes in room echoes recovered some missing pieces. Using the new estimate on its own lost pieces elsewhere. Keeping verified pieces from the first decode and trying again only on an incomplete frame gave the best result on the archived recordings.

In a follow-up sweep, sending up to four frames before waiting for the phone’s reply gave the fastest verified delivery on both phones. The Pixel took 69 seconds and the Moto took 117 seconds for the same 64 KiB file. Reversing the order of the one- and four-frame settings reproduced the advantage on both phones. In a separate physical test, combining two individually unreadable replies recovered the correct bitmap on each return path.

## Receiver experiments

The reference was the research C decoder with blind clock correction and coherent pilot phase fitting. All 62 reference frame scores reproduced the November 17 analysis, including its mono policy for the Moto. The local interpolation candidate aligned each microphone's timing and phase, then interpolated changes in the channel between adjacent pilots. Its interpolation strength came from prediction error on neighboring pilots. The delay-domain candidate used the initial impulse response to distinguish echoes that sparse pilots map to the same delay. It checked its prediction for each pilot using the opposite parity of pilots. Neither estimate uses transmitted data bytes.

| Archived recordings | Scheduled blocks | Reference | Local interpolation | Delay-domain update | Reference plus selective second decode |
|---|---:|---:|---:|---:|---:|
| Evening desk | 870 | 867 | 867 | 869 | 870 |
| Rotated desk | 870 | 166 | 167 | 173 | 173 |
| Behind monitor A, Pixel | 450 | 188 | 189 | 189 | 189 |
| Behind monitor A, Moto | 450 | 94 | 94 | 103 | 115 |
| Behind monitor B, Pixel | 480 | 477 | 477 | 476 | 477 |
| Behind monitor B, Moto | 480 | 281 | 271 | 258 | 290 |

The selective second decode retains the reference's CRC-valid blocks and fills holes from the delay-domain decoder in the same frame window. Conflicting CRC-valid copies would be erased. No such conflicts occurred. Thirty of 62 frames needed the second pass. Fifteen gained blocks, totaling 41 additional byte-verified blocks out of 3,600 scheduled. These captures were already known when the research approaches were chosen. Of the eight rotated frames that originally decoded zero blocks, one gained a single block.

## Reverse link

Both USB phones transmitted at their existing media volumes. The Mac used its built-in microphone. Each phone sent two control-profile frames and two data-profile frames in alternating order.

| Phone → Mac | QPSK rate ½, 0.3–11 kHz | 16-QAM rate ½, 0.3–17 kHz |
|---|---:|---:|
| Pixel 7a | 4/4, 4/4 | 0/28, 25/28 |
| Moto G (2026) | 4/4, 4/4 | 0/28, 0/28 |

Both profiles used a 16,384-point FFT and 8,192-sample cyclic prefix at 48 kHz. The control frame had three data symbols and the data frame six. These probes informed the choice of the return ACK waveform. The devices were the same USB-connected phones as November 17. Distance and orientation were not remeasured on November 22.

## Transfer method

A temporary native arm64 program on each phone ran the existing research receiver and constructed its ACK. The HIL app supplied microphone PCM and played the resulting ACK waveform. The host deployed the receiver, chose the modulation, and scheduled each half-duplex turn over USB. The sender advanced its file state only after decoding a matching cumulative bitmap from the Mac microphone. Each protected block included a session identifier, chunk index, and file length. The independent file integrity check used SHA-256 on the phone after completion.

Each 65,536-byte file required 276 chunks of up to 238 file bytes. The phone used mono when at least 99.9% of sufficiently active PCM samples were identical across the two channels, with a minimum of 4,800 active samples. Otherwise it evaluated single microphones and both combining orders by pilot error. Time began before the first recording request and ended after the final acoustic ACK. Rates include recording margins, Android decoding, USB turn scheduling, retransmissions, return ACKs and host processing. Binary deployment and the final out-of-band digest verification were outside that interval.

A digital-loopback test initially recovered no useful records when most unused data slots contained zeros. Filling unused space with pseudorandom or random bytes allowed the sparse-frame test to pass. The final test also checked cumulative ACKs across partial and duplicate deliveries and rejection of a stale session token. The cause of the zero-fill failure was not isolated.

## Measured file delivery

| Mac → phone | Wall time | Acoustic ACKs decoded | File chunk transmissions | Verified file goodput |
|---|---:|---:|---:|---:|
| Pixel 7a | 126.95 s | 8/8 | 325 for 276 distinct chunks | 4.13 kbps |
| Moto G (2026) | 226.83 s | 14/15 | 303 for 276 distinct chunks | 2.31 kbps |

The Pixel received 9/58 chunks on the opening 64-QAM turn. The controller reduced modulation to 16-QAM and finished in seven more turns. The Moto began with 16-QAM. Its first acoustic ACK was unavailable to the sender, which retried at QPSK. The second ACK reported 33 cumulative chunks. Both files matched their independently generated SHA-256 digests.

Each turn occupied 4.224 s of forward waveform and 2.688 s of return waveform, including preambles, training and cyclic prefixes. Combined waveform durations accounted for 43.6% of the Pixel's elapsed time and 45.7% of the Moto's. A separate on-device repeat of the first three decoder and ACK-generation calls took 1.11–1.25 s per turn on the Pixel and 0.51–0.54 s on the Moto. The remaining time includes recording and playback margins, process and activity control, turn scheduling, and host work. Grouping several frames per ACK and reducing idle turn time are candidates for a paired transfer experiment.

These are single-file, bench-coordinated measurements. The experiment exercised acoustic feedback and Android-native file assembly. Autonomous discovery, over-the-air profile negotiation, and unattended app-to-app operation remain open.

## Replay on the fresh file traffic

The live transfers used the reference receiver. After each file completed, the phone microphone recordings were collected for a comparison using the same fallback algorithm and the same file chunks. The Pixel scored 276/325 with either receiver policy. The Moto went from 288/303 to 292/303, gaining three chunks on its first turn and one on its third. These totals count transmissions, including repeats, so a perfect final file can coexist with missing chunks on individual turns. File-completion time with the fallback enabled is unmeasured.

## Evidence and reproduction

The local acoustic archive under `~/dev/cyrinx-artifacts/garage/` contains `reverse-20261122-a`, `transfer-pixel-20261122-a`, and `transfer-moto-20261122-a`. Each transfer directory includes its source file, independent handset digest, turn ledger, microphone policy log, per-turn receiver PCM, and recorded acoustic ACKs. The source and score kit is `receiver-iteration-20261122.zip` in the same archive. It expands into the repository's artifacts directory structure. Its REPRODUCE.md describes rebuilding the experimental receivers against cba849e and rerunning the comparisons.

The executable and experimental tracker are research tooling. The production receiver and Android app source were unchanged during these runs.

## Fixed-modulation ACK spacing experiment

A second campaign sent the same 65,536-byte file six times. The Mac used 16-QAM rate ½ for every Pixel transmission and QPSK rate ½ for every Moto transmission. The scheduler sent up to one, two or four data frames before listening for one cumulative acoustic ACK. It shortened the last burst when fewer chunks remained. Each data frame occupied 4.224 s, successive data frames had a 250 ms gap, and each return waveform occupied 2.688 s. Power, geometry and waveform durations were held fixed within each phone. The Mac reported 88% output volume, the Pixel 25/25 and the Moto 15/15. The sender used a new session ID and fresh random padding for each file.

The run order was Pixel 1, Moto 4, Pixel 2, Moto 2, Pixel 4, Moto 1. The user described the phones as about two feet away behind the monitor without line of sight. The experiment used one file per phone and spacing, with opposite spacing orders on the two phones. Room and operating-system behavior could vary between trials. The Mac also compiled and ran software checks during some transfers, so host load was uncontrolled. No distance, latency percentile or repeatability claim follows from this sweep.

| Mac → phone | Max frames per ACK | Data frames | Acoustic ACKs decoded | Chunk transmissions | Elapsed seconds | Verified file kbps |
|---|---:|---:|---:|---:|---:|---:|
| Pixel 7a | 1 | 9 | 8/9 | 315 | 174.34 | 3.01 |
| Pixel 7a | 2 | 8 | 4/4 | 276 | 85.04 | 6.17 |
| Pixel 7a | 4 | 8 | 2/2 | 276 | 69.45 | 7.55 |
| Moto G (2026) | 1 | 17 | 15/17 | 305 | 259.95 | 2.02 |
| Moto G (2026) | 2 | 18 | 8/11 | 306 | 265.42 | 1.98 |
| Moto G (2026) | 4 | 15 | 4/4 | 278 | 116.93 | 4.48 |

The four-frame schedule had the highest file goodput on each phone in this sweep. On the Pixel it was 2.51 times the one-frame rate. Both the two- and four-frame Pixel runs sent exactly 276 chunk transmissions in eight data frames. Halving the number of ACK turns from four to two saved 15.58 seconds for that pair. On the Moto the four-frame rate was 2.22 times the one-frame rate. The two-frame Moto run took 265.42 seconds, including three consecutive misses of the final ACK. The reduced number of turns, return-path losses and varying host scheduling times all affect the comparisons.

The archive retained a stereo phone recording and a mono Mac ACK recording for each of 47 turns. Offline reconstruction recovered all six original files, reproduced the successful acoustic bitmaps, and checked the handset file digests. The shared source SHA-256 was `9f4ccd13b731fce86953a8472d6a67043313e13bb02a0598ada0162a27d6e039`. Reproduction uses [the file-transfer replay](../../scratch/garage/file_transfer/README.md) and the local archive `ack-schedules-20261122-a`. Its `sha256.json` digest is `5e46879fcf7359409fba02b99c4d51e5052273003f3064b193593f81c4d72a4a`. The archive includes the physical campaign source and build instructions.

### Receiving a repeated ACK

In the two-frame Moto trial the sender repeatedly sent the last ten chunks after missing the return bitmap. All three missed return recordings contained a chirp peak roughly 0.48–0.49 s after capture began, with just over two seconds of recording remaining after the expected waveform end. Each single-recording decode produced zero valid control blocks. The earlier delay-domain fallback also recovered no ACK from those three captures.

An exploratory offline receiver aligned consecutive failed recordings by their chirps and passed them to the existing two-input maximum-ratio combiner. It only tried pairs for which the sender retried the identical set of chunk indices. A candidate had to pass the CRC, session token and sender bitmap checks. Every candidate was independently scored against the reconstructed phone state. Across the six trials there were 2 eligible pairs, 2 accepted candidates and 2 exact receiver-state matches. The first successful pair on the two-frame Moto run confirmed the file using recordings through turn nine, at 219.64 s elapsed. The live sender recognized completion on turn eleven, 45.78 s later. This difference measures the timestamps of the available evidence. Live runtime with the combiner is unmeasured.

As a separate stress test, the offline runner also combined 35 adjacent pairs whose reconstructed bitmaps differed. Sixteen pairs produced a session-matched ACK. Eight reproduced the earlier bitmap and eight reproduced the later one. All reported chunks were present on the phone. Overlapping pairs from one room provide limited independent evidence. A live follow-up should counterbalance repeated one- and four-frame trials and exercise changing bitmap, stale-session and absent-ACK cases before enabling opportunistic combining in a streaming sender.

## Cross-binding replay

The batch replay runner exercises direct C, Swift C interop and Kotlin/JNI on macOS with both KISS FFT and Accelerate. Nine fixtures produced 54 matching sets of discrete decisions. One retained Pixel desk frame recovered eight of ten blocks with the ordered mask `1111110101`, while the next frame recovered all ten. On the November 22 return path, the shipped decoder recovered zero of four control blocks on each phone's retained first probe. The separate clock-corrected research receiver recovered the four transmitted blocks in each recording. The digital cases also cover periodic dropouts, complementary microphones, swapped inputs and duplicated input. Intentionally reversing the Kotlin mask failed on the dropout fixture even though the recovered block count stayed the same.

The initial check covered host probes. The later repeat below extends it to the public Swift binding and two Android runtimes. [Batch replay instructions](../../Tests/BatchReplay/README.md)

## Counterbalanced repeat and live ACK combining

The repeat used the same 65,536 source bytes, fixed modulation, device positions and volumes as the first sweep. Each phone saw the one- and four-frame settings in the opposite order from its first run. Both Pixel runs sent eight data frames. Increasing the burst length reduced the return turns from eight to two.

| Mac → phone | Max frames per ACK | First sweep kbps | Repeat kbps | Repeat elapsed seconds | Repeat acoustic ACKs |
|---|---:|---:|---:|---:|---:|
| Pixel 7a | 1 | 3.01 | 4.17 | 125.59 | 8/8 |
| Pixel 7a | 4 | 7.55 | 8.23 | 63.67 | 2/2 |
| Moto G (2026) | 1 | 2.02 | 2.17 | 241.22 | 15/16 |
| Moto G (2026) | 4 | 4.48 | 3.64 | 143.84 | 4/5 |

Four frames per ACK was fastest in both orders on both devices. In the repeat it improved the Pixel's file goodput by a factor of 1.97 and the Moto's by 1.68. The first Moto four-frame ACK was missed, so four data frames were repeated before the sender learned the receiver's state. This illustrates the retransmission cost of a longer burst when the return path fails. The repeat enabled combining consecutive failed ACKs for the same retry list. Both misses were isolated. The file transfers completed without invoking the combiner. USB scheduled the turns, host load was uncontrolled, and each phone had two observations at one- and four-frame spacing and one at two-frame spacing.

### Controlled return-path experiment

Each phone played eight scheduled control slots to the Mac. An unmodified ACK carried a bitmap of 254 chunks. For the next two copies, the transmitter attenuated alternating frequency bands on the training and data symbols, reversing the affected bands on the second copy while preserving the acquisition chirp. Three controls used a changed bitmap, a different session token and two silent slots. These were physical speaker-to-microphone trials with deliberately imposed spectral losses. Reference bytes and the scoring schedule were available to the verifier.

| Return condition | Pixel → Mac | Moto → Mac |
|---|---|---|
| Unmodified positive control | Exact 254-chunk bitmap | Exact 254-chunk bitmap |
| Same bitmap, complementary losses | Both individual decodes missed. Combining recovered the exact bitmap. | Both individual decodes missed. Combining recovered the exact bitmap. |
| Changed bitmap, complementary losses | No accepted bitmap | No accepted bitmap |
| Stale session | Stale token decoded, current session rejected it | Stale token decoded, current session rejected it |
| Two silent slots | No accepted bitmap | No accepted bitmap |

The sender had ACK combining enabled during the file trials, but no pair of consecutive misses triggered it. The control probe scored the captured sequences immediately after each playback. These outcomes establish a recoverable loss case and exercise three rejection cases. They do not estimate a false-ACK rate or time saved during live file delivery. Replay reconstructed all four files and reproduced the sender decisions for all 31 turns. The repeat archive is `ack-repeat-20261122-a`. Its 79-entry hash manifest has SHA-256 `84cd2d93528e3cb6ac668fb874ba7a63d009a40666a73b83a325d22ef90aa444`.

### Public batch binding and payload sensitivity

Swift package tests and host and Android JNI tests round-tripped all five registered profiles. Nine fixtures agreed across direct C, the public Swift decoder and host Kotlin/JNI on KISS FFT and Accelerate, and in Kotlin/JNI on both Android devices using KISS. The runner produced 72 cross-binding outcomes plus two separately scored research-decoder outcomes. Android used an NDK-built native library under ART. Streaming callbacks and autonomous acoustic turn scheduling remain open. [Swift migration example](../BATCH_PHY.md)

Empty Swift input encoded to the same waveform as a full, zero-padded input when this was measured. The Swift encoder now rejects a payload shorter than the frame. Digital loopback through the underlying C codec revealed a payload-dependent weakness on registry geometry 1. An all-zero payload recovered 0/75 CRC-valid blocks. Constant bytes 0x01, 0x11, 0x71 and 0xff and a seeded pseudorandom payload each recovered 75/75. This was measured without audio hardware.
A paired experiment then applied a known pseudorandom XOR mask to application bytes before encoding and reversed it after decoding. Across the five registry geometries, each pattern scheduled 560 blocks. With the current codec, all-zero frames, 128-byte random records padded with zeros, and eight random bytes per otherwise-zero block each recovered 0/560. With whitening, each recovered 560/560 original blocks. A constant 0x71 payload and dense pseudorandom data recovered 560/560 with either setting. Every CRC-valid block matched its application input. The run used the shipped C sources, one deterministic mask, and clean digital waveforms. The fixed mask is an experimental data transformation. The following broadcast tests that transformation on hardware. A production design would need a compatible wire version. [Reproducible whitening experiment](../../scratch/garage/payload_whitening.py)

### Paired sparse-record broadcast

Both phones simultaneously recorded a six-frame Mac broadcast with the 4.224 s QPSK research geometry used in the Moto file trials. The Mac output was 88%. Dense pseudorandom controls bracketed two different 128-byte application records. Each record was padded with zeros to 19 blocks and sent twice. The first pair used plain then whitened bytes, and the second pair reversed the order. For the whitened cases the same known mask used in the digital experiment was removed from the decoded payload before verification. The research receiver chose its microphone configuration by pilot error. Each score required a valid CRC and byte agreement with the original block.

| Receiver | Dense controls | Plain padded blocks | Whitened padded blocks | Plain 128-byte records recovered | Whitened 128-byte records recovered |
|---|---:|---:|---:|---:|---:|
| Pixel 7a | 38/38 | 33/38 | 38/38 | 2/2 | 2/2 |
| Moto G (2026) | 38/38 | 2/38 | 37/38 | 0/2 | 2/2 |

The application record fits in the first block. On the Moto, the two CRC-valid plain blocks contained only padding. Both whitened frames recovered their application record. The Pixel already recovered both plain records. The Moto reported a microphone mapping outside the two recorded channel indices, and its active PCM channels met the duplicate-channel threshold on all six frames. The receiver used mono for the Moto. Its physical microphone assignment remains unconfirmed.

This was one short broadcast using predetermined frame windows and two test records. It measures record recovery. It does not measure file goodput with whitening or establish a production scrambler. The local archive `sparse-air-20261122-a` retains both PCM captures, device logs, transmitter schedule, scoring source and block counts. Replaying both recordings reproduced the scores. The score ledger SHA-256 is `953657dae73b133d2cb3f122ca6243a1551850a6c2e4655df2fcb2fdc2ae175d`.

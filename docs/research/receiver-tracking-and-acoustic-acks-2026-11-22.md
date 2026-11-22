# Receiver tracking and acknowledged acoustic file transfers

## In plain English

A Mac sent a different random 64 KiB file to each of two Android phones. Each phone listened, decoded pieces of its file locally, and played a short sound telling the Mac which pieces it had saved. The Mac used the sound it recorded to decide what to send next. A USB-connected harness scheduled the turns. The final file on each phone was checked against the original.

Reprocessing old recordings also showed where two proposed receiver changes help. Estimating changes in room echoes recovered some missing pieces. Using the new estimate on its own lost pieces elsewhere. Keeping verified pieces from the first decode and trying again only on an incomplete frame gave the best result on the archived recordings.

In a follow-up sweep, sending up to four frames before waiting for the phone’s reply gave the fastest verified delivery on both phones. The Pixel took 69 seconds and the Moto took 117 seconds for the same 64 KiB file. Each setting was tried once on each phone. Combining two missed phone replies during offline replay also recovered a correct completion message.

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

The manifest and runner instructions are in [Batch replay](../../Tests/BatchReplay/README.md). This evidence covers the host probes. C3-06 public Swift value types, the C3-07 Android NDK binding and the remaining C3-08 conformance matrix still precede the streaming receiver milestone.

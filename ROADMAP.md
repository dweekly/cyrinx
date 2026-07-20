# Cyrinx Roadmap

Fresh as of 2026-07-18. This is the globally stack-ranked plan for turning the
Cyrinx 2 physical-layer result into a reusable acoustic transport library.
Completed implementation and measurement history belongs in
[CHANGELOG.md](CHANGELOG.md); the currently validated state is summarized in
[README.md](README.md). Supporting plans and evidence live in
[docs/CYRINX_3_PLAN.md](docs/CYRINX_3_PLAN.md),
[docs/PUBLICATION.md](docs/PUBLICATION.md),
[docs/EXPERIMENTS.md](docs/EXPERIMENTS.md), and
[docs/A1_AUTO_MRC.md](docs/A1_AUTO_MRC.md).

`docs/CYRINX_3_PLAN.md` is the PR-sized execution decomposition for Ranks 1
through 7. This roadmap remains authoritative for stack rank, research gates,
and evidence standards.

## Cyrinx 2.0 measured state

The accepted, gap-preserving MacBook Pro-to-Pixel 7a benchmark measured
**65.875457875 kbps mean strict ordered byte-verified scheduled goodput**:
4,215/4,280 decoded blocks (98.4813%). All eight candidate runs beat their
paired controls; the control mean was 44.199 kbps. The exact schedule ceiling
for that 64-QAM rate-2/3 profile is 66.891 kbps, so the current implementation
is already close to the ceiling of that particular MCS and frame schedule. The
result is about 1.80x the schedule-comparable earlier 36.571 kbps benchmark,
although that historical campaign used the weaker expected-set verifier rather
than the current strict ordered-byte oracle.

The faster profile did not meet the conservative control's 99.9667% block
success. It is therefore a measured throughput advance with a resilience cost,
not a universally superior default. A separately labeled zero-gap research
class reached 69.652 kbps but recovered only 90.666% of blocks and is not
comparable to the accepted gap-preserving class.

The flagship evidence is one directional, static, near-field route using one
Mac speaker channel, two Pixel input channels, and host-side batch decoding.
The portable C receiver ships two-input MRC, automatic receiver selection, and
payload-independent pilot-local LLR weighting. It does **not** yet establish a
real-time mobile transport, broad device/room generality, or true 2x2 MIMO.

## Planning contract

### Maturity labels

- **LIBRARY-SHIPPED:** implemented in the canonical public C library, exposed
  through every claimed binding, and covered by deterministic conformance
  tests. This label does not imply broad hardware qualification.
- **BENCH-SPIKE:** implemented only in `scratch/`, a simulator, or a campaign
  harness. It has no stable public API and supports no product claim.
- **PLANNED-SPIKE:** bounded experiment specified below but not yet executed.
- **INTEGRATION-GAP:** the important pieces exist, but the supported runtime
  path does not connect them end to end.
- **QUALIFICATION-GAP:** an integrated implementation exists, but held-out OTA
  evidence is insufficient for default-on status.
- **DEFERRED:** no current allocation. Work starts only after the stated trigger.

Hardware evidence is recorded separately as `QUALIFIED-ON` with the exact
device, route, source, sample rate, pose, distance, level, and environment. A
feature is never called generally qualified because it worked on one bench.

### Spike-before-integration rule

Every high-uncertainty PHY or protocol idea must pass a bounded research spike
before it enters `Sources/CCyrinx`, a public binding, or a default profile. Each
spike must declare, before collecting the deciding data:

1. a falsifiable hypothesis and the incumbent comparison;
2. the exact waveform, power/peak constraints, schedule, cells, and run order;
3. one primary metric and any non-regression constraints;
4. a promotion threshold and a stop condition;
5. training/calibration data separated from held-out evaluation data; and
6. the artifacts to retain: manifest, code/config hashes, route provenance,
   raw PCM where publishable, block map, and analysis output.

Promoted work is reimplemented in the canonical C core, exercised through thin
Swift and Kotlin/JNI bindings, and covered by golden vectors and retained-
capture replay. Python remains an independent research oracle and measurement
referee, not a second shipping modem.

### Benchmark contract

- Keep the 65.875 kbps five-frame/four-gap class intact for like-for-like
  comparisons. Label zero-gap, continuous-streaming, setup-inclusive, and
  distance campaigns as separate classes.
- Primary PHY metric: strict ordered byte-verified scheduled goodput over all
  planned slots. Once the session exists, also report application goodput with
  discovery, sounding, control traffic, retransmission, and recovery included.
- Report median, lower-tail performance (p10 by default, with run/cell-cluster
  uncertainty), range, block success, burst loss,
  setup time, and recovery time. Do not optimize only the best run.
- Change one mechanism at a time. A normal OTA path is a two-pair balanced
  screen followed, only if it passes, by no more than eight preregistered
  balanced confirmatory pairs. A promotion normally requires at least 7/8
  paired wins in addition to its metric/reliability gate.
- Make receiver selection and adaptation decisions from training or held-out
  pilots only. Payload bins, CRC results, and expected payload bytes are
  forbidden inputs to same-frame selection.
- Compare waveforms at matched occupied band and preregistered drive classes.
  Equal digital peak is primary for a PAPR/peak-limited hypothesis; fixed sum
  digital sample power plus per-speaker peaks is primary for MIMO. OS volume
  plus sample amplitude is a digital-drive class, not calibrated acoustic
  power. Report equal per-speaker drive as a separate +3 dB-total diagnostic;
  claim acoustic power only with calibrated instrumentation.
- Randomize or balance candidate order, capture HVAC/room-tone state, record
  clipping and route provenance, and preregister the analysis before the
  confirmatory campaign.
- A candidate normally needs at least a 5% held-out lower-tail goodput gain, or
  equivalent goodput with a separately declared material robustness, latency,
  power, or audibility improvement, to justify new library complexity.
- For session-dependent candidates, report setup-inclusive net goodput for
  declared 64 KiB and 1 MiB transfers so a sounding-heavy scheme cannot hide
  behind an indefinitely amortized rate.
- The historical 36.571 kbps result remains the website-era number Cyrinx 2
  surpassed; new candidates compare against the strongest current control in
  their exact schedule/reliability class, not against that older target.

## Critical path and global stack rank

```text
1 canonical C contract
  -> 2 streaming bulk + bootstrap PHY
     -> 3 on-device audio/bindings
        -> 4 manual-profile burst session/event envelope
           -> 5 sounding/calibration/negotiation
              -> 6 public peer API, adaptation, and selective recovery
                 -> 7 packaged diagnostics and held-out qualification

5a measurement substrate -> 8 SISO spikes -> candidate integration through 6
5a measurement substrate -> 9 N-mic diversity -> qualification through 7
5 + 10 path matrices -> 11 NxM feasibility -> integration through 6–7
7 qualified near-field stack -> 10 separate 3 ft program
4 versioned session -> 13 authenticated/advanced delivery semantics
5 + 7 -> 14 ultrasonic and 15 pleasant-audible research modes
```

| Rank | Outcome | Current maturity | Primary dependency | Promotion result |
|---:|---|---|---|---|
| 1 | One canonical DSP/metrics contract | INTEGRATION-GAP | none | Swift/JNI use the same C bulk PHY |
| 2 | Streaming bulk/bootstrap PHY | PLANNED-SPIKE | 1 | bounded continuous PCM processing |
| 3 | On-device Apple and Android audio paths | INTEGRATION-GAP | 1–2 | no host DSP in the accepted path |
| 4 | Burst session/event envelope | INTEGRATION-GAP | 2–3 | manual-profile best-effort messages |
| 5 | Sounding and negotiation | BENCH-SPIKE | 5a: 1,3; 5c: 4,5a/b | bidirectional measured setup |
| 6 | Public API, adaptation, and ARQ | BENCH-SPIKE | 4–5 | peer transport across changing cells |
| 7 | Packaged held-out qualification | BENCH-SPIKE | 1–6 | evidence-backed SDK defaults |
| 8 | Single-stream waveform improvements | PLANNED-SPIKE | 5 | only promoted wins enter the bulk PHY |
| 9 | N-mic diversity | N=2 LIBRARY-SHIPPED; N>2 PLANNED-SPIKE | 2–5 | verified input arrays |
| 10 | Directional/fixed-3-ft rates | DEFERRED | 5–8 | separate range profiles |
| 11 | NxM beamforming and true MIMO | PLANNED-SPIKE | 3, 5, 7, 10 | conditional spatial gain or stop |
| 12 | Faster non-coherent floor | BENCH-SPIKE | 5–6 | only if real sessions materially use the floor |
| 13 | Authentication/delivery/QoS | DEFERRED | 4–7 | negotiated secure transport |
| 14 | Ultrasonic/asymmetric mode | BENCH-SPIKE | 5, 7 | qualified hardware-specific mode |
| 15 | Pleasant-audible mode | PLANNED-SPIKE | 5, 7 | bitrate plus tolerability gate |

## Rank 1 — Canonical C DSP and metrics contract

**Outcome:** portable C is the sole shipping implementation of bulk TX/RX,
two-input MRC, receiver selection, framing, block validity, and link metrics.
Swift and a test JNI-backed Kotlin wrapper are thin bindings. Python is an
oracle. Rank 3 retires the legacy Kotlin and duplicate live Swift DSP paths only
after on-device parity.

**Integration spike:** define a coarse-grained C capture/decode API and one
versioned result schema containing ordered block-validity masks, selected input
mode and reason, EVM, pilot residuals, timing, clipping, and profile identity.
Run every Cyrinx 1.x, Cyrinx 2.0, guarded-MRC, malformed-input, and retained-
capture fixture through direct C, Swift, JNI, KISS FFT, and Accelerate.

New versioned C structures receive ABI/version and prefix-compatible
`struct_size` fields (`struct_size >= known_minimum`, not exact-size equality).
Preserve existing Cyrinx 2 symbols and add `_v2` entry points where these rules
would otherwise break source or binary compatibility. Long-lived TX and RX
state use opaque contexts with explicit ownership, allocator,
reset, thread-affinity, and teardown contracts. Do not freeze a session context
or sounder wire/state model until Ranks 4–6 replace the legacy session and
measurement prototypes.

The ABI uses fixed-width integers for status, enum-like values, counts, and
wire-visible fields; status is an `int32_t` plus out parameters rather than
platform-sized `long`. Retained configuration contains no borrowed strings,
and public symbols consistently use `CYRINX_API`. Shared allocator, clock,
error, profile-ID/hash, and event-envelope conventions may stabilize here;
session timing and state semantics may not.

**Promotion gate:**

- byte-identical payload and block-validity results across all bindings;
- toleranced metric parity, with tolerances declared in each fixture;
- ABI/version mismatch and unknown profile tests fail safely;
- non-finite, truncated, duplicate-channel, and channel-order errors are
  contract-tested;
- configuration parsers and demodulation entry points are fuzzed under
  ASan/UBSan; and
- direct C, Swift, and test-JNI bindings establish parity sufficient for Rank 3
  to retire the duplicate live decoders.

**Library integration:** add the internal CMake/JNI plumbing and a test AAR
needed for conformance. Stable distributable AAR/pkg-config/XCFramework
artifacts wait for Rank 7, after session and event semantics stabilize.
Maintain a single profile registry and golden-vector generator. Establish the
fixture schema and initial raw/retained replay corpus here; every later spike
extends that corpus, while Rank 7 owns withheld policy qualification and public
corpus packaging.

## Rank 2 — Streaming bulk/bootstrap PHY and burst transmitter

**Outcome:** the C receiver consumes arbitrary PCM chunks, preserves timing
state across callback boundaries, acquires and tracks frames continuously, and
recovers after gaps without requiring a complete capture in memory. A queued
transmitter exposes a platform-neutral bounded PCM enqueue/render contract and
owns exact tail-padding accounting. Rank 3 alone owns persistent hardware audio
streams and decides when callbacks request rendered PCM.

This rank covers both the fast bulk PHY and one canonical robust bootstrap
bearer for discovery, capabilities, ACKs, and recovery. First compare the
legacy D-CSS control waveform with the RS-coded MFSK floor on acquisition
latency, false positives, goodput, reverberation, and implementation cost. Port
exactly one initial bootstrap TX/streaming RX to C; do not leave control traffic
as duplicated Swift/Kotlin DSP or make the session depend on Python.

**Spike hypothesis:** a stateful sliding synchronizer can reproduce batch
decoder decisions without rescanning unbounded history or exhausting the
measured on-device CPU margin.

**Minimal experiment:** replay the retained corpus through candidate streaming
synchronizers using randomized chunk sizes down to one sample, split
chirps/symbols/frames, inserted silence, zero-gap frames, dropouts, duplicated
callbacks, and clock-skewed chunks. Compare every delivered block and metric
with the frozen batch decoder. Render queued bursts through equally irregular
output-buffer requests and verify exact PCM plus tail accounting. Use a
standalone NDK benchmark runner for Pixel performance; this is not yet the
production Android audio adapter owned by Rank 3.

**Promotion gate:**

- exact block-result parity for all unmodified fixtures at every chunking;
- canonical bootstrap control frames interoperate through C, Swift, and JNI,
  including split-frame and back-to-back control/bulk transitions;
- no duplicate or missing delivery across 1,000 continuous replayed frames;
- bounded memory and bounded correlation history;
- at least 4x real-time sustained processing on the Pixel 7a and qualified Mac,
  with CPU and allocation traces retained;
- reacquisition after a declared dropout within the preregistered frame bound;
  and
- zero CRC-valid deliveries during a declared hour-long room-tone/noise corpus.

**Library integration:** expose an opaque streaming receiver context with
`pushPCM`, drain/callback delivery, reset, route-change invalidation, and
diagnostic snapshot operations, plus queued TX/enqueue/render operations. Audio
threads perform only bounded PCM movement: no heap allocation, decode, logging,
or application callback. Prefer a bounded SPSC ingress that records monotonic
sample indices/timestamps and discontinuity flags, followed by worker-thread
`process`; alternatively require the adapter to copy first and call C only on
its decoder worker. `pushPCM` must never ambiguously mean “real-time-safe copy
plus unbounded correlation.” Keep batch decode as a deterministic test and
offline-analysis convenience API.

## Rank 3 — On-device platform audio and bindings

**Outcome:** Android stereo `AudioRecord` and Apple audio routes feed the
streaming C core directly; C output feeds platform playback directly. The
accepted path no longer depends on ADB capture extraction or host-side DSP.

**Integration spike:** on each platform, capture PCM once and decode the exact
same bytes both on-device and on the host. Exercise route changes, microphone
source modes, interruptions, sample-rate conversion, channel duplication,
buffer overruns, independently addressed output channels, and app
foreground/background transitions. Prove each logical speaker can be driven
alone and each logical microphone retained alone; silent input/output downmix
or duplication fails capability qualification.

**Promotion gate:**

- on-device results match host replay for the same PCM;
- Pixel 7a and one Apple mobile target complete OTA encode/decode without host
  signal processing;
- route, sample rate, audio source, channel mapping, buffer loss, clipping, and
  app lifecycle state are recorded in every result;
- sample-identical/obviously duplicated logical inputs are flagged here rather
  than silently counting as diversity; full empirical independence/rank
  qualification follows in Rank 5; and
- playback gain changes are explicit, bounded, and verified where the platform
  permits verification; and
- release builds use the canonical C PHY through Swift/JNI-backed Kotlin and
  cannot select the legacy duplicate bulk decoders.

Before qualification, run a 30-minute simultaneous capture/playback soak per
platform with no unexplained sample loss, dead audio route, or unreported queue
overflow.

**Library integration:** ship platform audio adapters separately from the C
PHY so applications can provide their own capture/playback layer. Document
permissions, audio-session ownership, interruption behavior, cancellation, and
threading. Keep Elgato/external routes opt-in rather than silently selecting
them. Prefer native qualified 48 kHz operation. Any fallback resampler must
have a separately qualified phase/noise response, surface its use in
provenance, invalidate the prior profile, and force re-sounding; never silently
resample a coherent link under cached calibration.

## Rank 4 — Bootstrap discovery, burst session, and event envelope

**Outcome:** a robust, low-rate control plane remains available for discovery,
negotiation, acknowledgements, and recovery while the bulk plane carries app
data. This rank uses one manually configured conservative bulk profile and
provides best-effort message delivery with explicit missing-block results;
measured profile negotiation and reliable adaptive delivery arrive in Ranks 5
and 6.

The acoustic “CQCQ” beacon is deliberately small and robust: service hash,
ephemeral peer ID, protocol-version range, capability hash, and collision/role
nonce. Listen-before-talk, randomized slots, and deterministic nonce-based role
election prevent two advertising peers from remaining in a symmetric transmit
collision. Detailed capabilities follow in Rank 5 after the control session is
acquired.

The legacy session/control code is a protocol scaffold, not the production
runtime to wrap unchanged. Replace its blocking polling/immediate-ACK behavior,
shared capability-reply throttling, unsynchronized state assumptions, and
synthetic goodput reporting with a burst-oriented, serialized session actor and
measured counters.

**Session/event spike:** build the state machine first in the deterministic simulator.
Exercise simultaneous discovery, role-election collisions, peer disappearance,
route changes, partial negotiation, bulk-plane failure, control-plane recovery,
cancellation, and subscriber backpressure before exposing a stable API.

The authoritative protocol state, generations, timers, role election, and
transitions live in C. The eventual Swift wrapper is actor-based and Android
wrapper coroutine-based so the platforms cannot evolve different protocols.
This is the target public shape after Ranks 5–6 complete it:

```swift
let transport = CyrinxTransport(configuration: .automatic)
try await transport.start(
    in: .listenAndAdvertise(service: "com.example.app")
)

for await event in transport.events {
    // peerFound, peerLost, connectionReady, linkEstimateChanged, ...
}

let connection = try await transport.connect(to: peer.id)
let transfer = try await connection.send(message)
for await status in transfer.statusUpdates { /* ... */ }
for try await message in connection.incomingMessages { /* ... */ }
```

`send(_:)` returns an operation/receipt rather than implying peer delivery.
Its states distinguish accepted into the bounded queue, rendered on air,
peer-acknowledged, and reliably delivered. Rank 4 promises only the states the
best-effort policy can prove; Rank 6 adds reliable terminal delivery without
silently changing the meaning of `send`.

The public push stream must contemplate:

- `peerFound`, `peerUpdated`, and `peerLost` with explicit loss reasons;
- discovery, negotiation, sounding, connected, degraded, recovering, and
  closed state changes;
- `connectionReady`, `connectionClosed`, route changes, and recoverable errors;
- `linkEstimateChanged`, not an ambiguous scalar `newBitrate`; and
- incoming-message availability, `sendProgress`/`sendCompleted`, and
  backpressure without requiring polling.

The C core owns a bounded event queue; Swift `AsyncSequence` and Kotlin `Flow`
wrappers drain it on their own executors. A lightweight wake signal may announce
queued events, but C must never invoke application code from the real-time audio
callback. A sticky overflow generation/counter lives outside the queue so the
overflow signal cannot itself be dropped; generation gaps force the wrapper to
refresh authoritative versioned snapshots. High-rate link estimates coalesce.

`LinkEstimate` is directional and reports sustainable send/receive goodput,
conservative lower bound, current PHY mode/rank/FEC, observed block loss,
latency, setup/sounding amortization, stability/coherence, confidence,
measurement age, and the reason for the update. Events are debounced, carry a
monotonic generation and timestamp, and complement queryable authoritative
snapshots so dropped events cannot corrupt app state.

The metrics distinguish configured coded PHY rate, scheduled payload rate,
estimated CRC-accepted airtime goodput, and observed wall-clock application
goodput. A stale estimate remains labeled stale rather than silently becoming a
measurement.

Wrappers may additionally derive coarse application budgets such as
`controlOnly`, `text`, `thumbnail`, and `bulk`, allowing an app to choose
different content on a measured 30 kbps versus a hypothetical future 300 kbps
link. The numeric directional estimate and confidence remain authoritative;
the convenience tier is not a second adaptation algorithm.

**Promotion gate:**

- deterministic event traces for every modeled transition and race;
- no missed terminal state, silent truncation, or deadlock under loss and
  cancellation injection;
- bounded event buffering with documented replay/current-snapshot semantics;
- a sample app discovers a peer and attempts 64 KiB and 1 MiB best-effort
  transfers through the manually configured real bulk plane, reporting exact
  delivered and missing block maps without silent hash-success claims; and
- public documentation plainly explains discovery, connection, send/receive,
  permissions, variable rate, failure, and unauthenticated-peer semantics.

**Library integration:** use message boundaries internally; add byte-stream or
SwiftNIO-style adapters later. Reserve session IDs, negotiation transcripts,
message IDs, and versioned `SendOptions` now so reliability, security, and QoS
do not require an API redesign.

## Rank 5 — Sounding, self-calibration, capacity, and capability negotiation

**Outcome:** after Rank 4 acquires a bootstrap control session, the parties
exchange independently represented capabilities, characterize every usable
port-to-port path in both directions, select a profile, verify it, and expose
the resulting link estimate.

Spikes 5a/5b depend only on Ranks 1 and 3 and may proceed before the burst
session is complete. Spike 5c and negotiated activation depend on Rank 4 plus
the promoted 5a/5b measurements. The eventual C sounder context and result ABI
stabilize here, not prematurely in Rank 1.

The current legacy capability payload is a useful scaffold, but bulk-profile
negotiation is absent and Swift currently maps one `channels` value to both
speaker and microphone counts. Replace that with versioned, extensible port
descriptors for verified logical speakers and microphones, independent channel
control, sample rates, source modes, routes, bands, profile/MCS/FEC support,
clock behavior, buffer limits, linear level/THD limits, and calibration hashes.

### Spike 5a — Canonical passive and active measurements

Port the known-waveform sounder and passive room-tone analysis behind a C
metrics API. Estimate complex per-bin channel response, noise covariance,
delay spread, timing/CFO/SRO, pilot EVM, clipping, and nonlinear products from
caller-supplied PCM. Add a same-session capacity report; do not publish one
unqualified “Shannon limit.”

The older PSD surveys reported rough 184/155 kbps directional estimates, and
EVM-to-effective-SINR arithmetic can produce a different, lower heuristic.
Neither is a measured capacity limit: stationary PSD can overstate phase-
modulated performance, while EVM residuals mix non-Gaussian tracking,
estimation, and nonlinear errors. This spike replaces cross-session arithmetic
with same-session `H[k]`, noise covariance, power, nonlinearity, coherence, and
explicit protocol-duty evidence.

**Gate:** synthetic channels recover known parameters within fixture-specific
tolerances, retained captures match the frozen Python oracle, and estimates do
not consume payload decisions or expected bytes. Capacity output records the
assumed bandwidth, power constraint, active-bin mask, noise model, protocol
overhead, and confidence.

As a separate predictor spike, compute the log-det/Shannon upper bound,
bitwise generalized mutual information from randomized, data-representative
known QAM probes with representative PAPR and actual LLRs, and the scheduled
ceiling after CP/pilots/FEC/preamble/gaps. Fit any probe-to-block-loss mapping
on whole runs/devices and hold out a preregistered minimum set of whole physical
cells/devices. Promote it into adaptation only if median held-out goodput error
is at most 15%, it selects a profile within 10% of the measured oracle in at
least 80% of held-out cells, and it never chooses below the declared recovery
objective (initially 98%) when a profile meeting it exists; selecting below 90%
is a zero-tolerance catastrophic miss. If predictions remain more than 20%
optimistic or held-out Spearman correlation with delivered goodput is below
0.5, retain the calculation only as a descriptive bound.

### Spike 5b — Local self-characterization

Play each speaker one at a time and retain every local microphone. Measure
logical channel mapping, cross-talk, duplication, echo path, response, THD,
clipping, route/source identity, and safe gain range. Pose and accelerometers
may prioritize an Android source such as `CAMCORDER`, but a tone check must
confirm the realized route; pose alone is not proof of microphone selection.

**Gate:** repeated checks identify stable port mappings and reject duplicated,
mixed, clipped, or unexpected routes. Profiles are keyed by device + route +
source + sample rate and invalidated on a material route or OS-build change.

### Spike 5c — Bidirectional cross-device matrix sounding

Sound A speaker 1..N to all B microphones, then B speaker 1..M to all A
microphones. Begin sequentially; compare orthogonal FDM/CDM/Zadoff-Chu pilots
only after the sequential estimate is trustworthy. Estimate distinct
`H_AB[k]` and `H_BA[k]`: acoustic propagation may be reciprocal, but the full
transducer, location, gain, and DSP paths are not.

**Gate:** all verified paths are repeatable within declared tolerances across
held-out repeats; simultaneous orthogonal sounding agrees with sequential
sounding before it is used to reduce setup time; unsupported physical ports
are never inferred from marketing specifications.

### Integration and handshake gate

- protocol version ranges and unknown TLVs are handled safely;
- speaker and microphone capabilities are independent;
- both directions negotiate sample rate, active band, CP, MCS/FEC, diversity,
  rank ceiling, control profile, and calibration/profile hash;
- every immutable profile has an on-wire ID/hash, proposed and accepted
  activation sequence, and effective frame boundary; a peer that misses
  activation returns to the robust control profile instead of deadlocking on
  incompatible CP/MCS/pilot geometry;
- setup latency and sounding overhead are measured and included in
  setup-inclusive application goodput; and
- `peerFound` remains explicitly unauthenticated until Rank 13.

## Rank 6 — Public peer API, closed adaptation, and selective recovery

**Outcome:** the session selects the best sustainable directional profile,
tracks degradation, changes rate with hysteresis, performs bounded selective
recovery or falls back without deadlock, and completes the public peer/
connection API designed in Rank 4.

**Policy spike:** replay a preregistered sequence of quiet, HVAC, reverberant,
shadowed, moved, and route-changed cells. Compare the adaptive policy with the
best fixed conservative profile. Freeze each profile decision before payload
and test whether lower-bound `LinkEstimate` predictions are calibrated against
subsequent delivered goodput.

Same-frame receiver/mode selection cannot inspect payload bins or that frame's
CRC. Causally prior block/CRC outcomes may inform the next burst's MCS and retry
policy when the observation window and activation boundary are recorded.

The ladder includes speaker/input selection, two-mic MRC, CP, pilot cadence,
MCS/FEC, active-bin mask, coherent modes, and the RS-coded non-coherent floor.
Reserve robust control slots so the system can recover when the selected bulk
profile becomes undecodable.

Baseline reliability here includes block/message fragmentation and bounded
reassembly, selective ACK bitmaps/ARQ, duplicate suppression, bounded retry,
ACK-loss recovery, backpressure, and cancellation. Rank 13 owns incremental
parity, resumability, authentication, and advanced multi-priority QoS—not the
minimum machinery required for a hash-correct transport.

**Promotion gate:**

- versioned policy and thresholds with deterministic replay results;
- no payload-bin, payload-byte, or same-frame CRC leakage into selection;
- every declared fast/robust held-out cell completes all preregistered 1 MiB
  transfers hash-correctly; slower coherent tiers use a preregistered 4 KiB
  transfer, while the 138 bps floor uses a bounded 32-byte liveness/recovery
  test rather than an hour-long file gate;
- setup, sounding, control, retransmission, and recovery count in net
  application goodput;
- mean and lower-tail net goodput beat the best fixed conservative policy under
  the preregistered objective; raw block recovery must either clear the
  profile's numerical floor or meet a one-sided 95% confidence noninferiority
  margin of at most 0.1 percentage point versus its conservative control, and
  no planned file transfer may fail; and
- an advertised p10/lower-bound estimate is exceeded by the subsequent window
  at least as often as its stated confidence requires.

**Library integration:** make the adaptive policy replaceable and versioned,
but keep one conservative built-in default. Emit material
`linkEstimateChanged`, `degraded`, and `recovered` events with reasons and
app-level advice. Avoid rate-event chatter through hold times and hysteresis.
Activate profile changes only through Rank 5's accepted sequence/boundary
protocol, and return to bootstrap control if either peer misses activation.

## Rank 7 — SDK packaging, diagnostics, corpus, and default qualification

**Outcome:** defaults and support statements are based on held-out hardware,
routes, poses, environments, and days rather than one optimized bench cell,
and the same packaged SDK that developers receive produces the evidence.

### Packaging and diagnostics gate

- clean external SwiftPM and Android consumer projects build from a release tag
  without repository-relative paths; packaged artifacts, not source-tree-only
  builds, pass binding/golden/retained-capture conformance;
- CMake/installable headers, Android AAR/JNI ABIs, symbol visibility, semantic/
  protocol/ABI compatibility, licenses/SBOM, and reproducible archives are
  checked in release CI;
- structured support bundles include route/source/channel provenance, profile
  and negotiation transcript, event timeline/generation gaps, PCM queue drops,
  block maps, sounder metrics, and separate PHY/application goodput counters;
- optional raw PCM is opt-in and bounded, payloads and keys are redacted, and
  every induced route/sync/clipping/FEC/MAC/queue failure yields a bundle that
  replays through the canonical C receiver.

The fixture schema and seed corpus begin in Rank 1 and feed Ranks 2, 5, and 6;
this rank expands it into the withheld cross-device corpus and public support
surface rather than creating the measurement substrate late.

**Campaign design:** preregister a device/geometry matrix including at least
two Mac hardware families, Pixel and iPhone peers, Android source modes,
face-up/face-down poses, modest lateral offsets, HVAC on/off, different days,
and order-balanced repetitions. Add Moto where its route can be reproduced.
Publish raw PCM when privacy permits, route signatures, schedule denominators,
block maps, code/config hashes, and negative findings as an open replay corpus.

**Promotion gate:**

- the profile-selection policy is frozen before held-out evaluation;
- a fast profile becomes default only if its per-cell and aggregate recovery
  clears the preregistered numerical objective (initial reliable-default target:
  99.9%) or a one-sided 95% confidence noninferiority margin of at most 0.1
  percentage point versus the conservative control, with zero planned
  hash-transfer failures; its preregistered p10 run/cell-cluster goodput must
  also beat that control;
- mic diversity claims identify the exact logical channels and prove they are
  neither duplicated nor OS-beamformed into one stream;
- a withheld physical benchmark has a written scoring and claims-integrity
  rubric before it is reported; and
- failures remain in the corpus and in `docs/NEGATIVE_FINDINGS.md`.

Near-field, fixed-3-ft, handheld, ultrasonic, and pleasant-audible cells remain
separate benchmark classes.

## Rank 8 — Single-stream goodput research portfolio

These spikes have better near-term value-to-effort than true MIMO. They may run
after Rank 5 supplies trustworthy measurements, but none enters the library
until it independently clears its gate and then passes Rank 6/7 integration.

### Spike 8a — Regularized per-speaker pre-equalization

**Hypothesis:** a bounded inverse of the stable component of the measured
end-to-end transfer recovers bins lost to smooth transducer roll-off without
amplifying narrow room nulls or triggering protection DSP. The complex
fixed-route/speaker oracle uses the regularized form described by
[Yamamoto and Kubo](https://www.jstage.jst.go.jp/article/comex/11/3/11_2021XBL0206/_article/-char/en/).
The held-out smooth profile is a Cyrinx robustness variant, not a claim that a
built-in speaker/room/mic sweep has isolated the loudspeaker alone.

**Experiment:** characterize each speaker separately through all retained mics
at three small placement offsets. Compare a smooth magnitude/minimum-phase
profile derived across those cells with a fixed-route complex-inverse oracle.
Test only flat, +3 dB-cap, and +6 dB-cap EQ; compare equal-band candidate and
control at both equal sample peak and a separately labeled matched-RMS
diagnostic. Record crest factor, clipping, THD/intermodulation, protection-DSP
behavior, and out-of-band energy.

**Promote in two stages:** at the identical MCS/schedule, where only 1.54%
headroom remains, require at least 3 dB improvement in payload-weighted p10
probe effective SINR (or a preregistered equivalent GMI improvement),
noninferior scheduled goodput, and better recovery/lower-tail
behavior with no clipping, THD/intermodulation, protection, or spectral
regression. Only after that result enables a preregistered higher-capacity
MCS/band/loading may a rate promotion require at least 10% net-goodput gain at
noninferior recovery. Rank 7's stronger default gate still applies.

Stop if useful inversion requires more than 6 dB boost, its benefit reverses
or fails transfer stability on held-out positions/mics, or yields less than
1 dB p10 probe gain in the two-pair screen. If only the route-specific complex
oracle works, label it fixed-route precoding rather than creating a per-device
database. Make acoustic-level/exposure comparisons only when calibrated
LAeq/LCpeak/SPL instrumentation is present.

### Spike 8b — Bracketing channel estimates

**Hypothesis:** known full-band training before and after a longer data region,
with temporal interpolation inspired by
[Yamamoto and Kubo](https://www.jstage.jst.go.jp/article/comex/11/3/11_2021XBL0206/_article/-char/en/),
arrests chronological EVM/block-loss degradation without decision-directed
leakage. Align start/end CIRs into a common CFO/SRO/timing reference first,
then interpolate significant taps or unwrapped magnitude/phase while retaining
the current per-symbol timing and common-phase tracking.

**Experiment:** first establish an offline, non-claimable oracle bound on
retained 128-symbol captures by deriving a terminal estimate from known final
symbols. Continue to playback only if that diagnostic recovers at least half
the currently lost late-half blocks or improves late-quartile EVM by at least
3 dB. Then add one or two known terminal trainers before the protected tail and
compare start-only versus complex endpoint interpolation on the same waveform.
Hold CP96, pilots/16, rate 2/3, and zero gap fixed; vary only 64 versus 128 data
symbols. Report three denominators: scheduled goodput counts terminal training
and remains comparable with a recomputed same-definition reference; gross
goodput additionally counts trailing padding; session goodput/latency counts
buffering and decode. A shared boundary trainer is an accounting/superframe
variant and becomes a new schedule class only if gaps or timing change.

**Promote only if:** the 128-symbol candidate exceeds the 69.110 kbps
cross-session zero-gap reference under the same scheduled denominator while
meeting at least its 97.009% recovery, or
qualifies only as a robustness mode by gaining at least five recovery
percentage points for no more than 2% goodput loss. Stop after the two-pair
screen if real terminal training recovers less than 25% of lost blocks,
regresses the first half, or endpoint interpolation does not predict the
midpoint. Do not respond by adding an unbounded trainer sequence; fall back to
shorter frames or a fixed known-pilot lattice.

### Spike 8c — Localized DFT-spread OFDM

**Hypothesis:** lower PAPR avoids speaker/OS nonlinearities and produces more
delivered bits at the same band and peak constraint than conventional OFDM.

**Experiment:** define one localized DFT-spread mapping and its known-pilot
arrangement before simulation; do not use “SC-FDE” as an interchangeable label
or redesign pilots after results. First generate at least 10,000 random frames
through AWGN and retained room responses with identical coded bits, CP,
accounted training, and band.
Proceed OTA only if the candidate lowers 99.9th-percentile crest factor by at
least 3 dB without a coded-RIR or spectral-mask regression. OTA compares equal
sample peak/OS volume first and matched RMS only as a diagnostic, across at most
two preregistered MCSs and two levels. Keep pre-EQ out of this spike.

**Promote in two stages:** at identical MCS/schedule, require noninferior
scheduled goodput and materially better recovery/lower-tail EVM or distortion;
received-RMS change is diagnostic rather than a mandatory conjunct. Only if
the waveform enables a preregistered higher-capacity MCS/geometry may the rate
stage require at least 10% net-goodput gain with noninferior recovery. Stop
after the two-pair screen if neither EVM/recovery nor distortion improves, or
if protection/spectral regrowth appears; do not redesign pilots inside this
spike.

### Spike 8d — Effective-SINR loading and coding

**Hypothesis:** data-representative randomized QAM probes and Rank 5's
effective-SINR/GMI estimator allocate bins more safely than stationary raw-PSD
SNR while recovering capacity lost by one uniform constellation.

**Experiment:** on whole-device/cell holdouts, compare at most four frozen
policies: uniform incumbent, raw-PSD thresholds, pilot-GMI thresholds, and one
noise-covariance-aware variant. Then OTA-screen only the best non-incumbent.
Keep allocation/profile signalling in the denominator and keep the current
convolutional code until measurements show it is the bottleneck.

**Promote only if:** the payload-independent allocation improves p10 net
goodput by at least 5%, meets the selected recovery objective, and wins 7/8
confirmatory pairs after signalling overhead. Stop before OTA if replay gain is
below 2% or the Rank 5 predictor fails calibration; stop after the screen if
signalling or loss-tail regressions erase the gain. LDPC or polar codes are not
justified by headline rate alone.

## Rank 9 — Generalized N-microphone diversity

**Outcome:** generalize the proven two-input C MRC/selection path to any set of
sample-aligned, independently verified logical microphones while degrading
cleanly to one input.

**Spike:** extend retained-capture simulation to identical, duplicated,
delayed, noisy, clipped, correlated, and complementary-notch branches. Compare
full MRC, per-band subset selection, and noise-covariance-aware combining.

**Promotion gate:**

- N=1 is identical to the scalar receiver and N=2 preserves current fixtures;
- duplicate or sufficiently delayed channels are rejected or aligned rather
  than double-counted;
- held-out pilot selection cannot worsen the primary beyond its declared
  selection margin;
- complexity and memory scale within declared bounds; and
- OTA rescue/general benefit is reproduced on at least two device classes with
  exact channel and route provenance.

**Library integration:** use a strided/interleaved multi-input C API and return
per-input evidence, selected subset, weights, and rejection reasons. Do not
expose physical-microphone claims that the OS route cannot verify.

## Rank 10 — Directional and fixed-3-ft throughput program

This is deliberately separate from Cyrinx 2's optimized near-field result.

### Spike 10a — Weak reverse direction

Re-characterize iPhone-to-Mac and other speaker-limited directions. Test only
Rank-8 mechanisms that have already passed their generic spike: stable
per-device pre-EQ, measured active-band reduction, effective-SINR loading, and
directional MCS/FEC. Do not reimplement or retune those mechanisms inside the
directional campaign. Include the return direction's control duties when
reporting a bidirectional session.

### Spike 10b — Fixed 3 ft

Fixture 36.0 inches from the active speaker acoustic center to the selected mic.
Repeat route/source verification, one-speaker-to-all-mic response, passive
noise, safe gain, delay spread, and channel coherence before testing data. Use
sequential halving over no more than eight preregistered profiles, starting from
sounder-derived band/CP with a QPSK rate-1/2 coherent anchor and robust 16-QAM;
include 64-QAM only if probe GMI supports it. Freeze the conservative adaptive
control before screening. Hold out eight four-repeat cells: face-up and
face-down centered with HVAC on/off, plus +/-15 cm lateral offset with HVAC
on/off. Do not silently pool those strata.

**Promotion gate:** centered median session-net goodput of at least 20 kbps at
at least 99% block recovery, held-out-pose aggregate recovery of at least 95%
with no zero-goodput cell, and at least a 20% gain over the same-cell
conservative adaptive control in 7/8 confirmatory pairs. Stop coherent-profile
search if no preregistered candidate reaches 90% recovery; record the channel
envelope and separately test/qualify the non-coherent floor at 3 ft rather than
assuming its near-field shadowed-cell evidence transfers. Publish the best
route-specific number and robustness distribution separately. Never reuse
near-field settings without requalification; they may remain a matched control.
Never compare 3-ft results as the same measurement class.

### Spike 10c — Motion sensitivity before motion-specific DSP

At fixed 3 ft, compare static placement with preregistered lateral translation,
lift/return, and rotate/set-down trajectories while logging audio and IMU on a
common monotonic timeline. Measure channel coherence time, phase slope,
occlusion/route changes, outage duration, and reacquisition before proposing
OTFS or IMU feed-forward.

Document the mapping from Android `SensorEvent` timestamps and audio-frame
timestamps onto that common clock before interpreting phase/IMU correlation.

Start a motion-specific tracker only if the baseline loses at least ten
recovery percentage points while coherence remains longer than two OFDM
symbols. Promote a tracker only if it retains at least 80% of static goodput,
recovers at least 95% of blocks, and reacquires by the next robust control
preamble in 7/8 runs.
Stop if baseline loss is smaller, coherence is below one symbol, or failures
are explained by occlusion/route change. For one-to-two-symbol coherence,
characterize shorter symbols/frames rather than starting a tracker. Define
reacquisition as the next robust control preamble and report the wall-clock
seconds, rather than using a profile-dependent “one frame” SLA. IMU phase
feed-forward additionally requires at least 0.7 correlation between predicted
radial motion and measured phase slope.

## Rank 11 — NxM beamforming and true MIMO

True MIMO is conditional research, not an assumed twofold upgrade. The existing
dual-preamble scalar SVD scaffold is not frequency-selective MIMO and must not
be described as such.

### Spike 11a — Feasibility and capacity accounting

Use Rank 5's path matrix to estimate, per subcarrier, `H[k]`, noise covariance,
singular values, effective rank, and coherence. Evaluate log-determinant
capacity under fixed sum digital sample power with per-speaker peak limits,
plus separately labeled equal-per-speaker and instrumented acoustic-power
classes. Include sounding, pilots, CP, control duty cycle, and precoder-update
cost.

Use the measured log-determinant result, not an assumed twofold multiplier.
Report fixed sum digital sample power with per-speaker peak limits as the
reproducible primary class. Calibrated radiated/SPL power is a separate class
only when instrumented; equal per-speaker drive adds approximately 3 dB total
digital power and remains a separately labeled diagnostic.

Measure at least five matrices each in optimized near-field, centered 3-ft, and
one 3-ft lateral-offset geometry. Proceed only if inputs/outputs are genuinely
independent, phase-referenced repeat coherence after CFO/SRO/timing removal is
at least 0.95, residual phase standard deviation is at most 15 degrees, and
amplitude stability meets a preregistered bound over the sound-feedback-burst
delay. A usable second mode means mode-2 GMI sufficient for QPSK rate 1/2 after
the total-power split, covering at least 40% of payload-weighted bins. Advance
only if predicted net two-mode gain is at least 1.35x in two of the three
geometries; otherwise publish and stop rather than treating the 1.15–1.35 gray
zone as permission to continue.

Sequential speaker slots must share one phase-continuous stereo render/capture
or an equivalent common phase reference; separate player launches cannot
establish relative channel-column phase for transmit precoding.

### Spike 11b — Single-stream spatial gains first

In order, compare:

1. best-speaker selection;
2. one-stream maximum-ratio/regularized transmit beamforming;
3. null filling and interference-aware precoding;
4. transmit diversity/SFBC for unstable channels.

Avoid unconstrained zero forcing on ill-conditioned bins. Constructive and
destructive interference are tools for array gain and crosstalk control; they
do not relax power, peak, THD, or audibility constraints.

Use preloaded/oracle CSI first, then deliberately age it to establish the
break-even transfer size and update interval. Promote beamforming only if
payload-weighted p10 post-combining SINR rises by at least 3 dB, session-net goodput rises
by at least 10% with noninferior recovery, and 7/8 pairs win at fixed total
digital sample power. Stop if more than 95% of power collapses onto one speaker for more than
80% of bins, CSI expires before one five-frame burst, or the two-pair screen
gains less than 5%; retain speaker selection instead.

### Spike 11c — True two-stream spatial multiplexing

Only after Spikes 11a and 11b establish feasibility and the best one-stream
control, validate two independently seeded coded streams through measured 2x2
room responses. Then test orthogonal known pilots and per-bin MMSE OTA; keep ZF
diagnostic-only. Compare with the best one-stream, two-mic receiver at identical
fixed sum digital sample power, per-speaker peaks, band, CP, schedule, and
all-overhead accounting. QPSK rate 1/2 per stream is an acquisition/separation
smoke test and cannot beat the current 64-QAM one-stream rate; test at most three
preregistered allocations, including a higher-rate candidate for the gain gate.

**Promotion gate:** offline replay is byte-correct, OTA aggregate session-net
goodput is at least 1.35x the same-cell one-stream/SIMO control, each stream
recovers at least 98% of blocks, and 7/8 confirmatory pairs win. Do not begin
OTA unless measured-matrix replay predicts at least the full 1.35x promotion
threshold. Stop after two
OTA pairs if either stream recovers less than 80% or aggregate gain is at most
10%, and stop after the three allocations if none reaches 1.35x. If promoted,
Rank 6 integration must select rank per band and fall back without outage when
the second singular value collapses.

**Library integration:** generalize the canonical C interfaces to `N_tx x
N_rx` matrices and explicit precoder/combiner state. Start with multiple ports
on one device. Coherent distributed transmission from different devices is
deferred because it requires substantially tighter shared clock and phase
synchronization.

## Rank 12 — Faster non-coherent floor

The RS(15,11) GF(16) MFSK floor is a BENCH-SPIKE with retained OTA evidence
at 138 bps. Optimize it only if Rank 6 telemetry shows supported sessions spend
material time there.

**Spike:** adapt symbol duration to measured delay spread and compare a denser
tone grid under the existing detector-confidence/erasure decoder. Include
false acquisition, shadowing, reverberation, and narrowband interferers.

**Promotion gate:** improve held-out net floor goodput while every declared
cell completes hash-correct delivery and the false-detection objective remains
met. Otherwise retain the current slow floor and spend effort on recovery or
repositioning guidance instead.

## Rank 13 — Authentication and advanced delivery/QoS

Start only after the unauthenticated session and measured link-estimate API are
stable. Reserve the API fields earlier, but do not imply identity or privacy
before this rank ships.

**Protocol spikes:**

- incremental parity and resumability beyond Rank 6's baseline selective ARQ;
- audited X25519 + HKDF + AEAD (ChaCha20-Poly1305 or AES-GCM) with capability,
  profile, role, and session nonces bound into the authenticated transcript;
- downgrade, replay, reflection, duplicate-message, and peer-loss behavior;
- explicit peer-authentication UX such as SAS/QR or documented TOFU, with key
  confirmation, replay windows, and rekeying; and
- priority/deadline scheduling under measured fast (~70 kbps), robust
  (~20–30 kbps), and 138 bps floor classes, plus a parameterized synthetic API
  stress range.

Do not port or bless the current experimental custom SHA-256-derived “CTR”
keystream; it is not a standard cipher mode. Bare X25519 does not authenticate a
peer. A degraded acoustic link must never silently fall back from secure to
plaintext.

**Promotion gate:** hash-correct ordered delivery under the declared loss
model, no silent downgrade or nonce reuse, deterministic interoperability
vectors, explicit unauthenticated/authenticated state transitions, and an
external cryptographic review before advertising authenticated security. Add
known-answer, MITM, replay, downgrade, malformed-handshake, fuzz, and
multi-stream fairness/starvation tests. Report encryption, handshake, retry,
and QoS overhead as application goodput, not as free metadata.

## Rank 14 — Ultrasonic and asymmetric modes

Current measurements show partial feasibility, not a general commodity-
speaker mode. Built-in-device coherent ultrasound, 96 kHz extended-band paths,
and specialized nonlinear downconversion are separate hardware classes.

### Spike 14a — Commodity-path qualification

Survey each speaker/microphone route for complex phase coherence, not only
received PSD. Stop coherent-QAM work on a route whose phase residual remains
incompatible with the lowest proposed constellation. Retain a non-coherent
fallback result rather than inferring capacity from multitone energy.

### Spike 14b — External ultrasonic transmitter

On explicitly characterized 36–40 kHz hardware, test BatComm-style microphone
nonlinearity, square-root predistortion, intermodulation-aware bin masks, direct
near-ultrasonic/downconverted band allocation, and differential fallback from
[BatComm](https://web.eecs.utk.edu/~jliu/publications/bai2020batcomm.pdf). Keep
this profile separate from built-in speakers.

**Promotion gate:** bidirectional negotiation, hash-correct delivery across at
least two qualified hardware pairs, route-specific calibration invalidation,
and net application goodput including the slow return path. “Inaudible” also
requires calibrated acoustic-level evidence and a preregistered blinded
audibility protocol; spectral location alone is insufficient.

## Rank 15 — Pleasant-audible mode

This mode deliberately trades bitrate for tolerability and is not a disguised
unrestricted audible OFDM profile.

**Spike:** bound the search to two branches and at most twelve waveform
settings: (A) 16/32 consonant chord-spectrum codewords with phase-continuous
synthesis, shaped transitions, non-coherent classification, and GF(16)/RS
coding; (B) data masked under a generated ambient/rain/pad cover at two fixed
masking depths. Use no copyrighted cover asset. Select and freeze candidates
before recruiting listeners. Level-match at the listener position with
instrumented LAeq/LCpeak; retain digital LUFS only as a supplement. Branch A
uses a benign no-data chord/carrier control, Branch B uses cover-only, and both
compare with the current audible modem. Randomize listening order. Measure
strict net goodput after preamble/FEC/CRC and cover/setup duration, recovery,
spectrum, peak/RMS, roughness/tonality as screening metrics, and listener
annoyance/preference.

**Promotion gate:** branch A delivers at least 50 bps or branch B at least
500 bps, with at least 95% recovery in both quiet and HVAC strata. The word
“pleasant” additionally requires a preregistered blinded study of at least 16
listeners: at least 12/16 prefer it over standard Cyrinx at matched level, it
meets an absolute pleasantness/annoyance threshold, and a confidence-bounded
noninferiority test places it no more than one seven-point-scale unit worse than
its benign no-data control. Calibrated level/peak safety limits apply before
exposure; an adverse/painful report stops the session but is not a promotion
statistic. Branch B also needs blinded ABX evidence that embedded data is not
reliably distinguishable from cover-only. Stop a branch after its six settings
if none clears both rate and recovery; stop masked audio if the data must become
audible above the cover or materially worsens its rating. Until the stronger
human gate passes, call a result “more tolerable,” not “pleasant.” A technically
successful masked mode remains labeled cover-dependent.

## Explicit deferrals

- **LDPC/polar FEC:** trigger only when soft-decision FEC is a measured
  bottleneck after channel tracking and loading are fixed.
- **OTFS or IMU-assisted Doppler cancellation:** trigger only after controlled
  motion measurements show Doppler, rather than route/pose loss, limits
  application goodput.
- **Distributed multi-device MIMO/OFDMA:** trigger after single-pair NxM and an
  acoustic MAC are qualified; distributed carrier/phase synchronization is a
  separate research problem.
- **Haptics-to-accelerometer transport:** retain as an exploratory contact-link
  project, not part of the acoustic library critical path.
- **Automatic cloud/device calibration database:** trigger after local profile
  invalidation and privacy/versioning rules are stable.

## Maintenance and external work

- Keep `CHANGELOG.md`, `docs/NEGATIVE_FINDINGS.md`, the experiment ledger, and
  `scratch/hw20k/NOTES.md` current as spikes land or stop.
- Extend golden vectors whenever a candidate is promoted; do not make a bench
  artifact a release dependency.
- Run `./scripts/check.sh`, Android unit/build gates, binding conformance, and
  retained-capture replay before release.
- Maintain [cyrinx.org](https://cyrinx.org) and the Cyrinx 2 follow-on paper as
  evidence/documentation surfaces, not as competitors for library engineering
  rank.
- arXiv submission remains user-gated and outside this library critical path.

## Completed foundations

| Foundation | Status |
|---|---|
| Portable C bulk PHY and Swift binding | shipped |
| Two-input per-subcarrier MRC | shipped; Pixel OTA exercised |
| Pilot-only automatic primary/MRC selection | shipped; Pixel OTA exercised |
| Pilot-local LLR reliability weighting | shipped; Pixel OTA exercised |
| Dynamic CP/MCS decision primitives | library decision half shipped; capture/integration open |
| RS(15,11) GF(16) MFSK floor | BENCH-SPIKE with retained OTA evidence |
| Passive room-tone analysis | Swift-only primitive; canonical C/session integration open |
| Legacy capability exchange | partial scaffold; bulk negotiation open |
| True 2x2 MIMO | open; existing scalar-preamble experiment is not payload MIMO |
| Cyrinx 2.0 website, release, and follow-on paper | complete |

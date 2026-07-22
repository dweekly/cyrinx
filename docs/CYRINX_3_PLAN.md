# Cyrinx 3.0 Delivery Plan

Fresh as of 2026-07-20. Status: proposed execution decomposition, revised per
PR #69 review: Rank 2's 4x real-time gate now blocks at C3-09/C3-10, Rank 5 is
split (C3-20a measurements / C3-20b self-characterization / C3-21 sounding),
the bounded capacity-predictor spike is restored as C3-21a, and C3-16 requires
physical over-the-air smoke transfers.

This document turns the stack-ranked outcomes in
[`ROADMAP.md`](../ROADMAP.md) into independently reviewable pull requests. The
roadmap remains authoritative for research priority, evidence standards, and
promotion gates. This plan defines how to build and merge the Cyrinx 3.0
critical path without treating a bench spike as a shipping feature.

## Release definition

Cyrinx 3.0 is the first integrated acoustic transport SDK, not primarily a new
peak-PHY-rate release. A supported Apple or Android endpoint must be able to:

1. open and verify a local audio route;
2. discover a nearby peer and elect complementary session roles;
3. establish a robust control channel;
4. measure both directions of the realized acoustic path;
5. negotiate and activate a qualified bulk profile;
6. exchange bounded messages with explicit delivery evidence;
7. adapt or fall back without deadlock when the route degrades; and
8. export enough structured evidence to replay and diagnose a failure.

The accepted production path performs latency-critical PHY and transport work
on the endpoint that captures or renders the audio. A Mac acting as an acoustic
endpoint is on-device processing. Exporting phone PCM over ADB or another link
for a separate computer to decode is host-assisted processing and is not an
accepted Cyrinx 3.0 runtime path. Host batch decode remains a required oracle,
replay, and research facility.

The release implements Roadmap Ranks 1 through 7. Ranks 8 through 15 remain
separately gated 3.x research unless a candidate clears its existing promotion
criteria without delaying the critical path.

## Release invariants

These are design constraints, not optional polish:

- **One shipping modem:** portable C owns the shipping bootstrap and bulk PHY,
  framing, diversity decisions, block validity, and link metrics. Swift and
  Kotlin/JNI are bindings, not independent modem implementations.
- **One protocol authority:** the C session engine owns state, generations,
  timers, role election, profile activation, retry state, and event order.
- **Serialized mutation:** every session mutation executes on one declared
  executor or processing loop. Audio callbacks only move bounded PCM and mark
  discontinuities.
- **Bounded resources:** every PCM queue, event queue, message queue,
  reassembly buffer, retry window, and retained diagnostic has a documented
  limit and overflow behavior.
- **Causal adaptation:** same-frame payload bins, decoded payload bytes, and CRC
  outcomes cannot select the receiver or PHY mode for that frame.
- **Honest delivery:** a successful API return never ambiguously means both
  “queued” and “delivered.” Transfer states state exactly what was observed.
- **Honest rates:** coded PHY rate, scheduled payload rate, CRC-accepted airtime
  goodput, and wall-clock application goodput are distinct metrics.
- **Versioned boundaries:** library semantic version, C ABI version, wire
  protocol version, profile ID/hash, policy version, and diagnostic schema
  version are independent fields.
- **No silent fallback:** route change, resampling, profile mismatch, event
  overflow, and security downgrade are observable state changes.
- **Evidence before defaults:** a profile can ship as experimental before it is
  a default, but default-on support requires held-out qualification.

## Non-goals for 3.0

- A guaranteed increase over the 65.875 kbps Cyrinx 2 scheduled benchmark.
- True 2x2 MIMO, coherent distributed transmission, or N-mic claims beyond the
  currently implemented two-input receiver.
- A general fixed-3-foot, handheld, ultrasonic, or pleasant-audible guarantee.
- LDPC, polar coding, OTFS, or a new bit-loading algorithm without a promoted
  spike.
- Authenticated identity or confidentiality. The public API reserves security
  fields, but 3.0 remains explicitly unauthenticated unless the separate
  cryptographic program and external review complete.
- Background transfer while an iOS application is suspended.
- A general byte-stream API. Version 3.0 stabilizes bounded messages first; a
  stream adapter can be added after message semantics are qualified.
- Removal of host replay or Python research tools. They remain independent
  oracles and measurement referees, not runtime dependencies.

## Target architecture

```text
Application
    |
    | Swift async API / Kotlin suspend + Flow API
    v
Platform binding and session facade
    |
    | versioned commands, events, and snapshots
    v
Canonical C session reducer and policy
    |
    +-- robust discovery/control bearer
    +-- negotiated bulk bearer
    +-- selective recovery and link estimates
    |
    | bounded streaming PCM ingress/egress
    v
Apple audio adapter              Android audio adapter
RemoteIO / AVAudioEngine         AudioRecord / AudioTrack
```

The C transport is reducer-like: an input event and current state produce a new
state plus bounded effects to execute. Timers, clocks, randomness, and audio are
injected effects so deterministic replay can drive the same transitions as a
physical session. The Swift facade is an actor. The Kotlin facade serializes
commands in a coroutine scope and exposes events through `Flow`. Neither facade
contains an independent protocol state machine.

### Proposed package boundaries

The exact target names may change in the initial architecture decision record,
but the dependency direction must remain:

```text
CCyrinxDSP          portable FFT, bulk/bootstrap codecs, sounder metrics
    ^
CCyrinxCore         profiles, framing, streaming contexts, session, diagnostics
    ^
CyrinxCore          platform-neutral Swift value types and actor facade
    ^
CyrinxAppleAudio    RemoteIO and AVAudioEngine adapters
    ^
Cyrinx              compatibility umbrella product

CyrinxSimulation    deterministic clocks, fault links, and replay support
CyrinxExperimental  raw codecs, PHY stubs, debug and unqualified research APIs
```

`CCyrinxDSP` and `CCyrinxCore` may remain one installed native library if that
simplifies ABI distribution. They are separate ownership boundaries even if
the linker artifact is unified. FFT implementation headers stay private.

Android packages the same C library behind JNI in an AAR. The Apple audio
adapter is optional: applications may supply their own source and sink through
the documented PCM adapter contract.

## Pull-request rules

Every Cyrinx 3.0 PR follows these rules:

1. It has one primary behavioral outcome and can be reverted independently.
2. It keeps the default branch buildable and the existing 2.x API usable until
   the migration PR deliberately changes that contract.
3. New behavior is either unreachable from the stable API or protected by an
   explicit experimental/versioned entry point until its dependency gates pass.
4. It adds deterministic tests before relying on physical hardware evidence.
5. Public API, wire, ABI, profile, metric, or diagnostic changes update their
   specification and compatibility fixtures in the same PR.
6. A research spike and its shipping integration are separate PRs. A failed
   spike closes its integration PR rather than inviting unbounded retuning.
7. Hardware execution retains failures and exact provenance. Passing digital
   tests is never described as OTA qualification.
8. The PR description names the plan ID below, its hard dependencies, commands
   run, artifacts produced, and any remaining qualification gap.

## Dependency map

Arrows denote hard merge dependencies. Items on the same row may proceed in
parallel once their incoming dependency is complete.

```text
C3-01 contracts
  +-> C3-02 CI baseline
  +-> C3-03 ABI foundation -> C3-04 profiles -> C3-05 batch contract
  |                                             +-> C3-06 Swift v3
  |                                             +-> C3-07 test JNI
  |   C3-05 + C3-06 + C3-07 -> C3-08 conformance
  |
  +-> C3-28 chat contract -> C3-29 Apple offline chat
                         \-> C3-30 Android offline chat

C3-08 -> C3-09 streaming RX
C3-08 -> C3-10 queued TX (parallel with C3-09)
C3-09 -> C3-11 bootstrap spike
C3-09 + C3-10 + C3-11 -> C3-12 bootstrap in C
C3-09 + C3-10 + C3-12 -> C3-13 fault simulator

C3-09 + C3-10 + C3-08
  +-> C3-14 Apple audio adapter ----+
  +-> C3-15 Android JNI/audio ------+-> C3-16 on-device parity and soak

C3-08 -> C3-20a canonical measurements (dev may parallel Phases B-D;
                                        merging also requires C3-16)
C3-20a -> C3-21a capacity-predictor spike (must complete; integrates
                                           only past frozen thresholds)
C3-14 + C3-15 + C3-16 + C3-20a -> C3-20b capabilities/self-characterization

C3-12 + C3-13 -> C3-17 session engine -> C3-18 discovery
C3-18 + C3-16 -> C3-19 manual-profile messages
C3-18 + C3-19 + C3-20a + C3-20b -> C3-21 sounding/activation
C3-19 + C3-21 -> C3-22 selective ARQ
C3-21a + C3-22 -> C3-23 adaptation/recovery

C3-23 + C3-16
  +-> C3-24 Swift public API
  +-> C3-25 Kotlin public API
  +-> C3-26 diagnostics/support bundle

C3-24 + C3-25 + C3-26 -> C3-27 packaged SDK
C3-27 + C3-29 + C3-30 -> C3-31 live chat integration -> C3-32 chat HIL
C3-27 + C3-32 -> C3-33 qualification harness -> C3-34 held-out campaign
C3-34 -> C3-35 release candidate and migration
```

Documentation work is included in every PR and summarized later. It is not a
final downstream lane.

## Phase A — Contracts and continuous verification

### C3-01 — Freeze the 3.0 architecture and public semantic contract

**Depends on:** none.

**Scope**

- Add architecture decision records for canonical ownership, executor/thread
  ownership, on-device versus host-assisted processing, module boundaries, and
  the message-first public API.
- Specify lifecycle states, connection states, transfer states, terminal
  outcomes, event generations, and snapshot recovery after event loss.
- Specify the stable 3.0 API vocabulary without implementing it:
  `CyrinxTransport`, `CyrinxPeer`, `CyrinxConnection`, `CyrinxTransfer`,
  `LinkEstimate`, `SendOptions`, and structured errors.
- Define separate semantic, ABI, wire, profile, policy, and schema version axes.
- Inventory each public 2.x Swift and C symbol as retain, deprecate, move to
  experimental, or replace.

**Verification and documentation**

- Add a machine-readable API/ABI inventory checked for duplicate or unclassified
  public symbols.
- Add event and transfer-state diagrams with invalid transitions.
- Record the migration promise: 2.x shims remain until the final migration PR.

**Merge gate:** every existing public symbol is classified, every state has a
single owner, and no architecture section leaves thread ownership implicit.

### C3-02 — Establish the continuous-integration baseline

**Depends on:** C3-01 for required jobs and naming; it can develop in parallel
with C3-03.

**Scope**

- Add CI jobs for formatting, linting, `swift test`, the Accelerate parity
  suite, Android unit tests/build, portable C build/tests, and documentation
  link validation.
- Adopt Swift Testing as the default for new deterministic Swift unit,
  contract, state-machine, and async facade tests. Keep XCTest for XCUITest,
  `XCTMetric` performance tests, and unchanged legacy tests.
- Add one representative Swift Testing suite and prove that `swift test` and CI
  discover both frameworks. Keep test-framework migration mechanical and
  separate from production behavior; do not require a big-bang conversion.
- Define shared Swift Testing tags for critical contracts, conformance, and
  intentionally slow suites. Do not mix Swift Testing and XCTest in one file.
- Record Swift and C coverage separately. Coverage begins as an observed metric;
  only touched-file and new-code thresholds are blocking initially.
- Make compiler warnings visible as artifacts, then eliminate the existing
  warnings before enabling warnings-as-errors for new 3.0 targets.
- Add an explicit `.venv`-driven research-test command. Research tests remain a
  separate job and cannot make Python a shipping dependency.

**Verification and documentation**

- Test the workflow from a clean checkout and without untracked artifacts.
- Document the local equivalents of each CI job in `CONTRIBUTING.md`.
- Retain the toolchain, SDK, NDK, and dependency versions in job output.

**Merge gate:** a clean commit runs Swift Testing and existing XCTest suites,
plus all deterministic C and Android gates, and failures retain actionable
logs.

### C3-03 — Introduce the versioned C ABI foundation

**Depends on:** C3-01.

**Scope**

- Add a shared C base header containing fixed-width status, version, timestamp,
  allocator, clock, buffer-view, and opaque-context conventions.
- Introduce prefix-compatible `struct_size` and `abi_version` fields for new
  retained inputs and outputs. Accept `struct_size >= known_minimum`.
- Define explicit create/reset/process/snapshot/destroy ownership and
  thread-affinity contracts.
- Normalize `CYRINX_API` visibility and untangle circular public-header imports.
- Preserve current symbols; add versioned entry points rather than changing old
  layouts in place.

**Verification and documentation**

- Compile C and C++ header consumers with hidden visibility.
- Test undersized, exact-size, oversized/future, unknown-version, null, and
  allocator-failure cases.
- Add ABI layout assertions on supported 32-bit/64-bit Android and Apple ABIs.
- Document callback lifetime, ownership, and error precedence.

**Merge gate:** malformed version/size combinations fail without partial output,
and the existing 2.x ABI still builds its consumer fixture.

### C3-04 — Add the canonical profile registry and wire identity

**Depends on:** C3-03.

**Scope**

- Replace borrowed rate strings and ad hoc configuration tuples in new APIs with
  typed modulation, code rate, band, FFT/CP, pilot, block, and diversity fields.
- Give every immutable bootstrap and bulk profile a stable on-wire ID and a
  canonical content hash.
- Classify profiles as compatibility, qualified-on-specific-route,
  experimental, or internal test fixture.
- Preserve the accepted Cyrinx 2 schedule as a frozen comparison class.
- Generate Swift and Kotlin profile value types from the canonical registry or
  prove them against the same registry fixture.

**Verification and documentation**

- Round-trip every profile through canonical serialization.
- Reject unknown IDs, hash mismatch, invalid geometry, unsupported enum values,
  and out-of-range dimensions.
- Add profile tables describing evidence scope and prohibited claims.

**Merge gate:** one registry drives C, Swift, Kotlin/JNI tests, benchmark
manifests, and documentation without hand-copied profile constants.

### C3-05 — Define the canonical batch capture/result contract

**Depends on:** C3-04.

**Scope**

- Add coarse-grained C batch encode/decode entry points around the shipping bulk
  PHY, retaining batch decode for tests, replay, and host diagnostics.
- Return ordered block-validity masks, selected input and reason, timing,
  clipping/non-finite evidence, EVM/pilot residual summaries, profile identity,
  and exact consumed/produced sample ranges.
- Accept explicit mono or interleaved/strided input layouts with sample count,
  channel count, channel order, monotonic start index, and discontinuity flags.
- Ensure metrics never require expected payload bytes.

**Verification and documentation**

- Extend golden vectors across current compatibility and Cyrinx 2 profiles,
  mono, MRC rescue, automatic-primary selection, malformed inputs, and partial
  block recovery.
- Compare direct C and the frozen Python oracle with declared numeric tolerances.
- Document which fields are decoding evidence versus policy decisions.

**Merge gate:** the result identifies every accepted or missing block in order,
and current payload/count results remain unchanged on frozen fixtures.

### C3-06 — Add the Swift v3 value binding and 2.x compatibility shim

**Depends on:** C3-05.

**Scope**

- Bind the C batch and profile contracts with immutable `Sendable` Swift value
  types and typed errors.
- Replace optional-as-error behavior in the new surface with explicit error
  cases; reserve `nil` for genuine absence.
- Nest generic names such as configuration, event, role, and metrics under their
  owning 3.0 types.
- Keep `BulkPHY` and `CyrinxSession` source-compatible during development, with
  deprecation annotations only after replacement coverage exists.

**Verification and documentation**

- Add Swift Testing parameterized tests for every profile/result/error mapping.
- Verify no Swift wrapper recomputes DSP or policy results.
- Start a 2.x-to-3.x migration guide with side-by-side batch examples.

**Merge gate:** Swift is a lossless representation of the C result, including
unknown future values and block masks, and adds no `@unchecked Sendable` escape.

### C3-07 — Add the test JNI binding

**Depends on:** C3-05.

**Scope**

- Add CMake/NDK plumbing and a minimal JNI wrapper for profiles, batch encode,
  batch decode, and result extraction.
- Keep JNI coarse-grained: copy or pin bounded arrays once per operation rather
  than crossing JNI for individual symbols or bins.
- Define Java/Kotlin exception and resource-lifetime mapping.

**Verification and documentation**

- Run golden fixtures through direct C and JNI on the host/NDK test runner.
- Test invalid direct buffers, wrong endianness/layout, closed handles, repeated
  close, allocation failure, and unknown result versions.
- Document JNI ownership and the fact that this is conformance plumbing, not yet
  the production audio adapter.

**Merge gate:** JNI payloads and block masks are byte-identical to direct C and
metrics satisfy the same declared tolerances.

### C3-08 — Establish cross-binding conformance and retained replay

**Depends on:** C3-05, C3-06, and C3-07.

**Scope**

- Build one runner that executes direct C, Swift, JNI, KISS FFT, and Accelerate
  against the same manifest.
- Add a small committed PCM replay set and content-addressed references to the
  larger retained corpus.
- Record tolerances per field rather than applying one global floating-point
  tolerance.
- Emit a machine-readable parity report for CI and support diagnostics.

**Verification and documentation**

- Include truncation, non-finite samples, duplicate channels, swapped channel
  order, dropout, clipping, unknown profile, MRC rescue, and receiver-selection
  fixtures.
- Run C parsers and batch demodulation under ASan and UBSan.
- Document fixture provenance, generation, permitted regeneration, and leakage
  boundaries.

**Merge gate:** all supported bindings make the same ordered block decision on
every fixture, and a deliberate binding divergence fails CI.

## Phase B — Streaming PHY and deterministic simulation

### C3-09 — Add the bounded streaming receiver

**Depends on:** C3-08.

**Scope**

- Add an opaque receiver context with push/copy, process, drain, snapshot,
  reset, and destroy operations.
- Accept arbitrary chunk boundaries down to one sample and retain the minimum
  bounded synchronizer history needed across calls.
- Track monotonic sample indices, timestamps, channel layout, discontinuities,
  overflow, acquisition, and reacquisition.
- Keep correlation, FFT, allocation, logging, and application callbacks off the
  real-time audio callback.

**Verification and documentation**

- Replay every batch fixture under deterministic random chunking, split chirps,
  split symbols, inserted silence, zero-gap frames, duplication, dropout, and
  clock-skewed chunks.
- Require exact ordered-block parity with batch decode for unmodified captures.
- Add memory/high-water and correlation-history assertions.
- Document which operations are real-time safe and which require the worker.
- Measure sustained streaming-demodulation throughput on the qualified Mac
  directly and on the Pixel 7a through the C3-07 NDK test runner, retaining
  CPU and allocation traces (Roadmap Rank 2 gate).

**Merge gate:** 1,000 continuously replayed frames produce no duplicate or
missing delivery, memory remains bounded, reacquisition meets the declared
frame/time bound, and streaming demodulation sustains at least 4x real time
on both the qualified Mac and the Pixel 7a with retained CPU/allocation
traces. C3-12 and C3-13 do not start until this performance gate holds.

### C3-10 — Add the queued streaming transmitter

**Depends on:** C3-08; may develop in parallel with C3-09 but both are required
before platform integration.

**Scope**

- Add an opaque transmitter context with bounded message/profile enqueue,
  prepare, render-PCM, cancel, flush, snapshot, reset, and destroy operations.
- Render into arbitrary output-buffer sizes without allocation on the callback.
- Own exact preamble, gap, transition, and tail-padding accounting.
- Report queue acceptance separately from first rendered sample, final rendered
  sample, and cancellation.

**Verification and documentation**

- Compare irregular render requests with the frozen batch waveform sample for
  sample, including one-sample buffers and split tail padding.
- Test queue full, cancellation before/during render, reset, underrun, and route
  invalidation.
- Measure render-path cost on the same Mac and Pixel 7a targets as C3-09 and
  retain the traces; TX must fit inside the same 4x real-time budget.
- Document producer/consumer ownership and backpressure.

**Merge gate:** render chunking cannot alter emitted PCM or transfer state, the
callback path allocates neither heap objects nor log strings, and rendering
sustains the same 4x real-time budget as C3-09 on the qualified Mac and the
Pixel 7a with CPU and allocation traces retained.

### C3-11 — Select the canonical robust bootstrap bearer

**Depends on:** C3-09 for the target streaming contract. This is a bounded spike
PR and changes no shipping default.

**Scope**

- Preregister and compare the legacy D-CSS control bearer with the RS-coded MFSK
  floor on acquisition latency, false positives, net control goodput,
  reverberation, narrowband interference, and implementation cost.
- Include discovery-sized beacons, ACK-sized messages, back-to-back
  control/bulk transitions, and hour-long room-tone replay.
- Freeze a decision rule for selecting exactly one initial bootstrap bearer.

**Verification and documentation**

- Retain manifests, seeds, captured failures, decision output, and stop reason.
- Verify the candidate through the streaming chunk harness, not only a batch
  decoder.
- Record whether the result promotes a candidate or retains the incumbent.

**Merge gate:** the PR ends with one evidence-backed bootstrap choice. If no new
candidate passes, the conservative incumbent is selected explicitly; failure
does not block the critical path or authorize a third unbounded candidate.

### C3-12 — Implement the selected bootstrap bearer in C

**Depends on:** C3-11 and C3-09/C3-10.

**Scope**

- Port the selected bootstrap TX and streaming RX into the canonical C contexts.
- Support discovery beacon, capability fragments, ACK/control messages, and
  transition to/from the bulk receiver without dropping boundary samples.
- Remove bootstrap DSP from future Swift/Kotlin runtime paths while retaining
  old implementations as comparison fixtures until platform parity completes.

**Verification and documentation**

- Add cross-binding golden vectors and arbitrary-chunk tests.
- Measure false acquisition and recovery against the retained noise/reverb set.
- Document payload limits, coded geometry, timing, and audibility class.

**Merge gate:** C, Swift, and JNI interoperate for all bootstrap message classes,
including split and back-to-back control/bulk captures.

### C3-13 — Build the deterministic faulting simulator

**Depends on:** C3-09, C3-10, and C3-12.

**Scope**

- Replace real-clock sleeps and lossless direct frame delivery in the 3.0 test
  path with injected monotonic clocks, seeded randomness, and virtual PCM links.
- Model delay, loss, corruption, duplication, reordering, chunk jitter, ACK
  loss, bounded queue pressure, clock offset/skew, route change, interruption,
  and peer disappearance.
- Support deterministic event-trace and support-bundle output.
- Keep fast symbol/packet abstractions for state-machine tests, plus PCM mode for
  end-to-end modem tests.

**Verification and documentation**

- Require byte-identical results for a given seed and explicit different traces
  for deliberately changed faults.
- Test simulator faults themselves with controlled fixtures.
- Document what the simulator models and, importantly, what only OTA can show.

**Merge gate:** session work can test timeouts, cancellation, recovery, and
backpressure without `sleep`, wall-clock dependence, or physical audio.

## Phase C — On-device audio and bindings

### C3-14 — Implement the Apple PCM adapter

**Depends on:** C3-08, C3-09, and C3-10.

**Scope**

- Split Apple route/session management, PCM queues, resampling, and diagnostics
  out of the existing monolithic audio scaffold.
- Feed captured PCM once into the canonical C streaming receiver and render C
  transmitter output directly.
- Preserve available channel separation and record actual route, sample rate,
  layout, buffer loss, clipping, interruption, and lifecycle state.
- Make native 48 kHz the qualified default. Any resampler use is explicit,
  measured, and invalidates cached calibration.

**Verification and documentation**

- Test queue and adapter logic with fake audio units; use platform tests only for
  Apple framework integration.
- Add route-change, interruption, permission denial, mono/stereo, duplicated
  input, underrun, overflow, cancellation, and restart tests.
- Remove `@unchecked Sendable` from the supported path through isolation rather
  than annotation.
- Document audio-session ownership and application integration responsibilities.

**Merge gate:** the same captured PCM yields the same C result on-device and in
host replay, with no unexplained sample insertion, loss, or channel duplication.

### C3-15 — Implement the Android JNI and PCM adapter

**Depends on:** C3-07, C3-09, and C3-10; may develop in parallel with C3-14.

**Scope**

- Promote the JNI wrapper into an internal AAR and connect stereo `AudioRecord`
  plus `AudioTrack` to the canonical streaming contexts.
- Preserve direct logical inputs, explicit audio source, realized route,
  channel mask, sample rate, frame position, and queue discontinuities.
- Add app-owned, confirmed, restorable volume handling for HIL; never silently
  claim a physical speaker route that Android cannot prove.
- Keep the Kotlin modem available only as a legacy comparison until C parity and
  soak tests pass.

**Verification and documentation**

- Add JVM tests for Kotlin mapping and instrumentation/NDK tests for JNI,
  lifecycle, buffer, and route behavior.
- Test permission denial, source fallback, mono/stereo, duplicated channels,
  short reads/writes, queue overflow, route change, cancellation, and restart.
- Document supported ABIs, ProGuard/R8 rules, threading, and audio source choice.

**Merge gate:** Pixel on-device decode matches host replay for the same PCM and
the accepted path contains no Kotlin DSP decision.

### C3-16 — Prove on-device parity and platform soak stability

**Depends on:** C3-14 and C3-15.

**Scope**

- Run one captured input through on-device and host replay on each platform.
- Complete at least one physical over-the-air smoke transfer per platform —
  the Pixel 7a and one Apple mobile target at minimum — encoding, emitting,
  capturing, and decoding acoustically on-device with no host DSP in the loop
  (Roadmap Rank 3 outcome; replay parity and soak alone do not demonstrate it).
- Exercise exact logical speaker and microphone selection where the OS exposes
  it, and record inability where it does not.
- Run 30-minute simultaneous capture/playback soaks on the qualified Mac, Apple
  mobile target, and Pixel.
- Disable selection of duplicate live bulk decoders in release builds after
  parity passes; retain them only in experimental/test targets.

**Verification and documentation**

- Compare ordered blocks and metric tolerances.
- Retain CPU, allocation, thermal, queue high-water, dropout, route, and lifecycle
  traces.
- Require at least 4x real-time sustained processing on the qualified Mac and
  Pixel for the declared profile.
- Add a platform capability/support table.

**Merge gate:** each platform completes on-device encode/decode of a physical
over-the-air smoke transfer — not only replayed PCM — without host signal
processing, including the Pixel 7a and one Apple mobile target, and every soak
ends without unexplained sample loss, dead route, or unreported overflow.

## Phase D — Session, discovery, negotiation, and reliability

### C3-17 — Replace the prototype session with a serialized C engine

**Depends on:** C3-12 and C3-13. It may develop in parallel with C3-14/C3-15.

**Scope**

- Implement explicit session input events, pure transition logic where
  practical, and bounded effects for timers, TX enqueue, snapshots, and events.
- Replace blocking ACK polling, direct application callbacks, process-global
  throttles, and synthetic goodput mutation.
- Add a bounded C event queue with monotonic generation/timestamp, coalescing for
  high-rate metrics, and sticky overflow generation outside the queue.
- Define explicit start, stop, restart, cancel, reset, and destroy behavior.

**Verification and documentation**

- Snapshot every valid transition and reject invalid commands deterministically.
- Inject timer races, simultaneous commands, queue overflow, cancellation, and
  destruction with pending effects.
- Run TSan where supported and sanitizer builds for C.
- Document the executor and callback contract.

**Merge gate:** all state mutation is serialized and replayable, no application
callback occurs on the audio thread, and no test uses wall-clock sleeping.

### C3-18 — Implement discovery and collision-safe role election

**Depends on:** C3-17.

**Scope**

- Define the versioned small beacon: service hash, ephemeral peer ID,
  protocol-version range, capability hash, and collision/role nonce.
- Add listen-before-talk, randomized bounded slots, duplicate suppression,
  expiration, and deterministic nonce-based role election.
- Emit peer found/updated/lost events with explicit reasons and generation.

**Verification and documentation**

- Simulate two and multiple simultaneous advertisers, collisions, duplicates,
  peer disappearance, nonce ties, version mismatch, and cancellation.
- Run a long noise corpus with no false peer event meeting the declared
  confidence threshold.
- Document peer identity as ephemeral and unauthenticated.

**Merge gate:** two symmetric peers converge on complementary roles or a bounded
failure without persistent collision or deadlock.

### C3-19 — Deliver manual-profile best-effort messages

**Depends on:** C3-18 and C3-16 for the physical demonstration.

**Scope**

- Implement bounded message IDs, fragmentation, reassembly, duplicate
  suppression, cancellation, missing-block maps, and queue backpressure using a
  manually selected conservative bulk profile.
- Add transfer states for accepted, rendered, best-effort complete, partial, and
  failed. Do not call a best-effort result reliably delivered.
- Preserve robust control slots while the bulk burst is active.

**Verification and documentation**

- Test empty/min/max messages, fragment boundaries, missing/duplicate/reordered
  blocks, cancellation at each state, queue full, peer loss, and restart.
- Attempt 64 KiB and 1 MiB simulator and physical best-effort transfers and
  report exact delivered/missing maps.
- Document best-effort semantics and limits.

**Merge gate:** no message is silently truncated or incorrectly declared
complete, and a sample command-line consumer can transfer through the real bulk
plane.

### C3-20a — Port canonical passive and active measurements to C

**Depends on:** C3-08 for development, so work may proceed in parallel with
Phases B through D; merging additionally requires C3-16, because Roadmap
Spike 5a depends on Ranks 1 AND 3 — measurement results are not declared
complete on an audio path C3-16 has not proven on-device.

**Scope**

- Port passive room-tone analysis and the known-waveform sounder behind a
  canonical C measurement API operating on caller-supplied PCM.
- Estimate complex per-bin channel response, noise covariance, delay spread,
  timing/CFO/SRO, pilot EVM, clipping, and nonlinear products.
- Emit a same-session capacity report that records assumed bandwidth, power
  constraint, active-bin mask, noise model, protocol overhead, and confidence;
  never publish an unqualified single "Shannon limit."

**Verification and documentation**

- Recover known parameters from synthetic channels within fixture-specific
  tolerances; match retained captures against the frozen Python oracle.
- Prove estimates never consume payload decisions or expected bytes.
- Document metric semantics in the result/metric reference.

**Merge gate:** synthetic and retained-capture fixtures pass with declared
tolerances, the API is exercised from C, Swift, and the test JNI binding, and
C3-16's on-device parity/soak/OTA evidence has landed.

### C3-20b — Add capabilities and local route self-characterization

**Depends on:** C3-14, C3-15, C3-16, and C3-20a (not C3-19:
self-characterization needs local audio adapters and proven on-device routes,
not the message plane).

**Scope**

- Replace the legacy fixed capability payload with extensible versioned TLVs.
- Represent speakers and microphones independently, including verified logical
  port, source, route, sample rate, channel layout, supported profiles, buffer
  limits, and calibration hash.
- Add local speaker-by-microphone checks for duplication, cross-talk, response,
  clipping, nonlinear products, route identity, and safe digital gain envelope.
- Key cached results by device, OS build, route, source, sample rate, and adapter
  version; invalidate on material change.

**Verification and documentation**

- Round-trip unknown TLVs and safely reject malformed lengths/version ranges.
- Test duplicate/mixed channels, source fallback, route change, stale cache, and
  independent speaker/microphone counts.
- Document that logical OS channels are not unverified physical microphone
  claims.

**Merge gate:** a session cannot advertise or select a port/profile that the
realized local route has not verified.

### C3-21 — Integrate bidirectional sounding and profile activation

**Depends on:** C3-18, C3-19, C3-20a, and C3-20b (Roadmap: cross-device
sounding and negotiated activation depend on Rank 4's control/message plane
plus the promoted 5a/5b measurements; C3-16 arrives transitively through
C3-20a/C3-20b).

**Scope**

- Drive the C3-20a measurement API over the live control session to sound every
  verified A-to-B and B-to-A path independently and retain channel,
  noise, delay, timing/CFO/SRO, EVM, clipping, and confidence evidence.
- Negotiate directional band, CP, MCS/FEC, diversity, control profile, and
  profile hash.
- Add proposed/accepted activation sequence and effective frame boundary;
  return to bootstrap if either peer misses activation.

**Verification and documentation**

- Validate synthetic channels against known parameters and retained captures
  against the Python oracle with field-specific tolerances.
- Test unknown TLVs, asymmetric capabilities, stale calibration, activation
  loss/replay, profile mismatch, route change during sounding, and timeout.
- Measure and expose setup/sounding overhead.

**Merge gate:** both peers activate the same immutable directional profiles or
recover through bootstrap without deadlock; no payload outcome enters sounding.

### C3-21a — Bounded capacity-predictor spike

**Depends on:** C3-20a. This is a bounded spike PR (plan rule 6): it changes no
shipping default. C3-23 depends on its COMPLETION — the frozen evaluation must
run and record an outcome, including a negative one in
`docs/NEGATIVE_FINDINGS.md`, before adaptation defaults freeze — but C3-23
integrates the predictor only if it clears the promotion thresholds below.

**Scope**

- Preregister and compute three quantities per sounded route: the
  log-det/Shannon upper bound; bitwise generalized mutual information from
  randomized, data-representative known QAM probes with representative PAPR and
  actual LLRs; and the scheduled ceiling after CP, pilots, FEC, preamble, and
  gaps.
- Fit any probe-to-block-loss mapping on whole runs/devices and hold out a
  preregistered minimum set of whole physical cells/devices.

**Verification and documentation**

- Retain manifests, seeds, per-cell predictions versus delivered goodput, and
  the frozen promotion analysis.
- Record the outcome in `docs/NEGATIVE_FINDINGS.md` if the predictor fails.

**Merge gate (promotion thresholds, frozen before held-out data):** the
predictor enters C3-23 adaptation only if median held-out goodput error is at
most 15%, it selects a profile within 10% of the measured oracle in at least
80% of held-out cells, and it never chooses below the declared recovery
objective (initially 98%) when a qualifying profile exists — selecting below
90% is a zero-tolerance catastrophic miss. If predictions remain more than 20%
optimistic or held-out Spearman correlation with delivered goodput is below
0.5, the calculation is retained as a descriptive bound only, and the spike
stops rather than retuning indefinitely.

### C3-22 — Add selective ARQ and reliable message delivery

**Depends on:** C3-19 and C3-21.

**Scope**

- Add selective ACK bitmaps, bounded retransmission windows, ACK-loss recovery,
  retry exhaustion, duplicate suppression, and terminal reliable-delivery
  receipts.
- Keep retransmission scheduling compatible with robust control reservations and
  profile activation boundaries.
- Define cancellation and peer-loss semantics for queued, on-air, acknowledged,
  and terminal transfers.

**Verification and documentation**

- Fault-inject every data/ACK loss and duplication location, wraparound,
  cancellation, backpressure, restart, and retry exhaustion.
- Require hash-correct completion or an explicit terminal failure; never expose
  partial bytes as a reliable success.
- Document delivery evidence and why acknowledgment is not authenticated peer
  identity.

**Merge gate:** all declared simulator loss classes either complete hash-correct
within bounds or terminate with an exact reason and no leaked session state.

### C3-23 — Close the adaptation and recovery loop

**Depends on:** C3-21, C3-22, and completion of C3-21a (whose predictor is
integrated only if promoted; a recorded negative outcome also satisfies the
dependency).

**Scope**

- Define a versioned replaceable policy with one conservative built-in default.
- Select directional speaker/input, primary versus MRC, CP, pilot cadence,
  MCS/FEC, active mask, coherent tier, or non-coherent floor from causally prior
  evidence. The C3-21a predictor participates in these selections only if it
  cleared its frozen promotion thresholds; otherwise its outputs remain
  descriptive diagnostics.
- Add hysteresis, hold times, confidence/age, lower-bound estimates, degradation,
  robust fallback, recovery, and resounding triggers.
- Distinguish configured, scheduled, accepted-airtime, and application goodput.

**Verification and documentation**

- Replay preregistered quiet, HVAC, reverberant, shadowed, moved, and route-change
  sequences against the best fixed conservative policy.
- Add leakage tests that mutate payload/data bins/current CRC and prove the same
  receiver/profile decision.
- Calibrate advertised lower bounds against subsequent delivery windows.
- Document each event reason and application guidance.

**Merge gate:** held-out simulator/replay sequences improve the declared
lower-tail objective without transfer failure, and missed profile activation
always returns to bootstrap.

## Phase E — Public SDK, diagnostics, and packaging

### C3-24 — Ship the Swift actor API

**Depends on:** C3-23 and C3-16.

**Scope**

- Implement `CyrinxTransport` as the single Swift actor facade over the C command,
  event, and snapshot APIs.
- Expose peer discovery, connection lifecycle, transfer receipts/status updates,
  incoming messages, link estimates, cancellation, and explicit shutdown.
- Drain C events on a non-audio executor using bounded `AsyncStream` policies;
  detect generation gaps and refresh authoritative snapshots.
- Inject the audio adapter, clock where applicable, and diagnostic sink rather
  than creating hidden global dependencies.

**Verification and documentation**

- Add Swift Testing async tests for success, errors, event ordering, buffer
  overflow, snapshot refresh, cancellation, deallocation, repeated start/stop,
  and concurrent callers. Use confirmations and injected clocks rather than
  sleeps.
- Compile with strict concurrency and no `@unchecked Sendable` on public session
  types.
- Add DocC tutorials for discovery, send/receive, failure, and custom audio.

**Merge gate:** the actor cannot race the C context, every async sequence has a
documented termination/buffering rule, and terminal transfer events are never
lost silently.

### C3-25 — Ship the Kotlin coroutine and Flow API

**Depends on:** C3-23 and C3-16; may develop in parallel with C3-24.

**Scope**

- Add Kotlin resource-safe transport, connection, and transfer wrappers over
  JNI.
- Serialize commands in an owned coroutine scope and expose bounded `Flow`
  streams for events, incoming messages, and status.
- Map cancellation, close, native error, unknown enum/version, and event overflow
  without duplicating protocol state.

**Verification and documentation**

- Add coroutine tests with virtual time for lifecycle, flow backpressure,
  cancellation, generation gaps, close races, and native exceptions.
- Run Swift/Kotlin API-behavior scenarios from shared machine-readable traces.
- Add KDoc and Android integration documentation.

**Merge gate:** Swift and Kotlin expose equivalent state/delivery semantics and
produce matching traces for the same simulator scenario.

### C3-26 — Add structured diagnostics and replayable support bundles

**Depends on:** C3-23; public mapping integrates with C3-24/C3-25.

**Scope**

- Define a versioned support-bundle manifest containing route/source/channel
  provenance, profile and negotiation transcript, event timeline/generation
  gaps, PCM queue drops, block maps, sounder metrics, policy decisions, and
  separate goodput counters.
- Make bounded raw PCM opt-in. Redact message payloads, private key material, and
  future security secrets by construction.
- Add failure categories for route, sync, clipping, FEC, MAC, queue, lifecycle,
  compatibility, and cancellation.
- Add an offline replay command that verifies hashes and replays relevant PCM
  through the canonical C receiver.

**Verification and documentation**

- Induce every failure category and require a parseable bundle with enough
  information to reproduce or explicitly explain why PCM was unavailable.
- Add schema-forward-compatibility and redaction tests.
- Document privacy, retention, size limits, and safe issue-reporting workflow.

**Merge gate:** no default bundle contains payload bytes or secrets, and every
bundle validates independently of the source checkout.

### C3-27 — Produce distributable SDK artifacts and clean consumers

**Depends on:** C3-24, C3-25, and C3-26.

**Scope**

- Publish the SwiftPM products, optional XCFramework, installable CMake/pkg-config
  package, headers, Android AAR/JNI ABIs, symbols, licenses, and SBOM.
- Add clean external Apple, Android, and C consumer fixtures that use release
  artifacts rather than repository-relative paths.
- Add reproducible archive and checksum generation, semantic/ABI/wire
  compatibility checks, and release signing hooks without requiring signing for
  ordinary PR tests.

**Verification and documentation**

- Build every clean consumer from the candidate artifact.
- Run golden and retained-replay conformance against packaged binaries.
- Verify exported symbol allowlists, notices, minimum OS/API levels, and R8 rules.
- Publish installation, permissions, threading, and upgrade documentation.

**Merge gate:** the exact package a developer downloads passes conformance and
does not depend on the repository layout or an untracked generated file.

## Phase F — Chat sample application

The chat sample is a product-level acceptance client, not a second HIL panel.
It is intentionally small enough that transport behavior remains visible.

### Chat product scope

- Discover peers advertising the Cyrinx Chat service.
- Connect to one peer and exchange UTF-8 text messages.
- Show queued, transmitting, acknowledged/delivered, and failed state for every
  outgoing message.
- Show connection state plus a coarse directional link budget (`controlOnly`,
  `text`, `thumbnail`, or `bulk`) backed by the numeric `LinkEstimate`.
- Explain permission, route, peer-loss, degradation, recovery, and repositioning
  actions in plain language.
- Show a persistent **Unauthenticated acoustic link** notice. The sample must not
  invite users to send secrets.
- Offer a deterministic simulated transport so the sample and UI tests work
  without microphones, speakers, or two devices.
- Export a redacted support bundle from an advanced diagnostics sheet.

The 3.0 sample stores conversation state in memory only. It does not implement
accounts, cloud sync, background delivery, push notifications, read receipts,
typing indicators, contact identity, attachments, or encryption.

### Chat message envelope

The sample owns a compact, versioned application envelope separate from the
Cyrinx transport wire protocol. Version 1 contains:

- envelope version and message kind;
- 128-bit random message ID;
- sender's current ephemeral peer ID;
- UTF-8 body length and body; and
- optional reply-to message ID reserved but absent by default.

The body is capped at 2 KiB for 3.0. Sender wall-clock time is display metadata,
not delivery ordering evidence, because peer clocks are not assumed
synchronized. Cross-language golden vectors pin valid, malformed, maximum,
Unicode, duplicate-ID, and unknown-version envelopes.

### Sample architecture

```text
Apple ChatModel (@MainActor, @Observable)   Android ChatViewModel (StateFlow)
                \                          /
                 ChatTransportClient contract
                    +-- Simulated client
                    +-- Cyrinx 3 live adapter
```

The UI models consume a small injected application protocol and do not call C or
JNI directly. They own display state, while `CyrinxTransport` owns protocol
state. The sample does not add TCA or another application architecture
dependency.

### C3-28 — Define the chat contract, envelope, and simulator client

**Depends on:** C3-01 only. This PR may run while the core is being built.

**Scope**

- Add shared machine-readable chat-envelope fixtures plus Swift and Kotlin
  codecs.
- Define peer, conversation, message, display-status, and `ChatTransportClient`
  application contracts on each platform.
- Implement deterministic in-process paired simulated clients driven by seeded
  peer, link, delivery, degradation, and failure scripts.
- Define accessibility identifiers and launch arguments before UI work.

**Verification and documentation**

- Cross-check Swift and Kotlin encoded bytes against every golden vector.
- Test invalid UTF-8, oversize, duplicate ID, unknown version/kind, and seeded
  failure behavior.
- Add `Apps/Chat/README.md` with scope, non-security warning, and simulator use.

**Merge gate:** both platforms can exchange the same chat envelopes in tests
without importing the live Cyrinx SDK.

### C3-29 — Build the Apple offline chat app

**Depends on:** C3-28.

**Scope**

- Add iOS and macOS SwiftUI targets with peer browser, connection banner,
  conversation list, composer, per-message status, error/recovery presentation,
  and diagnostics sheet.
- Use a small `@MainActor @Observable` model with injected
  `ChatTransportClient` and no transport logic in views.
- Support simulator scenarios through launch arguments and previews.
- Include microphone permission text now, but keep physical audio disabled until
  C3-31.

**Verification and documentation**

- Add Swift Testing model tests for event projection, send failure/retry,
  duplicate suppression, peer loss, cancellation, and teardown.
- Add XCUITest page objects for discovery, connect, send, receive, degraded,
  permission-denied, and failed-message flows.
- Check VoiceOver labels, Dynamic Type, keyboard operation on macOS, contrast,
  and status meaning without color alone.

**Merge gate:** all user flows pass deterministically with the simulated client
and no test requires audio hardware or sleeps.

### C3-30 — Build the Android offline chat app

**Depends on:** C3-28; may develop in parallel with C3-29.

**Scope**

- Add an Android application with the same peer, connection, conversation,
  composer, status, recovery, and diagnostics behavior.
- Use a lifecycle-aware ViewModel/StateFlow boundary over the injected simulated
  client.
- Support seeded scenarios through intent extras or instrumentation arguments.
- Request no physical audio permission until the live adapter is enabled.

**Verification and documentation**

- Add JVM ViewModel tests with virtual coroutine time and instrumentation UI
  tests for the same shared behavior scenarios as Apple.
- Test rotation/recreation, background/foreground while not transferring,
  TalkBack labels, font scaling, and status meaning without color alone.
- Record intentional platform-specific UX differences.

**Merge gate:** Apple and Android scenario traces agree on message and transfer
state even when presentation differs.

### C3-31 — Connect both chat apps to the packaged live SDK

**Depends on:** C3-27, C3-29, and C3-30.

**Scope**

- Add live `ChatTransportClient` adapters using only the public packaged Swift
  and Kotlin APIs.
- Implement permission and route preflight, discovery, connect, incoming
  messages, outgoing transfer status, cancellation, peer loss, and restart.
- Keep simulator mode selectable so development remains hardware-independent.
- Add the unauthenticated-link notice to every live conversation.

**Verification and documentation**

- Run public-API contract tests against simulated and live adapters.
- Test that support-bundle payload redaction covers chat bodies and IDs where
  required by the privacy policy.
- Add a two-device quick start and troubleshooting guide.

**Merge gate:** clean packaged artifacts, rather than repository internals, let
an Apple and Android app exchange text and show correct terminal delivery state.

### C3-32 — Automate chat HIL and product acceptance

**Depends on:** C3-31 and C3-26.

**Scope**

- Add tokenized automation to start peers, wait for discovery/connection, send a
  seeded sequence, inject permitted route/movement interventions, and export
  results/support bundles.
- Define separate Mac-to-Android, Mac-to-iOS, and Android-to-Apple scenarios.
- Retain message IDs, hashes, status timelines, link estimates, route provenance,
  and exact denominators without retaining chat text by default.

**Verification and documentation**

- Require bidirectional seeded message sets to arrive once, byte-identical and
  in transport order, or end in an explicit failure state.
- Exercise peer disappearance, cancellation, degradation/fallback, recovery,
  and route change.
- Capture release screenshots only after the underlying run passes its evidence
  gate; screenshots are not test evidence themselves.

**Merge gate:** the chat sample is both a comprehensible integration example and
a repeatable public-API acceptance test, without private HIL hooks in the SDK.

## Phase G — Qualification and release

### C3-33 — Package the qualification harness and held-out corpus contract

**Depends on:** C3-27 and C3-32.

**Scope**

- Define a preregistered device/route/pose/environment/day matrix covering at
  least two Mac hardware families, Pixel, one Apple mobile target, relevant
  Android sources, face-up/down, modest offsets, and HVAC strata.
- Freeze profile-selection policy and scoring before held-out evaluation.
- Package public-safe PCM or content-addressed external inputs, route signatures,
  manifests, block maps, code/config hashes, and negative findings.
- Define separate near-field, fixed-distance, handheld, ultrasonic, and
  pleasant-audible benchmark classes; only near-field is a 3.0 blocker.

**Verification and documentation**

- Machine-validate job counts, ordering balance, unique seeds, training/holdout
  separation, target provenance, and analysis hashes before playback.
- Dry-run every job without emitting audio.
- Publish the scoring and claims-integrity rubric before results exist.

**Merge gate:** the harness can prove it will not silently skip, replace, pool,
or reorder a failed cell, and the analysis cannot inspect held-out outcomes
before the declared freeze.

### C3-34 — Run held-out qualification and freeze defaults

**Depends on:** C3-33. This PR is evidence-heavy and does not change algorithms
after held-out data are opened.

**Scope**

- Execute the frozen platform, transfer, chat, and link-adaptation campaigns.
- Require every declared fast/robust 1 MiB transfer to complete hash-correct;
  use the smaller preregistered gates for slow coherent and 138 bps recovery
  tiers.
- Promote a fast default only if recovery reaches the declared 99.9% target or
  satisfies the one-sided 95% noninferiority margin of at most 0.1 percentage
  point versus control, with zero planned hash-transfer failures and better p10
  goodput.
- Record setup, sounding, control, retransmission, fallback, recovery, and wall
  time in application goodput.

**Verification and documentation**

- Independent analysis reproduces every aggregate from retained per-slot data.
- Failures remain in the corpus and `NEGATIVE_FINDINGS.md`.
- Publish exact supported devices/routes and an explicit unsupported/unknown
  table.

**Merge gate:** defaults and support claims follow the frozen rubric. A failed
gate produces an experimental or conservative default, not a rewritten claim.

### C3-35 — Cut the 3.0 release candidate and migration surface

**Depends on:** C3-34.

**Scope**

- Remove or move superseded public stubs/debug/raw codecs to the experimental
  product according to C3-01's inventory.
- Finalize 2.x deprecations and compatibility shims; remove only symbols whose
  major-version removal was planned and documented.
- Freeze semantic, ABI, wire, profile, policy, and schema versions.
- Build release artifacts, SBOM, checksums, support matrix, sample apps, and
  release notes from a clean tag candidate.

**Verification and documentation**

- Run every CI, conformance, sanitizer, consumer, simulator, performance, HIL,
  and qualification gate against the exact release artifacts.
- Run migration examples from 2.0 source and verify expected warnings/errors.
- Perform a claims audit against README, API docs, chat UI, release notes,
  website, and retained evidence.
- Document known defects, unsupported modes, security status, and rollback.

**Merge gate:** the downloadable artifacts reproduce the qualified behavior,
all public claims trace to retained evidence, and no supported path selects host
DSP or a duplicate live modem implementation.

## Testing plan

Cyrinx 3.0 uses a deliberate hybrid Swift test strategy:

- Swift Testing is the default for all new deterministic Swift unit, contract,
  state-machine, facade-integration, and asynchronous tests.
- XCTest remains for XCUITest, `XCTMetric` performance tests, snapshot tooling
  that requires it, and existing tests that have not yet been migrated.
- The existing XCTest suite stays green throughout the program. Migration is
  incremental, behavior-preserving, and never a prerequisite for modem or
  transport work. Swift Testing and XCTest are not mixed in the same file.

Kotlin uses JUnit plus Android instrumentation tests. Portable C uses native
test executables and fuzz targets. Python remains restricted to
`.venv/bin/python` and research or independent-oracle tests.

### Test layers

| Layer | Purpose | Runs |
|---|---|---|
| Static | format, SwiftLint, ShellCheck, compiler warnings, public-symbol and docs checks | every PR |
| C unit | framing, profiles, state transitions, queues, codecs, allocators, malformed inputs | every PR |
| Swift Testing/Kotlin unit | value mappings, facade lifecycle, cancellation, backpressure, app models | every PR |
| Golden conformance | direct C, Swift, JNI, KISS, Accelerate ordered-block and metric parity | every PR after C3-08 |
| Fault simulation | discovery, timeouts, loss, corruption, duplication, queue pressure, recovery | every PR after C3-13 |
| Sanitizers/fuzz | C parser/DSP memory safety, undefined behavior, stateful malformed streams | PR subset plus scheduled full run |
| Performance | encode/decode speed, allocations, memory high-water, queue latency | affected PRs and release |
| Platform integration | route, source, channel, interruption, lifecycle, packaged consumer | platform PRs |
| HIL smoke/soak | on-device parity, physical transfer, 30-minute stability | tagged/manual hardware gate |
| Qualification | held-out devices/cells, p10 application goodput, recovery, claims | release gate only |

### Deterministic test requirements

- Inject clocks and randomness. Do not use `sleep` to make a protocol test pass.
- Every async test has a bounded completion condition and tests cancellation;
  use Swift Testing confirmations for callback and event-count assertions.
- Tests own isolated session and filesystem fixtures; shared global state is a
  defect, not a reason to depend on test ordering.
- Parameterize repeated profile, chunk, channel, and fault cases with
  `@Test(arguments:)` where the case matrix is data rather than distinct logic.
- Test public behavior rather than private implementation functions except for
  portable C algorithm primitives with an explicit contract.
- Retain the seed and event trace for every randomized failure.
- Attach or preserve machine-readable diagnostics on failure.

### Swift test migration rules

- C3-02 converts one small representative suite to prove package and CI support.
  Later conversions are independent, mechanical PRs with equivalent fixtures
  and assertions; they do not include production behavior changes.
- Use traits and shared tags to select critical, conformance, and slow suites.
  A test is not marked `.serialized` merely to hide a race or impose workflow
  order; serialization is reserved for unavoidable external shared state.
- Assume Swift Testing cases run in parallel. Give each case independent
  sessions, clocks, seeds, temporary storage, and retained artifacts.
- Keep XCUITest page objects and `XCTMetric` performance baselines in XCTest.
  Hardware campaigns remain explicit HIL jobs rather than ordinary unit tests.

### Coverage policy

- Record separate Swift and C baselines in C3-02.
- New 3.0 non-hardware code targets at least 90% line coverage and explicit
  branch/error coverage for state machines, parsers, queues, and ownership.
- The aggregate shipping non-hardware target must reach at least 80% line
  coverage before release.
- Audio callbacks and OS route code use fake-adapter coverage plus focused
  platform integration; coverage percentage cannot replace soak/HIL evidence.
- Zero-covered legacy implementations are either tested, quarantined in
  `CyrinxExperimental`, or removed at the major-version boundary.

### Performance and safety gates

- Streaming RX and queued TX sustain at least 4x real time on the qualified
  Pixel and Mac.
- Audio callbacks allocate no heap memory, perform no FFT/correlation, invoke no
  application code, and emit no formatted log strings.
- PCM, event, message, retry, and diagnostic buffers publish capacity and
  high-water marks; tests drive each overflow path.
- One hour of declared room-tone/noise replay yields zero CRC-valid deliveries.
- 1,000 continuous replay frames yield no duplicate or missing delivery.
- ASan and UBSan pass C parsing, streaming, and demodulation fixtures.
- TSan or equivalent stress testing covers supported serialized wrapper paths.
- Release builds contain no warnings in 3.0 targets.

### Hardware evidence classes

- **Smoke:** one known route verifies audio, capture, render, and one message.
- **Parity:** the same PCM produces the same on-device and host-replay result.
- **Soak:** 30 minutes of simultaneous capture/playback with queue and lifecycle
  telemetry.
- **Screen:** small preregistered balanced comparison that may stop a candidate.
- **Confirmation:** at most the declared balanced pairs after a passed screen.
- **Qualification:** frozen policy on held-out devices/cells; supports defaults.

No lower evidence class is relabeled as a higher class in documentation.

## Documentation plan

### Documentation set

| Document | Created/finished by | Purpose |
|---|---|---|
| 3.0 architecture ADRs | C3-01 | ownership, concurrency, modules, host-DSP boundary |
| Test strategy and migration guide | C3-02 | Swift Testing defaults, XCTest boundaries, tags, CI commands |
| C ABI and ownership reference | C3-03 | sizes, versions, allocator, clocks, lifecycle |
| Profile registry reference | C3-04 | immutable geometry, IDs, hashes, qualification |
| Result and metric semantics | C3-05/C3-23 | block maps, diversity, rate definitions, confidence |
| Streaming and real-time contract | C3-09/C3-10 | callback-safe operations, queues, discontinuities |
| Bootstrap/wire protocol | C3-12/C3-18 | beacon, framing, control timing, compatibility |
| Apple integration guide | C3-14/C3-24 | permissions, session ownership, routes, cancellation |
| Android integration guide | C3-15/C3-25 | AAR, JNI, ABIs, sources, permissions, lifecycle |
| Session and delivery guide | C3-19/C3-22 | best-effort/reliable semantics and limits |
| Sounding/adaptation guide | C3-21/C3-23 | directional estimates, activation, fallback |
| Diagnostics/privacy guide | C3-26 | support bundles, PCM opt-in, redaction, replay |
| Chat tutorial | C3-28 through C3-32 | smallest end-to-end consumer and troubleshooting |
| 2.x migration guide | C3-06/C3-35 | symbol mapping, code changes, behavior changes |
| Support/qualification matrix | C3-16/C3-34 | exact qualified hardware/routes and limitations |
| Security status/threat model | each relevant PR | unauthenticated state and deferred secure transport |
| Release notes and known defects | C3-35 | evidence-backed claims and explicit gaps |

### Per-PR documentation requirements

- Update public comments and examples with the implementation, not later.
- Add or update wire/ABI diagrams when bytes or layouts change.
- State whether a result is digital, simulated, replayed, OTA, or qualified.
- Include units, denominators, sample rate, route, and profile identity for every
  performance number.
- Put negative research outcomes in `docs/NEGATIVE_FINDINGS.md` and implementation
  milestones in `CHANGELOG.md`.
- Keep experimental instructions under `docs/spikes/` or `scratch/`; do not put
  them in the stable quick start.
- Validate local links and copy/paste examples in CI.

## Milestone exits

| Milestone | Included PRs | Exit condition |
|---|---|---|
| M0 Contract | C3-01–C3-04 | ownership, versions, profiles, and compatibility are explicit |
| M1 Canonical parity | C3-05–C3-08 | C, Swift, JNI, KISS, and Accelerate agree on fixtures |
| M2 Streaming PHY | C3-09–C3-13 | bounded continuous bootstrap/bulk processing and deterministic faults |
| M3 On-device alpha | C3-14–C3-16 | Apple/Android decode locally and pass parity/soak gates |
| M4 Manual session | C3-17–C3-20b | discovery plus honest best-effort messages and capabilities |
| M5 Adaptive reliable beta | C3-21–C3-23 | sounding, activation, reliable delivery, fallback, link estimates (C3-21a promotes into adaptation only past its frozen thresholds) |
| M6 Developer preview | C3-24–C3-32 | packaged SDK and chat sample work through public APIs |
| M7 Release candidate | C3-33–C3-35 | held-out qualification freezes supported defaults and claims |

## Release checklist

- [ ] Direct C, Swift, JNI, KISS, and Accelerate conformance is green.
- [ ] Streaming RX/TX meets chunking, memory, performance, and noise gates.
- [ ] Apple and Android accepted paths perform DSP locally.
- [ ] No supported session or audio path relies on `@unchecked Sendable`.
- [ ] Discovery collision, peer loss, event overflow, and cancellation are tested.
- [ ] Reliable messages complete hash-correct or fail explicitly.
- [ ] Link estimates are directional, aged, confidence-bearing, and calibrated.
- [ ] Fast/robust 1 MiB qualification transfers have zero planned failures.
- [ ] Application goodput includes setup, sounding, control, retry, and recovery.
- [ ] Packaged clean consumers pass without repository-relative files.
- [ ] Chat works in simulator mode and on the qualified Apple/Android pair.
- [ ] Support bundles validate, replay where applicable, and redact by default.
- [ ] Security is labeled unauthenticated in API docs, sample UI, and release notes.
- [ ] Supported and unsupported hardware/route matrices are published.
- [ ] Negative results and known defects are retained.
- [ ] Every public claim maps to a retained result or deterministic contract test.

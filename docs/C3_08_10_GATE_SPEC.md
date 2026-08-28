# Draft C3-08 Through C3-10 Promotion Gates

## In plain English

This document writes the exam before the student exists. C3-08 through C3-10
are the future streaming features, and this spec defines the tests each must
pass to count as done: C3-08 proves the same recording decodes to identical
results whether you call the C library directly, from Swift, or from Kotlin;
C3-09 is the real-time receiver — listening continuously, in arbitrary-sized
chunks, must produce the same answers as decoding a whole recording at once,
without ever allocating memory or stalling inside the audio callback; C3-10
is the transmit queue — the audio it renders must be sample-identical no
matter how playback slices it. Writing the pass/fail criteria first keeps
"done" from being renegotiated after the code is written. Numbers marked
provisional get measured on real devices before being frozen, and approving
this document does not start any of the implementation work.

---

Date: 2026-08-27
Status: **draft for critique; no C3-08, C3-09, or C3-10 implementation is
authorized by this document**

This specification turns the roadmap descriptions into falsifiable promotion
criteria. It depends on the final C3-05 profile, capture, result, and error
contracts. Field names and fixture paths may be reconciled after C3-05 review,
but a weaker test category or threshold requires an explicit plan change.

## Shared rules

1. A result is classified as digital, simulated, retained replay, current OTA,
   or route-qualified. The classifications are never interchangeable.
2. Direct C is the result owner. Swift and JNI must expose the same ordered
   result without recomputing DSP decisions or policy.
3. Exact fields compare byte for byte. Floating-point fields have a tolerance
   declared beside each fixture and field; there is no global tolerance.
4. Every runner writes its source revision, fixture manifest hash, profile
   identity, compiler/toolchain, target ABI, FFT backend, binding, and command
   line into a machine-readable report.
5. A skipped supported binding, target, fixture, or sanitizer is a failed
   promotion run. Unsupported targets are excluded only by a checked-in support
   matrix with a reason.
6. Public replayability is required for a new comparative headline claim. It is
   not required for release qualification based on internally retained
   evidence, but the qualification report must identify the custodian,
   provenance, integrity hash, route, and public-replay limitation.
7. Test regeneration is reviewable: the old and new semantic manifests, reason,
   generator revision, and changed artifact hashes are retained in the PR.

## Required command surface

C3-08 must introduce one non-interactive repository command with this logical
interface:

```text
./scripts/check-conformance.sh \
  --manifest Tests/Fixtures/conformance/manifest.json \
  --report artifacts/conformance/report.json
```

The command name and paths may change during review, but the final equivalent
must run from a clean checkout without an IDE, discover every supported binding
and backend, return nonzero on divergence, and retain a JSON report. C3-09 and
C3-10 extend the same manifest/report format rather than adding incomparable
ad hoc runners.

Sanitizer commands must be explicit CI jobs rather than environment-dependent
local conventions. The C parser/batch targets run with ASan and UBSan. Swift
ownership/concurrency paths run with TSAN where the target toolchain supports
it. Any sanitizer suppression is symbol-specific, justified, and checked in.

### Execution tiers and cost ceilings

The checked-in support manifest enumerates applicable matrix cells; it does
not construct nonsensical Cartesian products. Every conformance report names
its execution tier and the selected cells, schedules, and seeds.

- **Pull-request tier:** run every binding, backend, and registered profile on
  at least one happy-path fixture. Use deterministic pairwise coverage across
  channel layout, result class, and input fault, with every fault exercised at
  least once. For streaming boundary schedules, exercise the first and last
  split in every declared boundary class. The tier has a ceiling of 30 minutes
  wall time per lane and 120 runner-minutes in aggregate.
- **Nightly/full tier:** run every applicable cell in the support manifest,
  every declared boundary split, the full random-seed budgets below, all
  sanitizer jobs, and all supported on-device targets. The tier has a ceiling
  of 120 minutes wall time per lane and 720 runner-minutes in aggregate.
- **Release promotion:** requires a nightly/full report for the exact source,
  manifest, fixture, and toolchain revisions being promoted. It may not
  substitute the sampled pull-request tier.

A run that exceeds its ceiling fails rather than silently sampling less. If the
same tier exceeds its ceiling in two consecutive runs on the reference runner,
the owner must optimize or split the jobs, or obtain an explicit amendment to
the ceiling and support manifest before promotion.

### Real-time-path proof mechanism

Test builds route allocator/deallocator access, lock/wait acquisition, logging,
FFT/correlation entry points, and application callbacks through injectable
hooks or shims. A thread-local `inside_realtime_path` marker is set for the
complete push/copy or render call. A forbidden hook observed while that marker
is set records the symbol and call site and fails the gate.

Direct platform calls that bypass the hooks are rejected by a checked-in static
source/call-graph audit and, where the toolchain permits it, a link-symbol or
interposition audit for allocator and lock primitives. Each platform gate has
negative controls that deliberately perform one allocation, lock/wait, log,
FFT/correlation operation, and application callback on the marked path. Every
injection must fail for the intended symbol and call site. Passing only because
the instrumentation missed the injected operation is a gate failure.

## C3-08 — cross-binding conformance and retained replay

### Required matrix

Every applicable fixture is executed through:

| Dimension | Required values |
|---|---|
| Binding | direct C, Swift value binding, test JNI/Kotlin binding |
| FFT backend | portable KISS FFT, Apple Accelerate |
| Profile class | accepted 1.x compatibility, frozen Cyrinx 2 schedule, each new registered profile |
| Channel layout | mono, planar two-channel, interleaved two-channel, swapped roles, aliased/duplicate channels |
| Result class | complete decode, partial block recovery, CRC failure, synchronization failure, rejected input |
| Input fault | truncation, odd interleaved tail, non-finite sample, clipping, dropout, discontinuity, unknown profile, stale hash, wrong ABI version, undersized prefix, insufficient output capacity |

JNI is tested on every supported Android ABI declared by the support matrix.
Accelerate applies only on Apple targets; its result is compared with KISS on
the same Apple fixture and toolchain run.

The table describes the full/nightly matrix. The pull-request tier selects the
deterministic subset defined under execution tiers; release promotion requires
the complete applicable matrix.

### Exact comparisons

The following compare exactly unless the final C3-05 contract removes them:

- status and error precedence;
- profile ID and canonical identity bytes;
- payload bytes and produced payload length;
- total block count and the ordered validity value for every block;
- selected channel/input and selection reason;
- consumed raw-sample range, consumed logical-frame range, produced range,
  monotonic start/end indices, and discontinuity outcome;
- clipping and non-finite evidence counts; and
- required-capacity values on `BUFFER_TOO_SMALL`.

C3-08 approval is blocked until C3-05 reconciles block-validity storage with
the maximum valid block count. If validity is caller-provided, conformance must
exercise capacity negotiation and report every ordered value for a synthetic
valid profile with more than 256 blocks. If a fixed maximum is chosen instead,
that maximum is part of profile validation and the same synthetic profile must
be rejected before processing with the agreed status and unchanged outputs.

EVM, pilot residuals, timing, propagation delay, and other floating-point
metrics use fixture-specific absolute and/or relative tolerances. The report
contains the observed values, deltas, tolerance, and pass/fail decision.

### Corpus and provenance

The committed corpus contains at least:

- one small PCM replay per registered compatibility/qualified profile;
- one MRC-rescue capture and one automatic-primary-selection capture;
- one partial-block-recovery capture;
- deterministic generated malformed inputs for every fault class above; and
- content-addressed metadata for every larger internally retained capture used
  for qualification.

Each non-generated capture records acquisition date, devices, OS/build, route,
sample format/rate, channel order, gain/volume, pose, expected scope, custodian,
and file hash. A fixture with unknown provenance can be retained as a negative
artifact but cannot promote a profile or headline claim.

### Negative controls

CI must prove the gate itself works by deliberately changing, one at a time:

- one payload byte;
- one block-validity position;
- one exact count/range;
- one profile identity byte; and
- one floating metric beyond its declared tolerance.

Each mutation must fail the conformance command and identify the binding,
backend, fixture, and field.

### C3-08 promotion gate

C3-08 is promotable only when:

1. every matrix cell required by the support manifest passes;
2. all negative controls fail for the intended reason;
3. direct C ASan/UBSan and applicable Swift TSAN jobs pass;
4. the JSON report validates against a versioned schema and is retained by CI;
5. fixture provenance and regeneration checks pass; and
6. a clean checkout reproduces the committed small-corpus report.

C3-09 and C3-10 implementation does not start before this gate passes on the
final C3-05/C3-06/C3-07 shapes.

## C3-09 — bounded streaming receiver

### Batch-equivalence matrix

For every accepted C3-08 capture, streaming decode must make the same ordered
block and payload decisions as batch decode under:

- the entire capture in one push;
- one-sample pushes;
- every boundary split around preamble, header, cyclic prefix, symbol, block,
  and tail positions;
- deterministic random chunk sequences allocated across applicable
  profile/layout pairs: 32 total in the pull-request tier and 1,000 total in
  the nightly/full tier. Seeds are distributed round-robin, with every pair
  exercised once before any pair repeats;
- inserted zero-length process/drain calls;
- zero-gap consecutive frames;
- inserted silence, duplicated chunks, dropped chunks, explicit
  discontinuities, and configured clock skew; and
- reset and destroy at every legal state boundary.

For unmodified captures, payload, block validity, selected channel/reason, and
absolute monotonic sample ranges match batch exactly. Streaming-only acquisition
and buffering metrics are compared against their own declared bounds.

### Boundedness and callback safety

Before implementation, C3-09 must declare numeric limits for maximum channels,
chunk samples, synchronizer history, queued decoded results, and total context
bytes. Promotion then requires:

- measured high-water memory never exceeds the declared context plus caller
  buffer bound during a 1,000-frame continuous replay;
- zero allocation, deallocation, lock acquisition, logging, FFT, correlation,
  or application callback on the real-time push/copy path, proved with an
  instrumented allocator, the shared real-time hook/audit mechanism, and
  callback-thread assertions;
- bounded work per push proportional only to the supplied sample count; and
- deterministic overflow behavior with an exact dropped range and explicit
  reacquisition state.

The worker may allocate only through the configured allocator and must return
to its post-create allocation baseline after drain/reset.

### Delivery and recovery thresholds

- 1,000 unmodified back-to-back frames: zero duplicate, missing, or reordered
  deliveries and zero unbounded memory growth.
- A declared discontinuity: no delivery spanning the invalid range.
- Reacquisition candidate for critique: the first valid frame whose complete
  preamble begins after a discontinuity is delivered; otherwise the report must
  show why one full valid frame was insufficient. This replaces an ambiguous
  wall-clock bound with a sample-indexed condition.
- Processing throughput candidate for critique: at least 4.0x real time at the
  qualified sample rate on the slowest supported Android target and slowest
  supported Apple target, using release builds and excluding file I/O. Median,
  p95, maximum, thermal state, and device/build identity are retained. This
  threshold remains provisional until measured before implementation starts.

### C3-09 promotion gate

C3-09 promotes only if batch equivalence, all chunk/fault schedules, the
1,000-frame soak, allocation/thread assertions, memory bounds, reacquisition
condition, and approved on-device throughput threshold pass. A simulator-only
or desktop-only run cannot qualify the streaming receiver.

## C3-10 — queued streaming transmitter

### Waveform equivalence

For every registered profile and payload boundary (empty if allowed by C3-05,
one byte, block boundary minus/at/plus one, and maximum payload), compare the
streaming transmitter with the frozen batch waveform under:

- one-sample render buffers;
- every split around preamble, gap, symbol, block, and tail boundaries;
- deterministic random render-size sequences allocated across applicable
  profile/payload-boundary pairs: 32 total in the pull-request tier and 1,000
  total in the nightly/full tier. Seeds are distributed round-robin, with every
  pair exercised once before any pair repeats;
- consecutive queued messages with every legal profile transition; and
- flush/reset after every legal partial-render state.

Concatenated rendered PCM must be sample-identical to the batch reference,
including preamble, inter-message gaps, transitions, and tail padding. Queue
acceptance, first rendered sample, final rendered sample, cancellation, and
completion each have distinct monotonic sample indices and state events.

### Queue, cancellation, and underrun semantics

Before implementation, C3-10 declares numeric queue-message and queue-byte
limits. Promotion requires exact tests for:

- capacity minus/at/plus one, including concurrent producers;
- cancellation before preparation, during preparation, before first render,
  during render, and after final render;
- flush and reset with queued and active messages;
- output underrun and caller-supplied silence policy;
- route invalidation and profile incompatibility; and
- destruction with queued work and repeated destroy misuse behavior as defined
  by the ownership contract.

No enqueue acceptance is reported as transmission completion. Cancellation
cannot retract already rendered samples; the result reports their exact range.

### Boundedness and callback safety

The render path performs zero allocation/deallocation, blocking, lock
acquisition, logging, or application callback. The shared real-time hook/audit
mechanism, instrumented allocator, and callback-thread assertion must prove
this for the complete waveform matrix and a 1,000-message soak. Queue memory
remains within the declared message/byte limits, and reset returns retained
memory to the documented baseline.

The on-device throughput gate uses the same approved targets and reporting
method as C3-09. Rendering must sustain the target sample rate with the chosen
minimum callback buffer and retain p95/max callback time below that buffer's
deadline. The numeric buffer and timing thresholds are fixed from measured
target data before implementation begins.

### C3-10 promotion gate

C3-10 promotes only when waveform equivalence, state/range accounting, queue
capacity under concurrent producers, cancellation/underrun/route faults,
callback-safety assertions, memory bounds, the 1,000-message soak, and approved
on-device deadline margins pass.

## Decisions required before implementation

Review must resolve these values rather than leaving them to the implementing
PR:

1. final supported Apple and Android target/ABI matrix;
2. numeric C3-09 receiver memory, chunk, queue, and history limits;
3. whether the proposed first-valid-frame reacquisition condition is sufficient;
4. whether 4.0x real-time remains the receiver throughput threshold after a
   pre-implementation measurement;
5. numeric C3-10 message/byte queue limits and minimum callback buffer;
6. p95 and maximum callback deadline margins on each slowest target; and
7. which retained captures may support internal release qualification but not a
   public comparative headline; and
8. caller-provided block-validity capacity versus a validated fixed maximum,
   including the required C3-08 outcome for a synthetic valid profile with more
   than 256 blocks.

Approval of this specification ends the planned tranche. It does not authorize
C3-08 implementation.

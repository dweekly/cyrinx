# Cyrinx Gate Recovery and C3 Integration Execution Plan

Fresh as of 2026-08-27. Status: **revised after critique; decisions recorded**.
The plan governs the next engineering tranche, ending after C3-05 integration
and the C3-08 through C3-10 gate specification. C3-08 implementation is outside
this tranche.

## Objective

Establish a truthful, reproducible baseline on `main`, then integrate the first
Cyrinx 3 contract and batch-PHY work without implying that streaming,
on-device, session, or qualification milestones have been reached.

This is not a claim that the complete [Cyrinx 3 plan](CYRINX_3_PLAN.md) can be
completed as one uninterrupted change.

## Baseline at plan creation

Reviewed revision: `main@123a105`.

| Area | Observed state | Consequence |
|---|---|---|
| Root Swift tests | 105/105 passed | Deterministic library behavior has a useful baseline. |
| Swift chat suites | Passed | Apple simulator behavior has a useful baseline. |
| Android chat gate | One reproducible concurrency-test failure before Phase 0 | Chat CI was not locally reproducible as green. |
| API inventory | Tests and live inventory passed; 698 identities classified | The compatibility ledger is usable as an integration guard. |
| Format gate | Failed across first-party and vendored C files | Formatter ownership must be explicit. |
| Swift lint | 69 serious violations in 35 files | Existing debt needs a baseline; it must not force refactoring in this tranche. |
| C3 implementation | C3-01 and C3-28 through C3-30 are on `main`; C3-02 through C3-05 are branch-only | Branch work is not current product state. |
| OTA evidence | Aggregate ledgers and hashes are tracked; decisive raw captures and replay inputs are not all in the checkout | Existing claims can be checked arithmetically but not independently replayed from the repository. |

## Decisions recorded from plan review

1. Exclude vendored KISS FFT from repository formatting and add an
   upstream-integrity check. Do not create a formatting-only vendor fork.
2. Quarantine the existing cryptographic envelope in documentation now.
   Removal or replacement is separate security work.
3. Reconstruct the bundled C3-02 through C3-05 work into independently gated
   changes. Treat fresh implementation against approved contracts as the
   default and precisely ledger every tip hunk that is reused.
4. Replayable evidence blocks **new comparative headline claims**. It does not
   by itself block release qualification; internally retained evidence may be
   used for qualification if its provenance and limitations are explicit.
5. End this tranche at C3-05 plus the C3-08 through C3-10 gate specification.
   Start C3-08 implementation only after the integrated batch contracts survive
   review in their final form.
6. For existing SwiftLint complexity and length violations, use a checked-in
   baseline. Do not refactor behavior merely to reduce lint counts in this
   tranche.

## Delivery status at the Phase 3 review checkpoint

| Work | Delivery | State |
|---|---|---|
| Android completion barrier | PR [#77](https://github.com/dweekly/cyrinx/pull/77) | Isolated and validated; not treated as merged here. |
| Claim corrections, claims gate, and this plan | PR [#78](https://github.com/dweekly/cyrinx/pull/78) | Isolated and validated; updated with Phase 3 review evidence. |
| Formatter ownership and KISS FFT integrity | PR [#79](https://github.com/dweekly/cyrinx/pull/79) | Isolated and validated. |
| SwiftLint baseline | PR [#80](https://github.com/dweekly/cyrinx/pull/80) | Isolated and validated. |

C3 reconstruction does not stack on these branches. Its implementation base is
the reviewed result after the prerequisite PRs are accepted and merged.

## Operating constraints

1. Each phase produces a reviewable diff and must pass its own gate before the
   next dependent phase starts. Independent documentation work may proceed
   while Phase 0 is under review.
2. Mechanical formatting, behavioral refactoring, public-claim corrections,
   lint-baseline changes, and C3 feature integration remain separate changes.
3. Branch presence is never reported as merged capability.
4. Digital and simulator tests are never reported as OTA or on-device PHY
   qualification.
5. Existing 2.x compatibility remains in force until the planned migration
   step explicitly changes it.
6. Failed or non-identifiable research spikes are retained as negative results;
   they are not silently reclassified as unfinished wins.
7. No new comparative headline claim is merged without a public, replayable
   evidence bundle for that claim.
8. Historical values are preserved. Corrections add scope, evidence
   qualification, or an explicit retraction rather than rewriting the record.

## Execution phases

### Phase 0 — Isolate the Android completion-barrier correction

`SimulatedChatTransportClient.pendingSendJobCount` must acquire
`lifecycleLock` before reading `pendingSendJobs`. The previous accessor could
observe the interval between pending-job removal and terminal-event emission,
causing a trace assertion to run before `Delivered` was appended.

The lock order is the existing `lifecycleLock -> pendingSendJobs` order. The
change affects a test-only observation seam; it does not alter public API, wire
data, scenario timing, production event order, or command semantics.

**Delivery:** commit `7eddb2c`, PR
[#77](https://github.com/dweekly/cyrinx/pull/77). The execution-plan document is
not part of that commit.

**Validation completed:** the formerly failing focused test passed, followed
by:

```text
ANDROID_HOME=/Users/dew/Library/Android/sdk ./gradlew --no-daemon \
  check :app:testDebugUnitTest :app:assembleDebug
```

The full command passed, including 127 `chatkit` tests, app tests, Android
lint, and debug APK assembly. The absolute SDK path records the local
reproduction environment and must not enter portable build configuration.

**Gate:** PR #77 is independently reviewable, contains only the synchronization
change, and passes the declared Android workflow.

### Phase 1 — Reconcile claims with implementation and evidence

This documentation-only phase precedes format and lint debt because inaccurate
security and compatibility guidance has higher user impact and no dependency
on hygiene cleanup.

1. Replace the unqualified ABI-stability wording with a description of the
   supported 2.x API, versioned receiver contract, and active migration plan.
2. Add a status ledger to [CYRINX_3_PLAN.md](CYRINX_3_PLAN.md) that distinguishes
   merged, branch-only, stopped spike, and not-started work.
3. Update [ROADMAP.md](../ROADMAP.md) so Rank 8 records the completed stop/fail
   outcomes and Rank 11 records `STOP_NOT_IDENTIFIABLE` for the retained-corpus
   audit while leaving new OTA acquisition as future work.
4. Correct [CRYPTO_TRADEOFF.md](CRYPTO_TRADEOFF.md) to describe the current
   SHA-256-derived XOR/HMAC prototype and its actual 16-byte frame envelope.
5. Remove blanket enablement advice. Document unauthenticated X25519's active-MITM
   limitation, the absence of security qualification, and that this is not a
   standard AEAD construction.
6. Mark `walkthrough.md` as a historical lab narrative. Move any still-current
   operational guidance into maintained HIL documentation before deleting it
   from the narrative.
7. Distinguish arithmetic/integrity evidence from independently replayable
   evidence in performance and comparison claims, including the `137x`
   same-bench baseline.
8. Update `CHANGELOG.md` with the user-visible claim and security-documentation
   corrections.

#### Mechanical claims gate

Add `scripts/check-claims.sh` and invoke it from `scripts/check.sh`. Its initial
rules and allowlist are checked in beside the script. At minimum it rejects,
outside explicitly historical/retraction contexts:

The initial rules cover unqualified stable-ABI wording, the incorrect cipher/
authentication label, blanket crypto-enable advice, and the promotional
phrases retained in the old Android walkthrough. The literal patterns live in
the checker so the gate, rather than this prose, is the source of truth.

The script also verifies that current claim-bearing documents contain the
required evidence-class terminology. Every exception is path-and-line-pattern
specific and includes a rationale; there is no directory-wide exclusion.

**Evidence rule decided by review:** the author of any new comparative
headline claim owns its replay bundle. The bundle and replay command are due in
the same PR as the claim. If that cannot be done, the comparative claim does
not merge. Release qualification may use non-public evidence, but must label it
as such and cannot promote it into a new public comparative headline.

**Gate:** `scripts/check-claims.sh` passes reproducibly, its negative
self-tests prove each prohibited pattern is detected, and no production
behavior changes are present. General documentation-link validation remains a
C3-02 deliverable; Phase 1 does not claim a link checker that it does not ship.

### Phase 2 — Make gate ownership explicit

#### 2.1 C and Swift formatting

Classify files as first-party, generated, or vendored:

- first-party Swift and C are formatter-owned and must pass the checked-in
  configurations;
- generated outputs are checked only when their generator promises canonical
  formatting; and
- vendored KISS FFT is excluded from repository-wide formatting and checked
  against an upstream-integrity manifest.

Implementation:

1. encode classification in one file-selection helper shared by
   `scripts/format.sh` and `scripts/format-check.sh`;
2. record KISS FFT source origin and expected hashes;
3. format only first-party sources;
4. inspect the mechanical diff for semantic or generated-file churn; and
5. run format-check twice to prove idempotence.

**Gate:** a second formatter run produces no diff, all first-party sources
pass, and every excluded path is documented as vendored or generated.

#### 2.2 SwiftLint baseline

Generate and check in a baseline containing the existing violations. Configure
CI so new violations fail while baseline entries can be burned down separately.

For this tranche:

- do not extract helpers, split types, or reorder state-machine code solely to
  satisfy complexity, body-length, file-length, or type-length rules;
- use a narrowly documented inline suppression only if the installed SwiftLint
  cannot represent the violation in its baseline format; and
- do not raise global thresholds or disable rules repository-wide.

Correctness findings discovered during lint review become separate behavioral
changes with focused tests; they do not enter the lint-baseline change.

**Gate:** `scripts/lint.sh` rejects a seeded new violation, accepts the exact
checked-in baseline, and contains no behavioral source refactor.

#### 2.3 Minimum repository CI enforcement

PR #78 adds an unfiltered pull-request workflow for the claims gate. Format,
vendor-integrity, and lint enforcement cannot be added on any one isolated
prerequisite branch because their passing implementations are split across PRs
#79 and #80.

After PRs #78 through #80 are accepted and merged, the next independent change
adds a minimum repo-wide workflow running claims, format/vendor integrity, and
lint. It lands before any C3 reconstruction. Until then, claims are enforced by
CI but format/vendor/lint remain locally validated and voluntary; this is an
explicit temporary enforcement gap, not a green-CI claim.

### Phase 3 — Reconstruct and review C3-02 through C3-05

**Current checkpoint:** branch-tip review is complete and implementation is
paused for contract critique. The empirical findings, branch dispositions,
reuse-ledger rule, and proposed reconstruction contracts are recorded in
[C3-02 through C3-05 branch-tip review](reviews/C3_02_05_TIP_REVIEW_2026-08-27.md).
The review found promotion blockers in every bundled phase, including an
off-by-one interleaved-stride calculation left by the tip fix, so no branch hunk
is accepted merely because the existing sanitizer suites pass.

The review target is the **tip** of `origin/c3-02-05-batch-demod`:

```text
2370fe26183068cf48bd20474d0bbc9eda6db7a4
```

Do not review or reconstruct from the stale parent `ab0ce83` alone. Commit
`2370fe2` fixes known defects and is also a map of branch fragility. Re-test the
fixed defect classes at the tip rather than trusting the corrective diff.

Review order:

1. **C3-02 CI baseline:** reconcile it with Phases 1 and 2 and the existing
   chat workflow.
2. **C3-03 ABI foundation:** verify fixed-width fields, exports, ownership,
   allocator behavior, prefix-compatible `struct_size`, errors, and 2.x
   compatibility.
3. **C3-04 profiles:** verify stable IDs/hashes, unknown-profile rejection,
   registry uniqueness, and cross-language golden data.
4. **C3-05 batch contract:** verify malformed, non-finite, truncated, strided,
   interleaved, and channel-order handling; metrics; ordered block validity;
   and direct C/Swift/JNI parity.

#### Mandatory regression targets from `2370fe2`

- ASan boundary tests for more than 256 encoded blocks and the fixed-size
  block-validity mask;
- ASan tests for primary/secondary length disagreement, non-unit strides,
  interleaved stereo, missing secondary pointers, zero/one-sample inputs, and
  consumed-sample accounting;
- ASan/UBSan regression coverage that captures secondary-channel presence
  before temporary storage is freed and never branches on an indeterminate
  freed pointer value;
- byte-vector profile-hash tests proving explicit big-endian serialization is
  independent of host endianness;
- prefix-size, undersized-struct, oversized-struct, and ABI-version tests for
  both profile lookup entry points;
- symbol-visibility tests for every new profile function; and
- TSAN/concurrency tests for the Swift API paths changed by the fix commit.

The fix commit removes runtime-session calls and substitutes simulated loopback
behavior to eliminate Swift races. That is a semantic change, not automatically
an acceptable race fix. It must either be justified by the C3-01 simulator
contract or recorded as deferred/rejected branch content and replaced with an
ownership-safe implementation.

#### Reconstruction provenance

Reconstruct C3-02 through C3-05 as independently gated changes. Before
promotion:

1. record the merge base and the complete tip patch for `2370fe2`;
2. treat fresh implementation against the approved contracts as the default;
3. record the disposition of branch concerns at path/subsystem granularity; and
4. check in a reuse ledger for every retained tip hunk, identifying its source
   range, reconstructed commit, patch identity or reason for change, reviewer,
   and validation consequence.

An unlisted reused tip hunk fails the gate. Code already classified as dropped
or redesigned does not require line-by-line divergence bookkeeping; its
replacement is reviewed against the accepted contract and mandatory regression
targets.

Other required checks:

- API inventory completeness and disposition changes;
- direct C build and sanitizer tests;
- Swift fixture parity;
- JNI compile and JVM conformance tests;
- unknown-profile and malformed-input tests; and
- no weakening of the restored format, lint, or claims gates.

**Gate:** C3-02 through C3-05 each meet their documented merge gate; mandatory
`2370fe2` regressions pass; all reused branch code is accounted for; and the
complete validation matrix is green after every integration step.

**Stop conditions:** any unexplained 2.x ABI break, exact-size-only
extensibility, platform-sized wire/API field, retained borrowed string,
inconsistent metric meaning, binding-specific result, sanitizer finding,
TSAN finding, or unlisted reuse of branch code.

**Decisions required before implementation:** fixed-width ABI prefix/count
representation, 32-byte SHA-256 versus a truthfully sized non-cryptographic
profile fingerprint, separation of route qualification from wire identity,
caller-provided versus fixed-capacity block validity, and exclusion of the
Swift session facade until C3-06.

### Phase 4 — Specify the C3-08 through C3-10 feasibility gates

This phase writes and reviews acceptance criteria. It does not implement C3-08
or imply that streaming exists because batch parity passes.

**Draft delivered for critique:**
[C3-08 through C3-10 promotion gates](C3_08_10_GATE_SPEC.md). The draft defines
the cross-binding matrix, exact versus toleranced fields, negative controls,
chunk and render schedules, soak criteria, callback-safety instrumentation,
evidence provenance, and the numeric decisions that must be fixed before
implementation. It remains unapproved and does not authorize C3-08 work.

1. Specify the C3-08 direct-C/Swift/JNI conformance matrix over retained and
   malformed fixtures.
2. Freeze C3-09 criteria: arbitrary chunk boundaries, bounded memory,
   discontinuity recovery, deterministic batch equivalence, callback-safe
   execution, and measured real-time margin on the slowest target.
3. Freeze C3-10 criteria: bounded enqueue/render, exact sample/tail accounting,
   underrun behavior, cancellation, and no allocation or blocking in the audio
   callback.
4. Specify reproducible benchmark output with device and toolchain provenance.
   The proposed four-times-real-time threshold remains provisional until
   measured on the actual JNI and Apple targets.

**Gate:** reviewers approve measurable thresholds and confirm that the retained
fixture corpus can distinguish batch correctness from streaming correctness.

**Stop condition:** do not begin C3-08 implementation, bootstrap, session, live
chat, additional PHY research, or public SDK packaging within this tranche.

## Validation matrix for the approved tranche

```text
./scripts/check-claims.sh
./scripts/format-check.sh
./scripts/lint.sh
./scripts/check-api-inventory.sh --test
./scripts/check-api-inventory.sh
swift test
swift test --package-path Apps/Chat/CyrinxChatKit
swift test --package-path Apps/Chat/apple/CyrinxChatApp
ANDROID_HOME=<resolved-sdk> ./gradlew --no-daemon \
  check :app:testDebugUnitTest :app:assembleDebug
sanitizer, TSAN, hash-vector, ABI, and reconstruction-provenance probes added
in Phase 3
```

The final report lists warnings, environment prerequisites, branch
dispositions/reuse, and skipped platform checks, not only exit codes.

## Tranche completion definition

This tranche is complete when:

1. PR #77 has passed review;
2. claim, format, lint-baseline, API-inventory, Swift, and Android gates pass;
3. C3-02 through C3-05 are integrated as separately reviewable changes with
   all reused branch-tip code explicitly ledgered;
4. the `2370fe2` vulnerability, endian, ABI, and race regressions pass; and
5. the C3-08 through C3-10 gate specification is approved but C3-08
   implementation has not begun.

# C3-03 Through C3-05 Reconstruction Record

## In plain English

The July Cyrinx 3.0 branch was rejected in review, so its three pieces were
rebuilt from scratch: the rulebook for versioned C structs (so old apps and
new library versions can coexist), the catalog of radio presets with real
SHA-256 fingerprints, and the "decode this recording" function with proper
stereo handling. This document is the paper trail: which decisions were made
and why, exactly what was kept from the old branch (mostly the preset values
and enum numbering — credited in the reuse ledger below), how the result was
tested, and what still has to happen (the Android side, comparisons against
the Python reference, cross-device checks, CI) before these pieces count as
finished rather than experimental.

---

Date: 2026-08-28
Reconstruction base: `main@b4201c4` (after the claims, format-ownership, and
lint-baseline gates merged).
Rejected branch tip: `origin/c3-02-05-batch-demod@2370fe2`, reviewed in
[C3_02_05_TIP_REVIEW_2026-08-27.md](C3_02_05_TIP_REVIEW_2026-08-27.md).

Per that review's reconstruction rule, this is a **fresh implementation
against the recorded contracts**, with the reuse ledger below accounting for
every piece of tip content that was retained. C3-02 (the CI baseline) is
deliberately not part of this change; it follows once these contracts are
accepted, per the execution plan's Phase 2.3/Phase 3 ordering.

## Decisions recorded (2026-08-28, owner-directed reconstruction)

The five "review decisions required" from the tip review, resolved by adopting
the recommendation each document already carried:

1. **Fixed-width ABI prefix**: `uint32_t struct_size` + `uint32_t abi_version`
   in every versioned structure; status storage is `int32_t`
   (`cyrinx_abi_status_t`) with symbolic constants defined separately, values
   numerically identical to the 2.x enum for shared codes (asserted by tests).
2. **Profile identity is SHA-256** (32 bytes, field named `identity_sha256`),
   over a domain-separated big-endian canonical serialization
   (`cyrinx-profile-identity-v1`), via a reviewed portable first-party
   implementation pinned to the NIST FIPS 180-4 vectors and cross-checked
   against CryptoKit in tests. The FNV-64-padded-to-32-bytes scheme is gone.
3. **Evidence is not identity**: the digest covers physical/wire parameters
   only. `id` (registry key), `classification` (evidence metadata), and
   `block_count` (derived) are excluded, and a test proves changing them does
   not change the digest while changing any wire parameter does.
4. **Block validity is caller-provided storage** with capacity negotiation
   (`block_validity_required` written on `BUFFER_TOO_SMALL`); the embedded
   256-byte mask is gone and a synthetic >256-block profile exercises the
   boundary end to end.
5. **No Swift session facade**: `Cyrinx3API.swift` and its simulated
   transport are not reconstructed. Swift value bindings belong to C3-06,
   which must also replace the tip's error-code collisions with an explicit
   mapping contract.

Further contract decisions made during reconstruction:

- **Explicit strided channel views** (pointer, raw count, offset, stride,
  role) replace the tip's aliasing/stride inference; mono, planar, and
  interleaved layouts share one checked frame-count formula
  (`ceil((count - offset) / stride)`), fixing the tip's off-by-one.
- **No placeholder measurements**: the v1 result has no
  `propagation_delay_ms` / `symbol_timing_error` fields at all, rather than
  fields hardcoded to zero.
- **Partial recovery is not an error**: a capture that demodulates returns
  `OK` with the ordered validity mask (zero valid blocks included);
  `ERR_DECODE` is reserved for the demodulator producing no result. This
  matches the underlying 2.x bulk receiver's actual behavior instead of
  collapsing everything to `ERR_CRC`.
- **Prefix model**: acceptance requires the named
  `CYRINX_PROFILE_V1_SIZE`-style version constant (never a bare `sizeof`),
  bytes beyond the implemented version's prefix are never read or written,
  and rejection writes no output. `_Static_assert`s freeze every v1 field
  offset at compile time on every target the library builds for.

## Reuse ledger (tip content retained)

| Tip content | Disposition here |
|---|---|
| The five registered profile parameter sets (IDs 1–5) | Reused as data, re-expressed in the canonical JSON + parameter-only C rows; derived block counts (75/75/107/169/134) match the tip review's observed values. |
| `cyrinx_modulation_t`, `cyrinx_code_rate_t`, `cyrinx_classification_t` enum values | Reused unchanged (wire-stable value assignments). |
| Identity serialization field order and big-endian byte writers | Reused shape; now domain-separated, SHA-256-hashed, and pinned by a checked-in byte vector. |
| Profile→bulk-config mapping and geometry-derived `block_count` | Reused intent, single shared internal helper instead of two copies. |
| Batch encode zero-padding semantics and evidence scans | Reused intent; scans now cover exactly the decoded logical samples. |
| Everything else (base header, validation logic, batch layout/impl, Swift facade, workflow, docs) | Fresh or dropped per the tip review's disposition table. |

## Verification in this change

- 105 existing XCTest cases unchanged and passing; 26 new Swift Testing cases
  across `CyrinxABIBaseTests`, `CyrinxProfileRegistryTests`,
  `CyrinxBatchContractTests`; the three new suites also pass under
  AddressSanitizer.
- Mandatory regression targets from the tip review covered: exact-length
  interleaved stereo (the off-by-one), odd interleaved tails, zero/one-sample
  rejection, secondary-presence captured before any free (no freed-pointer
  branch exists), >256-block ordered validity via caller storage, stale-hash
  rejection in both profile validation and batch decode, prefix
  undersize/oversize/version rejection with oversized-tail preservation,
  identity byte-vector and digest pinning.
- Gates: claims, format (clang-format + swift-format), vendored-integrity,
  SwiftLint baseline (zero new violations), and the API inventory
  (808 identities; the 110 new C identities classified `experimental`,
  replacement `plan:C3-05`).

## Remaining before C3-03..05 promotion (unchanged gate items)

- Kotlin/JNI view generated from the canonical registry source, with JVM
  conformance tests (C3-04 merge gate).
- Batch-contract parity fixtures against the frozen Python oracle and
  retained captures; MRC-rescue and malformed channel-order corpus entries.
- Architecture fixtures beyond the locally built targets (32-bit Android ABI
  layout runs; a 2.x consumer binary fixture).
- C3-02: the reproducible CI baseline that runs all of the above.

# Cyrinx 2.x public API and ABI disposition

[`api-inventory.json`](api-inventory.json) is the normative, machine-readable
C3-01 classification of the public surface present on `main` when this contract
was frozen. This document explains its scope and the reviewed family decisions.

The inventory is a migration decision, not an immediate removal list. ADR 0001
requires the 2.x compatibility surface to remain available through C3-35.

## Classification meanings

| Disposition | 3.0 meaning |
|---|---|
| `retain` | The declaration name and semantic role remain supported public 3.x vocabulary. A module move must preserve the documented compatibility import. |
| `deprecate` | The 2.x declaration remains as a source-compatible shim with a named replacement. It is not the preferred 3.0 API. |
| `experimental` | The capability moves to simulation, replay, DSP-internal, or research scope and has no stable semantic-version promise there. |
| `replace` | The current semantics conflict with the 3.0 contract and are superseded by a named API or plan item. A temporary shim does not make the old semantics canonical. |

Every non-retained entry names a replacement API, module, or plan item and a
compatibility promise. Wildcards are invalid. Classification is materialized on
every member, case, field, macro, function, and type; parent inheritance is not
used to make the checked-in file appear complete.

## Inventory totals

The 698 classified identities comprise the complete surface emitted by the
defined extraction policy.

| Language | Retain | Deprecate | Experimental | Replace | Total |
|---|---:|---:|---:|---:|---:|
| Swift | 53 | 126 | 79 | 115 | 373 |
| C | 45 | 99 | 55 | 126 | 325 |
| **Total** | **98** | **225** | **134** | **241** | **698** |

The Swift total includes 366 source-declared entries and seven source-less
protocol-witness initializers emitted even with
`--skip-synthesized-members`. Those seven are classified with their owning
types. Public conformance relationships are recorded on their owner entries.
Nineteen entries are marked macOS-only: the raw macOS link/diagnostic surface
and four system-volume methods. The remaining 354 form the current portable
Swift extraction surface.

The C total includes all declarations from every header in
`Sources/CCyrinx/include/cyrinx`, whether or not the declaration currently uses
`CYRINX_API`.

| C header | Retain | Deprecate | Experimental | Replace | Total |
|---|---:|---:|---:|---:|---:|
| `cyrinx.h` | 19 | 36 | 1 | 87 | 143 |
| `cyrinx_bulk.h` | 26 | 63 | 15 | 0 | 104 |
| `cyrinx_fft.h` | 0 | 0 | 10 | 0 | 10 |
| `cyrinx_guidance.h` | 0 | 0 | 0 | 21 | 21 |
| `cyrinx_phy.h` | 0 | 0 | 28 | 0 | 28 |
| `cyrinx_sounder.h` | 0 | 0 | 1 | 18 | 19 |

## Reviewed Swift decisions

The JSON records the decision on every leaf. The family summary is:

### Retain

- `Cyrinx`, `CyrinxError`, and `StreamPriority`;
- `TransportBackend`, `AudioBackendState`, `AudioBackendError`, and
  `AudioBackendDiagnostics`; and
- `AcousticCalibration` and `AcousticNoiseScanner`.

Apple-specific declarations may move to `CyrinxAppleAudio`, but the
compatibility umbrella preserves their supported import. Retaining
`CyrinxError` keeps the public name and structured-error role; C3-24 may refine
its major-version case model under the semantic contract.

The retained calibration and scanner types are facade spellings. Their 3.0
measurement, notch-geometry, and gain-policy work must delegate to canonical C
contracts; only platform device-volume effects remain adapter-owned.

### Deprecate

- `CyrinxSession`, `Config`, `Role`, `Gear`, `ARCPolicy`, and `StreamFlags`;
- `TransportBackend.inMemory`; its legacy implementation stays quarantined in
  the compatibility shim while the canonical replacement moves to
  `CyrinxSimulation`;
- `BulkPHY`, its configuration, geometry, and explicit diversity receiver; and
- the 2.x MCS recommendation and raw macOS link/diagnostic facades.

These stay usable as migration shims. They cannot retain independent session,
profile, DSP, metric, or security authority once the 3.0 implementation
exists. Legacy device-signature, crypto-enable, and local-key fields map to the
explicitly unsecured `SecurityStatus`, not to transport configuration.

Leaf targets follow semantic ownership rather than inheriting the containing
type's target. `CyrinxSession.send` maps to `CyrinxConnection`, receive maps to
inbound `CyrinxTransfer`, events to `CyrinxTransport.Event`, in-memory linking
to `CyrinxSimulation`, audio diagnostics to C3-14, and ARC policy to C3-23.
Legacy `Config` endpoint/bound fields map to transport configuration, waveform
geometry to C3-04, role election to C3-18, and adaptive/gain policy to C3-23.
The raw macOS link is split by leaf: construction maps to the Apple audio
adapter in C3-14, lifecycle to `CyrinxTransport`, frame submission to
`CyrinxConnection`, and capture/diagnostic operations to C3-26.

### Move to experimental or simulation

- raw acoustic codecs and debug entry points;
- PHY complex/stub/configuration/sequential-state types;
- the direct Swift wrapper of prototype sounder bit loading;
- simulation profiles, options, results, and runner; and
- `AcousticPHYDebug` and its decode result.

### Replace

- `Event`, `ReceivedMessage`, `QoS`, and `Metrics`;
- `ChannelMetrics` and repositioning guidance;
- automatic-diversity result/reason and decoded bulk result values;
- Swift vDSP PHY/configuration/error entry points; and
- global MCS and repositioning functions.

Replacement targets are the generation-tagged state model,
`CyrinxTransfer`, `SendOptions`, `LinkEstimate`, the canonical C profile/batch
contracts, or the later plan item named in JSON. Legacy peer signature/key
metrics map to `SecurityStatus`; they do not become link-quality or identity
claims.

Likewise, measured SNR/EVM/CFO/PER values map to `LinkEstimate`; cumulative
frame/retry/reset/goodput counters map to C3-26 diagnostics; peer hardware and
buffer capabilities map to the connection snapshot; and MCS geometry maps to
C3-04 while recommendation policy maps to C3-23.

## Reviewed C decisions

### Retain

- `CYRINX_API`;
- `cyrinx_status_t`, every status constant, and the version/status diagnostic
  functions;
- the opaque session tag/typedef plus `cyrinx_start` and `cyrinx_close`; and
- the complete versioned receiver-contract-v1 family: 11 constants, its type
  and fields, and its query function.

Retaining the opaque session lifecycle assumes C3-17 keeps those spellings.
`cyrinx_open` is not retained because its unversioned configuration conflicts
with C3-03.

### Deprecate

- the role/security/bootstrap configuration, device constants,
  `cyrinx_default_config`, and config-bound `cyrinx_open`; and
- the existing bulk configuration, geometry, modulation/demodulation,
  automatic-diversity, chirp, guard, CRC-block, and diagnostic surface.

The bulk functions remain valuable replay-compatible wrappers over C3-04/C3-05
and therefore deprecate instead of disappearing.

The unversioned `cyrinx_config_t` is also classified by leaf rather than
blanket-assigned to ABI bootstrap work. Endpoint sizing remains with C3-03;
waveform/profile geometry maps to C3-04; role to C3-18; callbacks and callback
context to C3-17; dynamic CP, sensor ARC, gain, and leakage policy to C3-23; and
legacy signature/key/security inputs to explicit `SecurityStatus`.

### Move to experimental or DSP-internal

- deterministic RNG, PRBS, CRC/FEC, puncturing, QAM, and reliability helpers;
- both FFT plan tags, typedefs, and function families;
- every current PHY helper/stub declaration;
- `cyrinx_link_in_memory`; and
- sounder bit loading.

### Replace

- stream IDs/flags, QoS, frame type, gear, event, channel report, metrics,
  ARC policy, message metadata, callbacks, and related session functions;
- repositioning guidance; and
- the prototype sounder tier/recommendation model.

These are superseded by versioned profiles, reducer effects and events,
message transfers, link estimates, and later measurement/adaptation contracts.
Leaf targets mirror the Swift ownership split: gear and immutable profile
geometry map to C3-04; ARC, recommendation, and repositioning policy map to
C3-23; measured SNR/EVM/CFO/PER and repositioning evidence map to
`LinkEstimate`; cumulative counters map to C3-26; peer capabilities map to
`CyrinxConnection`; and legacy peer signature/key values map to
`SecurityStatus`. The `cyrinx_get_metrics` operation itself remains assigned to
C3-17, where the versioned C session access operation is introduced.

## Extraction and identity rules

The checker builds the `Cyrinx` module, reads SwiftPM's compilation target and
search paths, and runs `swift-symbolgraph-extract` at public access with
`--skip-synthesized-members`. It records:

- source-declared public types, cases, properties, initializers, methods, and
  global functions;
- the seven conformance-generated initializers still emitted by that mode; and
- conformance relationships, excluding the toolchain-only
  `Swift.SendableMetatype` marker.

It also canonicalizes the symbol-graph availability mixin, so changing an
introduced/deprecated/obsoleted constraint, rename, or deprecation message
changes the fingerprint.

It does not inventory inherited protocol-extension operations individually.
Those operations are owned by the recorded public conformance, not declared by
the Cyrinx module. This avoids treating toolchain additions as Cyrinx-authored
API.

The checked-in Swift inventory uses macOS as the reference surface. On Linux
the checker compares only entries marked portable; macOS-only entries remain
enforced by the macOS lane. `--write` is intentionally restricted to macOS so
refreshing from a narrower platform cannot delete reference-only API.

C3-01 found no current iOS-only public declaration. Until C3-02 adds a true
compiler-extracted macOS+iOS union lane, the checker evaluates nested
conditional branches against the macOS reference and rejects a `public` or
`open` declaration unless that branch is proven visible. Negated macOS
conditions, unknown custom conditions, and declarations whose access modifier
follows an attribute or another modifier are covered. Before interpreting
directives or access tokens, the temporary lexer suppresses nested comments,
Swift string/raw-string contents, and bare or extended regex literals so their
contents cannot masquerade as source structure. This is a temporary fail-closed
architectural prohibition, not a claim that macOS can discover iOS-only API.

Swift symbol graphs do not expose legacy raw-enum numeric values or static-let
initializer values. They are behavior rather than declaration identity in this
inventory. C3-02 must add semantic-value fixtures for retained/deprecated 2.x
raw enums and public constants before those values may change; C3-24 owns the
new 3.0 value-contract fixtures.

For C, the checker runs `clang -extract-api` over all six physical public
headers plus a Clang JSON AST pass. ExtractAPI emits 320 entries and omits or
collapses opaque `typedef struct tag alias;` identities. The AST restores the
five separately spellable session/FFT tag or typedef identities present today,
producing the checked 325 total. It also separates complete same-spelled named
tag/typedef pairs when introduced, deduplicates their collapsed ExtractAPI
aggregate, and records evaluated enum values, enum and field ordinals,
ExtractAPI availability, and explicit attributes on functions, parameters,
typedefs, records, enums, cases, and fields. The inventory includes:

- semantic macros, including all Windows import/export and non-Windows
  definitions of the logical `CYRINX_API`;
- enum types and constants;
- struct/typedef types and every public field;
- function-pointer typedefs;
- opaque tag/typedef identities; and
- every function declaration.

Header guards, compiler/environment inputs such as `_WIN32`, `CYRINX_BUILD`,
and `__cplusplus`, and unspellable anonymous AST nodes are not identities.
Every conditional `CYRINX_API` define/undef event is nonetheless fingerprinted
with its normalized effective `#if`/`#elif`/`#else` predicate, including
function-like macros. Public typedefs, cases, and fields remain included.

The current C manifest declares one all-platform surface. Until a
compiler-extracted configuration/platform union replaces that policy, the
checker rejects public C declarations inside platform-dependent or unknown
preprocessor branches. Conditional macro definitions remain allowed because
their definitions and predicates are inventoried. This prevents a Windows-only
function, for example, from remaining invisible to the default Clang pass.

Stable IDs use language, module, declaration kind, and public path. Current
paths are unique across all 698 entries. File, line, and declaration spelling
are not identity. A separate SHA-256 fingerprint covers normalized declaration,
availability, conformances, enum value/order, field order, and source
attributes. A signature change therefore preserves the classification while
requiring explicit review. A future same-path overload collision must add a
reviewed discriminator based on external labels and canonical parameter types.

## Validation and refresh workflow

Create a repository virtual environment once:

```sh
python3 -m venv .venv
```

The checker uses only the Python standard library.

Run its negative/fixture tests:

```sh
./scripts/check-api-inventory.sh --test
```

Compare the checked-in classifications with fresh Swift and Clang extraction:

```sh
./scripts/check-api-inventory.sh
```

For an intentional public-surface change:

```sh
./scripts/check-api-inventory.sh --write
```

`--write` refreshes technical fields and preserves reviewed decisions for
stable identities. A new identity is `new_unclassified`; a changed fingerprint
is `changed_unreviewed`; and a missing live identity remains in the file as a
`removal_unreviewed` tombstone. The normal check rejects every pending review
state. A reviewer must supply or reaffirm disposition, decision, compatibility,
replacement/removal target, and then mark the entry `reviewed`. Refresh cannot
silently erase a C3-35 migration promise.

`./scripts/check.sh` runs the checker tests and full extraction after formatting
and linting.

## Boundary with later ABI work

C3-01 classifies the complete source-level API/ABI identity set and freezes C
enum values, field order/source type, macro variants and predicates,
availability, and source attributes.
C3-03 owns target-specific C size, alignment, padded offsets, enum storage
width, calling convention, actual linker visibility, C consumers, and C++
consumers. C3-04 owns canonical profile values and hashes. C3-26 owns
diagnostic-schema compatibility fixtures. Those later baselines extend this
inventory; they do not replace its classification gate.

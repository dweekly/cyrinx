# ADR 0001: Canonical ownership and independent versioning

- **Status:** Accepted for Cyrinx 3.0
- **Date:** 2026-07-23
- **Plan:** C3-01

## Context

Cyrinx 2.x contains a portable C bulk receiver, a C session prototype, Swift
audio and PHY code, Kotlin DSP in the Android harness, and Python research
oracles. Similar operations can therefore be implemented or interpreted in
more than one language. That is useful for comparison, but it is not a safe
shipping architecture: state, framing, metrics, receiver selection, and
delivery claims can diverge without an ABI or compiler failure.

Cyrinx 3.0 also needs to evolve its C layout, acoustic framing, profile
registry, adaptation policy, and diagnostic files independently. Treating all
of those as the package semantic version would either force unnecessary
breakage or conceal real incompatibilities.

## Decision

The portable C implementation is the only production authority for modem and
protocol behavior. Swift and Kotlin expose platform-native facades and execute
effects, but do not implement a second state machine or shipping modem.

| State or behavior | Single authoritative owner | Other layers may |
|---|---|---|
| Modulation, demodulation, synchronization, FEC, coded PHY-block formation, and block-validity evidence | `CCyrinxDSP` | Marshal buffers and compare independent test oracles |
| Profile identity and geometry | `CCyrinxCore` registry | Present immutable generated values |
| Wire/control/message framing, fragmentation/reassembly, session, connection, transfer, inbound mailbox, retry, timer, and event order | `CCyrinxCore` reducer | Submit commands and render snapshots |
| Link measurements and receiver-selection evidence | `CCyrinxCore` using DSP results | Format or localize the resulting values |
| Profile/PHY-mode-selection evidence (adaptation inputs) | `CCyrinxCore` using DSP results, causally prior evidence only | Format or localize the resulting values |
| Swift object identity, async waiters, and subscriptions | `CyrinxCore` facade | Observe immutable C snapshots |
| Android object identity, coroutine waiters, and flows | Android binding facade | Observe immutable C snapshots |
| Audio device and route state | Platform audio adapter | Deliver bounded PCM and discontinuity effects |
| Application payload and user-interface state | Application | Submit messages and retain public snapshots |

An effect such as reading a clock, producing PCM, consuming PCM, or scheduling
a timer is executed outside the reducer. The reducer owns the decision to
request the effect and the interpretation of its result. Every effect carries
the session epoch and a reducer-issued effect token. Stop invalidates ordinary
tokens before teardown and invalidates teardown tokens only at the final
stopped commit; a late, duplicated, cancelled, or obsolete result cannot mutate
current state.

Mutable C state is never shared with a facade. Commands copy or borrow data only
for the documented duration of the call. Events and snapshots are immutable
values. A facade cache is a projection and is discarded when the generation
protocol requires snapshot recovery.

The complete state and lifetime model is normative in
[`CYRINX_3_SEMANTIC_CONTRACT.md`](../CYRINX_3_SEMANTIC_CONTRACT.md).

## Version axes

The following values are independent. No implementation may infer one value
from another.

| Axis | Identifies | Compatibility rule | Planned owner |
|---|---|---|---|
| Semantic version | Published Swift package, AAR, and SDK behavior | Semantic-version rules for the public SDK | Release metadata |
| C ABI version | Exported calling convention and versioned structure prefix | Caller and library validate supported ABI and `struct_size` | C3-03 |
| Wire version | Acoustic frame grammar and control negotiation | Peers must negotiate a mutually supported wire version | C3-12/C3-17 |
| Profile ID and hash | Immutable waveform, FEC, timing, and receiver contract | ID and canonical content hash must both agree | C3-04 |
| Policy version | Adaptation, selection, retry, and recovery rules | Policy is reported; incompatible policies cannot be silently treated as equal | C3-23 |
| Diagnostic schema version | Replay, trace, snapshot, and support-bundle encoding | Readers reject an unknown required version and ignore declared optional fields | C3-26 |

Changing a profile does not reuse its ID. Changing policy does not change the
wire version unless the encoded protocol changes. A diagnostic schema bump does
not imply a C ABI bump. A semantic release records the versions it contains but
does not replace their compatibility checks.

## Migration contract

The classified Cyrinx 2.x surface remains available through C3-35. Deprecated
or replaced declarations are compatibility shims, not alternate authorities.
Their implementation must delegate to the canonical 3.0 path once that path
exists. Removal requires the reviewed migration step and, after the 3.0
release, a later semantic major version.

Research oracles may remain independent implementations. They must be labeled
as oracles, simulations, or experimental code and cannot be linked into the
accepted production runtime path.

## Consequences

- Binding parity is tested against one C behavior rather than reconciled after
  two implementations drift.
- Platform audio code remains platform-specific without acquiring protocol
  authority.
- Version compatibility becomes explicit in artifacts and traces.
- The C core must accept injected clocks, timers, randomness, and audio effects
  so deterministic replay can drive the same reducer.
- New public behavior that cannot name one owner does not meet the architecture
  merge gate.

## Rejected alternatives

- **Independent native modems in Swift and Kotlin:** rejected because parity
  tests do not prevent runtime drift after later changes.
- **Host decoding as a supported phone runtime:** rejected in ADR 0003 because
  it does not provide an endpoint-local transport.
- **One global “protocol version”:** rejected because ABI, wire, profile,
  policy, and diagnostic changes have different compatibility requirements.

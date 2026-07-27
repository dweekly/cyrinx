# Cyrinx Chat sample

Fresh as of 2026-07-27. The chat sample is a **product-level acceptance
client** for the Cyrinx 3.0 SDK, not a second HIL diagnostics panel — see
[`docs/CYRINX_3_PLAN.md`](../../docs/CYRINX_3_PLAN.md) Phase F, "Chat sample
application." It exists to prove that a small, real application can be built
entirely on the public `ChatTransportClient`/UI-model contract, small enough
that Cyrinx's transport behavior (queued/transmitting/delivered/failed,
connection state, link budget, peer loss and recovery) stays visible in the
UI rather than getting absorbed into app-specific abstraction.

This document, [`ENVELOPE.md`](ENVELOPE.md), [`CONTRACT.md`](CONTRACT.md),
and the fixtures under `fixtures/` are the **specification** of C3-28. The
Swift package (`Apps/Chat/CyrinxChatKit`) and the Kotlin module
(`Apps/Chat/android`) implement it in this same PR: both suites pass every
envelope golden vector, and the committed `fixtures/traces/` goldens are
generated from the Swift implementation and checked byte-identical from
Kotlin.

## Security status — read this first

**The acoustic link is unauthenticated.** There is no identity
verification, no encryption, and no protection against a third party
listening to or injecting into the channel. `ChatMessageDisplayStatus
.delivered` means the transport acknowledged receipt of bytes — it is not
proof of who received them (see `CONTRACT.md` §1.5's honest-delivery
caveat). Every build of this sample must show a persistent
**"Unauthenticated acoustic link"** notice
(`ChatAccessibilityID.unauthenticatedNotice` / `ChatTestTags
.unauthenticatedNotice`, see `CONTRACT.md` §5), and the sample must never
invite a user to send secrets, passwords, or anything sensitive through it.
This matches `docs/CYRINX_3_PLAN.md`'s release-checklist item "Security is
labeled unauthenticated in API docs, sample UI, and release notes."

## Scope

- Discover peers advertising the Cyrinx Chat service.
- Connect to one peer and exchange UTF-8 text messages (2 KiB body cap,
  see `ENVELOPE.md`).
- Show queued / transmitting / delivered / failed state for every outgoing
  message.
- Show connection state plus a coarse directional link budget
  (`controlOnly`, `text`, `thumbnail`, `bulk`), backed by the numeric
  `ChatLinkBudget` (see `CONTRACT.md` §1.4).
- Explain permission, route, peer-loss, degradation, recovery, and
  repositioning actions in plain language.
- Offer a deterministic simulated transport
  (`SimulatedChatTransportClient`, `CONTRACT.md` §2) so the sample and its
  UI tests run without microphones, speakers, or two physical devices.
- Export a redacted support bundle from an advanced diagnostics sheet
  (later stage; diagnostics-button accessibility ID is reserved now, see
  `CONTRACT.md` §5).

Conversation state is in-memory only for 3.0.

## Non-goals

No UI in this stage (SwiftUI/Compose UI lands in C3-29/C3-30). No live SDK
adapter (C3-31 — this stage and C3-29/C3-30 build and test entirely
against the simulated transport). No accounts, cloud sync, background
delivery, push notifications, read receipts, typing indicators, contact
identity, attachments, or encryption. CI for this lane runs as
`.github/workflows/chat-c3-28.yml` (Python oracle self-test, Swift suite,
Gradle `check` including the JVM-17 consumer compile), kept in its own
workflow file so it cannot conflict with the repo-wide C3-02 CI baseline
being developed on a separate PR track.

## Simulator-first development

`CyrinxChatKit` (Swift) must not depend on the Cyrinx SDK package, and the
Kotlin `chatkit` module must not depend on any Android API — this is a
C3-28 merge gate, not a style preference. Both platforms develop and test
entirely against `SimulatedChatTransportClient` (`CONTRACT.md` §2): two
in-process client instances, paired, exchanging real envelope v1 bytes
through the same codec validated in `ENVELOPE.md`, driven by a virtual
clock and a seeded deterministic PRNG (SplitMix64) instead of real audio
hardware, real time, or real randomness. The six pinned scenario scripts
in `CONTRACT.md` §3 (`happyPair`, `peerLoss`, `degradedThenRecovered`,
`sendFailure`, `duplicateIncoming`, `slowLink`) are the whole surface a
contributor needs to exercise the sample's behavior end to end before any
live hardware exists. The live Cyrinx 3 SDK adapter is added in C3-31,
behind the same `ChatTransportClient` contract, so the simulator-first
code does not change shape when that lands.

## Directory map

| Path | Description | Status |
|---|---|---|
| [`README.md`](README.md) | This file: scope, security notice, simulator-first story, test commands. | present |
| [`ENVELOPE.md`](ENVELOPE.md) | Envelope v1 byte-layout spec, error taxonomy, golden-vector JSON schema, regeneration policy. | present |
| [`CONTRACT.md`](CONTRACT.md) | Platform-neutral app contracts (`ChatPeer`, `ChatEvent`, `ChatTransportClient`, ...), the simulated client, the six scenario scripts, the trace schema, and the accessibility-ID/launch-argument registry. | present |
| `fixtures/chat-envelope-golden.json` | Committed golden vectors for the envelope v1 codec (22 vectors: 6 decode, 16 error, including the sender-local sequence field frozen by C3-01). Diffed in review, not a build artifact — see `ENVELOPE.md` §9. | present |
| `fixtures/tools/generate_golden.py` | Python-stdlib-only reference codec + fixture generator + `--self-test`. The third independent implementation of the codec, kept permanently as a diagnostic ("ship the spike"). | present |
| `fixtures/traces/` | Golden JSON-lines traces for the `happyPair` and `peerLoss` scenarios (`CONTRACT.md` §4), generated from the Swift implementation in the verify stage and checked byte-identical from Kotlin. | committed — seed 1; regenerate only together with a `CONTRACT.md` change |
| `CyrinxChatKit/` | Standalone Swift package (Swift Testing tests). Must not depend on the Cyrinx SDK package. | implemented — `swift test --package-path Apps/Chat/CyrinxChatKit` |
| `android/` | Gradle project, pure-JVM Kotlin module `chatkit` (JUnit tests), following `Apps/HIL/android`'s wrapper/toolchain conventions. Must not depend on Android APIs. | implemented — `cd Apps/Chat/android && ./gradlew check` |

## Test commands

Canonical local commands, from the repository root (all runnable in this
commit; `gradlew check` also runs the `:consumer-compile-check` module,
which compiles against the public API under a JVM-17 toolchain):

```console
swift test --package-path Apps/Chat/CyrinxChatKit
cd Apps/Chat/android && ./gradlew check
```

The golden-fixture generator's self-test (the third, Python, codec
implementation — the cross-language oracle):

```console
python3 -m venv .venv
.venv/bin/python Apps/Chat/fixtures/tools/generate_golden.py --self-test
```

All three run in CI on every change under `Apps/Chat/` via
`.github/workflows/chat-c3-28.yml`.

See `ENVELOPE.md` §9 for the full regeneration policy (regeneration is
permitted only alongside a spec change, and the resulting JSON is
committed/diffed, not treated as disposable build output).

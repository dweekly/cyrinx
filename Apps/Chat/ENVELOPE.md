# Chat envelope v1 — byte spec and golden-vector contract

Fresh as of 2026-07-27. Pinned by the C3-28 design brief (orchestrator-owned,
not itself checked into this tree) and by
[`docs/CYRINX_3_PLAN.md`](../../docs/CYRINX_3_PLAN.md) Phase F, "Chat message
envelope." This document is authoritative for the wire layout; the Swift
codec (`Apps/Chat/CyrinxChatKit`) and the Kotlin codec
(`Apps/Chat/android/chatkit`) — both committed alongside this document —
must match it exactly, and both are checked against the golden
vectors in [`fixtures/chat-envelope-golden.json`](fixtures/chat-envelope-golden.json),
not against each other directly.

**The envelope is an application-layer format, not the Cyrinx transport wire
protocol.** It is carried as an opaque payload over whatever
`ChatTransportClient` implementation is in use (the simulated client, or the
live Cyrinx 3 SDK adapter in C3-31); Cyrinx's own framing, sessions, and ARQ
are a layer below this and have their own independent versioning.

## 1. Byte layout

Big-endian, strict sequential parse — every field is read in the order
below; the first violation encountered stops parsing and returns the named
error. There is no lookahead and no partial recovery.

### 1.1 Fixed fields (always present, in order)

| Offset | Size | Field | Value / range | Violation → error |
|---|---|---|---|---|
| 0 | 1 B (`u8`) | `version` | `0x01` | not `0x01` → `unknownVersion` (stop) |
| 1 | 1 B (`u8`) | `kind` | `0x01` (text; only value defined in v1) | not `0x01` → `unknownKind` (stop) |
| 2 | 1 B (`u8`) | `flags` | bit0 = replyTo present; bits 1–7 must be `0` | any bit 1–7 set → `malformed` |
| 3 | 16 B | `messageId` | opaque 128-bit ID | — |

### 1.2 Variable-position fields (offsets depend on `flags` bit 0)

`sequence` is new in this revision.
[`docs/CYRINX_3_PLAN.md`](../../docs/CYRINX_3_PLAN.md)'s "Chat message
envelope" section (frozen by C3-01, including its Reconciliation paragraph)
requires "a nonzero sender-local 64-bit sequence number, strictly increasing
within the current ephemeral peer/chat-connection scope," so a receiver can
buffer bounded out-of-order arrivals, discard duplicate IDs, and surface an
explicit gap when a missing sequence is skipped — capabilities the original
C3-28 envelope (PR #70, pre-freeze) did not have. The table below is that
amendment to the byte layout; see [`CONTRACT.md`](CONTRACT.md) §2 for the
corresponding simulator/client semantics (outgoing sequence assignment, the
receiver reorder window, and `messageGap` surfacing).

| Field | Present when | Size | Value / range | Violation → error |
|---|---|---|---|---|
| `replyToId` | `flags` bit0 = 1 | 16 B | opaque 128-bit ID | (presence itself is unconditional once bit0 is set — there is no separate error for this field beyond truncation, see §2) |
| `senderIdLen` | always, next field after `messageId`/`replyToId` | 1 B (`u8`) | `1..32` inclusive | `0` or `>32` → `malformed` |
| `senderId` | always | `senderIdLen` bytes | opaque bytes (ephemeral transport peer ID) | — |
| `sequence` | always | 8 B (`u64`, big-endian) | `1..0xFFFFFFFFFFFFFFFF` inclusive | `0` → `malformed` |
| `bodyLen` | always | 2 B (`u16`, big-endian) | `0..2048` inclusive | `>2048` → `oversizeBody` |
| `body` | always | `bodyLen` bytes | UTF-8; zero length is valid | invalid UTF-8 → `invalidUtf8` |
| (end) | — | — | no bytes may remain | any leftover byte(s) → `malformed` |
| (any field cut short by end of buffer) | — | — | — | → `truncated` |

### 1.3 Concrete offsets, both shapes

Without `replyTo` (`flags` bit0 = 0):

```
0        1     2      3                 19            20+L        28+L        30+L
+--------+-----+------+-----------------+-------------+-----------+-----------+---------+
|version | kind| flags|   messageId     | senderIdLen | senderId  | sequence  |bodyLen  | body...
| 1B     | 1B  | 1B   |     16B         |   1B        |  L bytes  |  8B       | 2B      |
+--------+-----+------+-----------------+-------------+-----------+-----------+---------+
```

With `replyTo` present (`flags` bit0 = 1):

```
0        1     2      3            19           35            36+L        44+L        46+L
+--------+-----+------+------------+------------+-------------+-----------+-----------+---------+
|version | kind| flags| messageId  | replyToId  | senderIdLen | senderId  | sequence  |bodyLen  | body...
| 1B     | 1B  | 1B   |    16B     |    16B     |   1B        |  L bytes  |  8B       | 2B      |
+--------+-----+------+------------+------------+-------------+-----------+-----------+---------+
```

(`L` = `senderIdLen`, the actual sender-ID length of that particular
envelope, 1..32.)

## 2. Strict sequential parse order (normative)

A conforming decoder reads fields in exactly this order and returns on the
first failure. Two range checks are deliberately evaluated **before** the
decoder attempts to consume the bytes that length describes — this is the
one non-obvious rule in this spec and both `senderIdLen` and `bodyLen` obey
it identically:

1. Read 1 byte → `version`. Buffer too short → `truncated`. `version != 1` →
   `unknownVersion`, stop.
2. Read 1 byte → `kind`. Buffer too short → `truncated`. `kind != 1` →
   `unknownKind`, stop.
3. Read 1 byte → `flags`. Buffer too short → `truncated`. Any bit outside
   bit0 set → `malformed`, stop.
4. Read 16 bytes → `messageId`. Buffer too short → `truncated`.
5. If `flags` bit0 is set: read 16 bytes → `replyToId`. Buffer too short →
   `truncated`.
6. Read 1 byte → `senderIdLen`. Buffer too short → `truncated`. **Range-check
   `senderIdLen` against `1..32` immediately, before attempting to read that
   many bytes.** Out of range → `malformed`, stop. (This is why
   `senderIdLen=33` with zero trailing bytes is `malformed`, not
   `truncated` — the decoder never gets far enough to ask for 33 bytes.)
7. Read `senderIdLen` bytes → `senderId`. Buffer too short → `truncated`.
8. Read 8 bytes, big-endian → `sequence`. Buffer too short → `truncated`.
   **Range-check `sequence != 0` immediately: `sequence == 0` → `malformed`,
   stop.** `sequence` is not a length prefix — no further bytes depend on its
   value the way `senderId`/`body` depend on `senderIdLen`/`bodyLen` — but it
   is checked in this same step, before parsing advances to `bodyLen`, under
   the same immediate-range-check discipline as steps 6 and 9: reject an
   invalid field value as soon as it is fully read, rather than deferring
   past fields that don't need it.
9. Read 2 bytes, big-endian → `bodyLen`. Buffer too short → `truncated`.
   **Range-check `bodyLen` against `0..2048` immediately, before attempting
   to read that many bytes.** `bodyLen > 2048` → `oversizeBody`, stop. (Same
   reasoning as step 6: `bodyLen=2049` with zero body bytes present is
   `oversizeBody`, not `truncated`.)
10. Read `bodyLen` bytes → `body`. Buffer too short → `truncated`.
11. Validate `body` as UTF-8 (strict — overlong encodings, truncated
    multi-byte sequences, and lone continuation/lead bytes are all
    rejected, not just outright invalid byte values). Invalid → `invalidUtf8`.
12. If any bytes remain in the buffer after step 11 → `malformed` (trailing
    bytes).

The reference implementation of this algorithm is `decode_envelope()` in
[`fixtures/tools/generate_golden.py`](fixtures/tools/generate_golden.py);
its comments cite this section for every branch.

## 3. Canonical encoding

Encoding is canonical: for **every** `expect: "decode"` golden vector,
re-encoding the decoded value must reproduce the input bytes exactly,
byte for byte. There is exactly one legal encoded form of any given
`(version, kind, messageId, replyToId, senderId, sequence, body)` tuple — no
padding, no alternate flag combinations for the same semantic value, no
field reordering.

The encoder enforces the same bounds as the decoder (senderId length
1..32, sequence nonzero, body length 0..2048, body must already be valid
UTF-8) and raises the matching named error rather than silently clamping or
truncating. Encoders never need to *produce* `truncated` or
`malformed`-trailing-bytes errors — those only arise from parsing an
externally supplied byte buffer — so in practice an encoder only needs to
guard `unknownVersion`, `unknownKind`, `malformed` (bad ID/senderId
lengths, zero sequence), `oversizeBody`, and `invalidUtf8`.

## 4. Maximum encoded size

```
version(1) + kind(1) + flags(1) + messageId(16) + replyToId(16)
  + senderIdLen(1) + senderId(32 max) + sequence(8) + bodyLen(2) + body(2048 max)
= 3 + 16 + 16 + 1 + 32 + 8 + 2 + 2048
= 2126 bytes
```

The `body_max_2048` golden vector is deliberately constructed at exactly
this maximum (`replyTo` present, 32-byte `senderId`, 2048-byte body) so the
2126-byte figure has a concrete, hand-verifiable instance in the fixture,
not just an arithmetic claim in this file.

## 5. Error taxonomy

Six error classes, identical **names** across all three implementations.
None of them carry a numeric code on the wire — the envelope itself has no
"status" byte; these are decode-time/encode-time results, not wire fields.

| Name | Fires on |
|---|---|
| `unknownVersion` | `version` byte is not `0x01` |
| `unknownKind` | `kind` byte is not `0x01` |
| `malformed` | undefined `flags` bit set; `senderIdLen` outside `1..32`; `sequence == 0`; trailing bytes after a complete envelope |
| `truncated` | buffer ends before a field (of any kind) can be fully read |
| `oversizeBody` | `bodyLen` field value exceeds `2048`, checked before reading body bytes |
| `invalidUtf8` | `body` bytes, once fully read, do not form strict, valid UTF-8 |

Swift: `enum ChatEnvelopeError: Error, Equatable` with one case per row above
(implementation lands with the rest of `CyrinxChatKit`). Kotlin: a sealed
class with one subtype per row (implementation lands with `android/chatkit`).
Python (this fixture generator, an oracle only, not a production codec): one
exception class per row, each carrying an `error_name: str` class attribute
matching the table's `Name` column exactly — see `ERROR_CLASSES_BY_NAME` in
`fixtures/tools/generate_golden.py`.

Duplicate-message-ID handling is explicitly **not** part of this taxonomy —
it is application/simulated-client-layer dedup behavior (see
[`CONTRACT.md`](CONTRACT.md)'s `duplicateIncoming` scenario), not a codec
error. The codec has no notion of "this ID was seen before"; it decodes one
buffer at a time, statelessly.

## 6. Sender wall-clock time is not delivery-ordering evidence

The envelope carries no timestamp field at all — this is deliberate.
`ChatMessage.sentAtWallClockMs` (see [`CONTRACT.md`](CONTRACT.md)) is
populated by the *receiving* client at receipt time from its own local
clock, purely as UI display metadata ("a few seconds ago"). Peer clocks are
not assumed synchronized, there is no NTP-equivalent in this sample, and no
code anywhere may use a sender-side or receiver-side wall-clock value to
infer message order across peers.

Within one sender's connection scope, the envelope's own `sequence` field
(§1.2 — nonzero, strictly increasing per accepted `send()`) is the real
ordering evidence: it is what lets a receiver buffer bounded out-of-order
arrivals and detect a skipped message (see [`CONTRACT.md`](CONTRACT.md) §2's
outgoing-sequence-assignment, reorder-window, and `messageGap` rules). It
says nothing about wall-clock timing, and it is still not comparable across
two different senders — a fresh connection scope also starts a fresh
`sequence` count (see the Reconciliation paragraph in
[`docs/CYRINX_3_PLAN.md`](../../docs/CYRINX_3_PLAN.md)'s "Chat message
envelope" section). Separately, each client's own local `eventSeq` (see
`CONTRACT.md`) is an unrelated counter: monotonic per client instance,
spanning every `ChatEvent` kind that client emits (not just messages), and
likewise not comparable across clients.

## 7. Version bumps use the version byte, never flag bits

If a future envelope version needs new fields, it gets a new `version` byte
value (`0x02`, ...) with its own layout table in a new section of this
document. Flag bits are reserved for boolean modifiers *within* a fixed
version's layout (today: "does a replyTo field follow"), never as a
side-channel to smuggle in version-dependent behavior. A decoder that sees
an unrecognized `version` byte stops immediately (`unknownVersion`) rather
than trying to interpret unknown flag bits heuristically.

## 8. Golden vector JSON schema

File: [`fixtures/chat-envelope-golden.json`](fixtures/chat-envelope-golden.json).

```json
{
  "schema": "cyrinx-chat-envelope-golden-v1",
  "generator": "fixtures/tools/generate_golden.py",
  "vectors": [
    {
      "name": "text_ascii_min",
      "expect": "decode",
      "bytes_hex": "…",
      "decoded": {
        "version": 1,
        "kind": "text",
        "messageIdHex": "…",
        "replyToIdHex": null,
        "senderIdHex": "…",
        "sequence": 1,
        "body": "hi"
      },
      "comment": "…"
    },
    {
      "name": "reject_unknown_version",
      "expect": "error",
      "bytes_hex": "…",
      "error": "unknownVersion",
      "comment": "…"
    }
  ]
}
```

Field notes:

- `bytes_hex` — the complete encoded envelope, lowercase hex, no `0x`
  prefix, no separators.
- `expect` — `"decode"` or `"error"`; determines which of `decoded` /
  `error` is present (mutually exclusive).
- `decoded` — present only when `expect == "decode"`. `messageIdHex`,
  `senderIdHex` are lowercase hex strings; `replyToIdHex` is `null` when
  `flags` bit0 is clear, otherwise a lowercase hex string; `body` is the
  decoded Unicode string (not hex — this is the whole point of the
  UTF-8-validation vectors: comparing decoded *text*, not bytes); `kind` is
  always the string `"text"` in v1 (the only defined kind).
  `version` is the JSON number `1`. `sequence` is the JSON number matching
  the wire `u64` value exactly — Python's `json` module preserves arbitrary
  integer precision natively, and the Swift/Kotlin sides of this fixture
  must decode it straight into `UInt64`/`ULong`, never through a `Double`
  intermediate, so `sequence_max_u64_accepted`'s decoded
  `18446744073709551615` round-trips exactly (§10). Every `expect:
  "decode"` vector's `sequence` is a small ascending positive integer (`1,
  2, 3, …`) assigned by the generator in vector-definition order, except
  `sequence_max_u64_accepted`, whose whole point is the boundary value.
- `error` — present only when `expect == "error"`; one of the six names in
  §5 exactly.
- `comment` — free-text provenance/rationale, not machine-checked.
- Vector `name`s are unique within the file (enforced by the generator).

## 9. Fixture provenance and regeneration policy

`fixtures/chat-envelope-golden.json` is **generated, then committed** — it
is source, not a build artifact, and is reviewed/diffed like any other file
in this repository (matching the convention already established by
[`Tests/Fixtures/golden/`](../../Tests/Fixtures/golden/) for the bulk-PHY
vectors).

Regeneration is permitted **only alongside a spec change** to this
document or to §1's byte layout — never as a routine "just rerun the
script" step, and never to silently absorb a codec bug fix. If a vector's
expected bytes or decoded fields need to change, the corresponding change
to this document's layout/error tables must land in the same commit, and
the PR description must say which section changed and why.

To regenerate from the repository root:

```console
python3 -m venv .venv          # if the venv does not already exist
.venv/bin/python Apps/Chat/fixtures/tools/generate_golden.py --self-test
# iterate on generate_golden.py until self-test passes, then:
.venv/bin/python Apps/Chat/fixtures/tools/generate_golden.py
git diff --stat Apps/Chat/fixtures/chat-envelope-golden.json
```

`--self-test` decodes every `expect: "decode"` vector and checks every
decoded field, re-encodes each and checks byte-identity against
`bytes_hex` (§3's canonical-encoding rule), attempts to decode every
`expect: "error"` vector and checks it raises exactly the named error, and
exits non-zero on the first mismatch. The no-flag invocation runs the same
self-test before writing the file, so a broken generator can never produce
a fixture that fails its own checks.

## 10. Required coverage (traceability)

Every row below has exactly one vector in the fixture (three vectors for
the "invalid UTF-8" row — see the note under that row, a scope decision the
design brief left to the writer of this document).

| Required coverage | Vector name(s) |
|---|---|
| ASCII minimal | `text_ascii_min` |
| Empty body | `empty_body` |
| Body exactly 2048 bytes | `body_max_2048` |
| Sequence at max `u64` (`0xFFFFFFFFFFFFFFFF`) accepted — scope exhaustion at that value is client-layer behavior (`CONTRACT.md`), not a codec concern | `sequence_max_u64_accepted` |
| Crafted `bodyLen=2049` → `oversizeBody` | `reject_oversize_body_len_2049` |
| Unicode body mixing emoji + CJK + combining marks | `unicode_mixed_emoji_cjk_combining` |
| Invalid UTF-8 (0xFF byte; truncated multibyte; overlong encoding) — treated as three vectors, one per shape, rather than one vector picking a single example; see note below | `reject_invalid_utf8_0xff_byte`, `reject_invalid_utf8_truncated_multibyte`, `reject_invalid_utf8_overlong_encoding` |
| Unknown version 0x02 | `reject_unknown_version` |
| Unknown kind 0x7F | `reject_unknown_kind` |
| Flags bit1 set → `malformed` | `reject_flags_unknown_bit` |
| replyTo present valid | `replyto_present_valid` |
| `senderIdLen=0` → `malformed` | `reject_sender_id_len_zero` |
| `senderIdLen=33` → `malformed` | `reject_sender_id_len_33` |
| `sequence=0` → `malformed` | `reject_sequence_zero` |
| Truncated after version byte | `reject_truncated_after_version` |
| Truncated mid-messageId | `reject_truncated_mid_message_id` |
| Truncated mid-senderId | `reject_truncated_mid_sender_id` |
| Truncated mid-sequence | `truncated_mid_sequence` |
| `bodyLen` larger than remaining bytes → `truncated` | `reject_body_len_exceeds_remaining` |
| Single trailing byte → `malformed` | `reject_single_trailing_byte` |

**22 vectors total** (6 `expect: "decode"`, 16 `expect: "error"`).

Duplicate-ID handling is intentionally absent from this table — per §5 it is
app-layer behavior, exercised by the `duplicateIncoming` scenario in
[`CONTRACT.md`](CONTRACT.md), not a codec-level golden vector. Sequence
*exhaustion* (what happens once a scope's sequence counter would need to
wrap past `0xFFFFFFFFFFFFFFFF`) is likewise absent: the codec accepts any
nonzero `u64`, including the maximum, per `sequence_max_u64_accepted` above;
exhaustion handling itself is client-layer behavior reserved for a later
stage, out of scope for this amendment (see `docs/CYRINX_3_PLAN.md`'s
Reconciliation paragraph).

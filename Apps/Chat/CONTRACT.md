# Chat application contract — types, transport client, simulator, traces

Fresh as of 2026-07-19. Platform-neutral application contracts for the C3-28
chat sample, pinned by the C3-28 design brief and
[`docs/CYRINX_3_PLAN.md`](../../docs/CYRINX_3_PLAN.md) Phase F ("Sample
architecture"). This document is the spec; the Swift realization
(`Apps/Chat/CyrinxChatKit`) and the Kotlin realization
(`Apps/Chat/android/chatkit`) are committed alongside it and
must match the shapes below. See [`ENVELOPE.md`](ENVELOPE.md) for the wire
format these types carry over the transport, and
[`README.md`](README.md) for product scope and non-goals.

Every place this document had to make a call the brief did not pin is marked
**DECISION (not pinned by the brief):** inline, and re-listed in the C3-28
spec-stage report.

## 1. Contract vocabulary

These types are consumed by the UI models (`Apple ChatModel`, `Android
ChatViewModel`) and produced by any `ChatTransportClient` implementation —
the simulated one here, or the live Cyrinx 3 SDK adapter added in C3-31. The
UI layer never touches Cyrinx C/JNI directly (see `README.md`).

### 1.1 `ChatPeer`

| Field | Type | Notes |
|---|---|---|
| `id` | opaque bytes | The peer's ephemeral transport ID — the same value that appears as `senderId` in envelopes that peer sends. Not authenticated (see the unauthenticated-link notice, `README.md`). |
| `displayName` | derived `String` | `"Peer-" + uppercase-hex(id[0:2])`, e.g. id starting `0xb1 0xa2...` → `"Peer-B1A2"`. Purely cosmetic; never used for equality or lookup. |
| `discoveredAtMs` | `Int64` / `Long` | Virtual (simulated) or monotonic (live) time the peer was first observed. |

Equality is by `id` only — two `ChatPeer` values with the same `id` but a
stale `discoveredAtMs` are still "the same peer" for dictionary/set
purposes; `peerUpdated` exists precisely to carry a refreshed value for an
already-known `id`.

```swift
struct ChatPeer: Equatable, Identifiable {
    let id: Data
    var discoveredAtMs: Int64
    var displayName: String {
        "Peer-" + id.prefix(2).map { String(format: "%02X", $0) }.joined()
    }
    static func == (lhs: Self, rhs: Self) -> Bool { lhs.id == rhs.id }
}
```

```kotlin
data class ChatPeer(val id: ByteArray, val discoveredAtMs: Long) {
    val displayName: String
        get() = "Peer-" + id.take(2).joinToString("") { "%02X".format(it) }
    override fun equals(other: Any?) = other is ChatPeer && id.contentEquals(other.id)
    override fun hashCode() = id.contentHashCode()
}
```

### 1.2 `ChatConnectionState`

UI-facing projection only — **the transport owns protocol state**; this
enum is a coarse view for display, not the source of truth. Four cases:
`disconnected(reason: String?)`, `connecting`, `connected`, `degraded`.

```swift
enum ChatConnectionState: Equatable {
    case disconnected(reason: String?)
    case connecting
    case connected
    case degraded
}
```

```kotlin
sealed class ChatConnectionState {
    data class Disconnected(val reason: String?) : ChatConnectionState()
    object Connecting : ChatConnectionState()
    object Connected : ChatConnectionState()
    object Degraded : ChatConnectionState()
}
```

**DECISION (not pinned by the brief):** the `reason` vocabulary for
`disconnected(reason:)`, `peerLost(reason:)`, and
`failed(reason:)` is free-text (`String`), not a closed enum — the brief
gives the shape (`reason?` / `reason`) but not a fixed value set. §5's
scenario scripts use a small, consistent set of reason strings
(`userInitiated`, `peerSilenceTimeout`, `noAcknowledgment`,
`transportFault`) as illustrative values, not an exhaustive product
vocabulary; the implementation stage may need to widen this.

### 1.3 `LinkBudgetClass`

Four cases, wire/trace string form (used in JSON-lines traces, §6, and
matched against product-scope language in `docs/CYRINX_3_PLAN.md`):
`controlOnly`, `text`, `thumbnail`, `bulk`.

```swift
enum LinkBudgetClass: String, Equatable {
    case controlOnly, text, thumbnail, bulk
}
```

```kotlin
enum class LinkBudgetClass(val wireName: String) {
    CONTROL_ONLY("controlOnly"),
    TEXT("text"),
    THUMBNAIL("thumbnail"),
    BULK("bulk"),
}
```

**DECISION (not pinned by the brief):** Kotlin enum constant identifiers
follow Kotlin's `UPPER_SNAKE_CASE` convention rather than literally
reproducing `controlOnly` as an identifier; the `wireName` property is the
cross-language-identical string actually used in traces and any
serialized form, so the two platforms remain byte-identical where it is
observable (trace files, accessibility/launch-argument strings), while
each stays idiomatic where it is not.

### 1.4 `ChatLinkBudget`

The "numeric `LinkEstimate` backing" the plan requires (see
`docs/CYRINX_3_PLAN.md` Phase F product scope: "coarse directional link
budget ... backed by the numeric `LinkEstimate`").

| Field | Type | Notes |
|---|---|---|
| `classification` | `LinkBudgetClass` | |
| `txLowerBoundBps` | `Int?` / `Int?` (nullable) | Conservative outbound lower bound; `null` when not yet estimated. |
| `rxLowerBoundBps` | `Int?` / `Int?` (nullable) | Conservative inbound lower bound; `null` when not yet estimated. |
| `confidence` | `Double`, `0.0...1.0` | |
| `ageMs` | `Int` | How stale this estimate is, in virtual/monotonic ms. |

```swift
struct ChatLinkBudget: Equatable {
    var classification: LinkBudgetClass
    var txLowerBoundBps: Int?
    var rxLowerBoundBps: Int?
    var confidence: Double
    var ageMs: Int
}
```

```kotlin
data class ChatLinkBudget(
    val classification: LinkBudgetClass,
    val txLowerBoundBps: Int?,
    val rxLowerBoundBps: Int?,
    val confidence: Double,
    val ageMs: Int,
)
```

### 1.5 `ChatMessageDisplayStatus` (outgoing messages)

Four cases: `queued`, `transmitting`, `delivered`, `failed(reason: String)`.

**Honest-delivery caveat (must appear in every surface that renders this
status, per the design brief):** `delivered` means **transport
acknowledgment only** —

- Queue acceptance (`send()` returning a `messageIdHex`) is **never**
  delivery. A message can sit in `queued` or `transmitting` indefinitely
  and still ultimately `fail`.
- Transport acknowledgment is **not authenticated identity.** The
  acoustic link has no authentication (see `README.md`'s security notice),
  so `delivered` tells you a peer's transport acked receipt of these
  bytes — it does not tell you which peer, cryptographically, actually
  received or read them.

```swift
enum ChatMessageDisplayStatus: Equatable {
    case queued
    case transmitting
    case delivered
    case failed(reason: String)
}
```

```kotlin
sealed class ChatMessageDisplayStatus {
    object Queued : ChatMessageDisplayStatus()
    object Transmitting : ChatMessageDisplayStatus()
    object Delivered : ChatMessageDisplayStatus()
    data class Failed(val reason: String) : ChatMessageDisplayStatus()
}
```

### 1.6 `ChatMessage`

| Field | Type | Notes |
|---|---|---|
| `id` | opaque bytes (128-bit) | Same value as the envelope's `messageId`. |
| `direction` | `incoming \| outgoing` | |
| `body` | `String` | Decoded UTF-8 text. |
| `senderPeerIdHex` | `String` | Lowercase hex. |
| `sentAtWallClockMs` | `Int64` / `Long` | **Display metadata only — see `ENVELOPE.md` §6. Peer clocks are not synchronized; never use this for cross-peer ordering.** |
| `status` | `ChatMessageDisplayStatus` | |

```swift
struct ChatMessage: Equatable, Identifiable {
    enum Direction: Equatable { case incoming, outgoing }
    let id: Data
    var direction: Direction
    var body: String
    var senderPeerIdHex: String
    var sentAtWallClockMs: Int64
    var status: ChatMessageDisplayStatus
}
```

```kotlin
data class ChatMessage(
    val id: ByteArray,
    val direction: Direction,
    val body: String,
    val senderPeerIdHex: String,
    val sentAtWallClockMs: Long,
    val status: ChatMessageDisplayStatus,
) {
    enum class Direction { INCOMING, OUTGOING }
    override fun equals(other: Any?) = other is ChatMessage && id.contentEquals(other.id) &&
        direction == other.direction && body == other.body &&
        senderPeerIdHex == other.senderPeerIdHex &&
        sentAtWallClockMs == other.sentAtWallClockMs && status == other.status
    override fun hashCode() = id.contentHashCode()
}
```

### 1.7 `ChatEvent`

Every event carries `eventSeq`, monotonic from 0, **per client instance**
(client A's and client B's `eventSeq` sequences are both independently
zero-based and never compared to each other). Eight payload kinds:
`peerFound(ChatPeer)`, `peerUpdated(ChatPeer)`, `peerLost(idHex, reason)`,
`connectionChanged(ChatConnectionState)`,
`linkBudgetChanged(ChatLinkBudget)`, `messageReceived(ChatMessage)`,
`messageStatusChanged(messageIdHex, ChatMessageDisplayStatus)`,
`clientFailed(reason)`.

No event is emitted for a client's implicit initial state (no peers,
`disconnected(reason: nil)`) — events represent *transitions*, not the
zero state.

**DECISION (not pinned by the brief):** the brief writes `ChatEvent` as a
flat list of payload cases with "every event carries `eventSeq`" as a
blanket property, which under-specifies whether `eventSeq` is a field on a
wrapper type or a field duplicated onto every case. This document pins it
as a wrapper on Swift (a struct pairing `eventSeq` with a nested payload
enum) and as a shared abstract property realized per-subclass on Kotlin
(idiomatic per language, since Kotlin sealed-class hierarchies commonly
hoist a shared field to the base class rather than wrapping). The
*semantic* content — eventSeq plus exactly these eight payload shapes with
exactly these fields — is identical on both platforms; only which
declaration carries `eventSeq` differs.

```swift
struct ChatEvent: Equatable {
    let eventSeq: UInt64
    let kind: Kind

    enum Kind: Equatable {
        case peerFound(ChatPeer)
        case peerUpdated(ChatPeer)
        case peerLost(peerIdHex: String, reason: String)
        case connectionChanged(ChatConnectionState)
        case linkBudgetChanged(ChatLinkBudget)
        case messageReceived(ChatMessage)
        case messageStatusChanged(messageIdHex: String, status: ChatMessageDisplayStatus)
        case clientFailed(reason: String)
    }
}
```

```kotlin
sealed class ChatEvent {
    abstract val eventSeq: Long

    data class PeerFound(override val eventSeq: Long, val peer: ChatPeer) : ChatEvent()
    data class PeerUpdated(override val eventSeq: Long, val peer: ChatPeer) : ChatEvent()
    data class PeerLost(
        override val eventSeq: Long, val peerIdHex: String, val reason: String,
    ) : ChatEvent()
    data class ConnectionChanged(
        override val eventSeq: Long, val state: ChatConnectionState,
    ) : ChatEvent()
    data class LinkBudgetChanged(
        override val eventSeq: Long, val budget: ChatLinkBudget,
    ) : ChatEvent()
    data class MessageReceived(override val eventSeq: Long, val message: ChatMessage) : ChatEvent()
    data class MessageStatusChanged(
        override val eventSeq: Long,
        val messageIdHex: String,
        val status: ChatMessageDisplayStatus,
    ) : ChatEvent()
    data class ClientFailed(override val eventSeq: Long, val reason: String) : ChatEvent()
}
```

#### `eventSeq` gap-detection contract and bounded-buffer policy

The event stream is a **bounded buffer of 512 events, drop-oldest**: if a
producer emits faster than a consumer drains, the oldest buffered event is
discarded to make room, never the newest, and never by blocking the
producer indefinitely.

- Events that ARE delivered are delivered in original relative order, each
  with its true, originally assigned `eventSeq` — dropping never
  renumbers or reorders survivors.
- A consumer detects drops by watching for `eventSeq` gaps:
  `gap = currentEventSeq - previousEventSeq - 1`. `gap == 0` means no loss.
  `gap > 0` means exactly `gap` events were dropped between the two it did
  see.
- A consumer MUST NOT assume `eventSeq` is contiguous. A UI or test
  observing a gap should treat its peer/connection/link-budget/message
  state as possibly stale for whatever was dropped (this sample does not
  implement an active resync-on-gap protocol in C3-28; that is left to a
  later stage if it proves necessary).
- The 512 bound and drop-oldest policy are identical on both platforms:
  Swift `AsyncStream<ChatEvent>` constructed with
  `AsyncStream(bufferingPolicy: .bufferingNewest(512))`; Kotlin
  `Flow<ChatEvent>` backed by a `MutableSharedFlow` (or equivalent)
  configured with `extraBufferCapacity = 512` and
  `onBufferOverflow = BufferOverflow.DROP_OLDEST`.

### 1.8 `ChatTransportClient`

Swift protocol and Kotlin interface, side by side — same operations, same
ordering, same semantics:

```swift
protocol ChatTransportClient {
    func start() async throws
    func stop() async

    /// Bounded, buffering newest 512 (drop-oldest); see §1.7's gap contract.
    var events: AsyncStream<ChatEvent> { get }

    func connect(toPeer idHex: String) async throws
    func disconnect() async

    /// Returns on queue acceptance, NOT delivery — see §1.5's honest-
    /// delivery caveat. The returned string is the new message's
    /// `messageIdHex`.
    func send(body: String) async throws -> String
    func cancelSend(messageIdHex: String) async
}
```

```kotlin
interface ChatTransportClient {
    suspend fun start()
    suspend fun stop()

    /** Bounded buffer (512, drop-oldest); see §1.7's gap contract. */
    val events: Flow<ChatEvent>

    suspend fun connect(peerIdHex: String)
    suspend fun disconnect()

    /**
     * Returns on queue acceptance, NOT delivery — see §1.5's honest-
     * delivery caveat. The returned string is the new message's
     * messageIdHex.
     */
    suspend fun send(body: String): String
    suspend fun cancelSend(messageIdHex: String)
}
```

**DECISION (not pinned by the brief):** the Kotlin `connect` parameter name
(`peerIdHex`) is chosen to match the Swift argument label's *meaning*
(`toPeer idHex:`) since Kotlin has no external/internal label split; Kotlin
error signaling uses plain thrown exceptions on `suspend fun`s (Kotlin has
no `async throws` distinction) rather than a `Result`-wrapping return type,
matching this repo's existing Kotlin conventions (no `Result<T>` usage
found in `Apps/HIL/android`).

## 2. Simulated client (`SimulatedChatTransportClient`)

Deterministic, in-process, driven by `(scenarioName, seed)`. Requirements:

1. **Paired in-process.** Two `SimulatedChatTransportClient` instances (A
   and B) are constructed together and share an in-process channel. A's
   `send(body:)` produces B's `messageReceived` event and vice versa —
   there is no real network, audio, or IPC involved.
2. **Envelope bytes actually cross the codec boundary.** A's `send(body:)`
   builds a real envelope v1 byte buffer via the same encoder validated
   against `ENVELOPE.md`/`fixtures/chat-envelope-golden.json`, and B
   decodes those exact bytes via the same decoder. This is the literal
   mechanism behind C3-28's merge gate: "both platforms can exchange the
   same chat envelopes in tests without importing the live Cyrinx SDK."
   The simulated transport is a courier for envelope bytes, not a shortcut
   that skips the codec.
3. **Virtual time only.** A `VirtualClock` starts at `0` ms.
   Swift: `clock.advance(byMs:)`. Kotlin: the coroutine test dispatcher's
   virtual time (`kotlinx-coroutines-test`'s `TestCoroutineScheduler`,
   driven via `runTest { ... advanceTimeBy(...) ... }` or equivalent). No
   test anywhere sleeps on wall-clock time.
4. **Seeded PRNG: SplitMix64.** Public-domain reference algorithm
   (Vigna, `splitmix64.c`, <http://prng.di.unimi.it/splitmix64.c>; also
   the generator from Steele, Lea & Flood, "Fast Splittable Pseudorandom
   Number Generators," OOPSLA 2014). Pinned constants:

   ```
   GOLDEN_GAMMA = 0x9E3779B97F4A7C15   // per-call state increment
   MIX_MUL_1    = 0xBF58476D1CE4E5B9   // first avalanche multiplier
   MIX_MUL_2    = 0x94D049BB133111EB   // second avalanche multiplier
   ```

   Reference `next()` step, identical on both platforms:

   ```
   state = state + GOLDEN_GAMMA            // (mod 2^64, i.e. u64 wraparound)
   z = state
   z = (z ^ (z >> 30)) * MIX_MUL_1         // (mod 2^64)
   z = (z ^ (z >> 27)) * MIX_MUL_2         // (mod 2^64)
   z = z ^ (z >> 31)
   return z                                 // one u64 draw
   ```

   `state` is initialized directly from the scenario's `seed` (a `u64`),
   no additional hashing of the seed itself.

   **PRNG draw order contract.** The six scenario scripts in §3 are fully
   deterministic *timelines* — the PRNG never selects which branch of a
   scenario executes. It supplies only the byte content of generated
   identifiers, in this fixed order, at simulator construction time
   (before virtual time starts advancing):

   1. Draw 1 → 8 bytes (big-endian) of the u64 result, of which the first
      4 bytes become client A's simulated local peer ID (as later
      discovered by B).
   2. Draw 2 → same treatment, becomes client B's simulated local peer ID
      (as later discovered by A).
   3. No further scenario-independent draws. A scenario needing additional
      deterministic filler content (there are none among the six below)
      would document its own draws 3+ inline.

   This makes `(scenarioName, seed)` fully determine every ID byte
   without those bytes being hand-chosen in this document, while still
   being exactly reproducible so a Swift-generated trace and a
   Kotlin-generated trace are byte-identical (§4's guarantee).

   **Message-ID stream (pinned).** Message IDs are not drawn from the
   construction stream above, which ends at draw 2. Each client owns an
   independent message-ID `SplitMix64` seeded at construction with
   `seed XOR roleTag`, where `roleTag` is the big-endian u64 reading of
   the ASCII bytes `MSGIDA__` (`0x4D53_4749_4441_5F5F`) for client A and
   `MSGIDB__` (`0x4D53_4749_4442_5F5F`) for client B. Each `send()`
   draws two consecutive u64 values from that stream; the 16-byte
   message ID is the big-endian serialization of the first draw followed
   by the big-endian serialization of the second. This is pinned across
   Swift and Kotlin because §4's byte-identical-trace guarantee covers
   the `idHex` of every sent message appearing in a trace.

   **Behavior outside the six scenario tables (pinned).**
   `cancelSend(messageIdHex:)` cancels the message's remaining scheduled
   status transitions and emits `messageStatusChanged(failed,
   failureReason: "cancelled")`, unless the message ID is unknown or
   already terminal, in which case it is a no-op. In every §3 scenario's
   `linkBudgetChanged` events, `txLowerBoundBps` and `rxLowerBoundBps`
   are `null`: the tables pin only classification, confidence, and ageMs
   numerically.

   **Send precondition (pinned).** `send(body:)` is accepted only while
   the connection state is `connected` or `degraded`; in any other state
   it throws/raises the transport-misuse "not connected" error and emits
   no event. A send accepted while `connected` or `degraded` follows the
   happyPair delivery timeline unless a scenario table or a lifecycle
   rule below overrides it. Identical on both platforms.

   **Lifecycle cancellation (pinned).** Scheduled simulator work must
   never outlive the state that scheduled it:

   - `disconnect()` cancels every scheduled action for this client
     (handshake steps, message status transitions, link-budget events),
     then emits `messageStatusChanged(failed, failureReason:
     "disconnected")` for each nonterminal outgoing message in send
     order, then emits `connectionChanged(disconnected,
     reason: "userInitiated")`. Nothing further fires afterward; a
     repeat `disconnect()` is a no-op.
   - `stop()` performs the same cancellation, emits
     `messageStatusChanged(failed, failureReason: "stopped")` for each
     nonterminal outgoing message in send order, emits
     `connectionChanged(disconnected, reason: "stopped")` unless the
     state is already `disconnected`, and then finishes the event
     stream. No event of any kind may be observed after the stream
     finishes, and previously scheduled actions must never fire after
     `stop()`. A repeat `stop()` is a no-op.
   - When a scenario script disconnects a client (for example
     peerLoss's scripted `connectionChanged(disconnected, ...)`), every
     nonterminal outgoing message on that client transitions to
     `messageStatusChanged(failed, failureReason: "peerLost")`
     immediately after the scripted disconnect event, in send order.
     `connected`, `delivered`, or any other post-disconnect transition
     for pre-disconnect work is a contract violation.

   - **Target ownership.** Every peer-driven effect — handshake
     transition, inbound message delivery, link-budget change — is owned
     by the client it mutates, regardless of which client's call
     scheduled it. `disconnect()` and `stop()` advance the target
     client's lifecycle generation, and every effect is validated
     against its target's current generation at fire time; a stale
     effect is dropped silently. Two consequences must hold on both
     platforms: a client never observes
     `connectionChanged(connected)` after its own `disconnect()`/
     `stop()`, even when the peer's `connect()` scheduled that
     transition (passive-side handshake cancellation); and a client
     never emits `messageReceived` after its own `disconnect()`/
     `stop()`, even for a message the peer's `send()` had already
     scheduled (receiver-side inbound cancellation). The sender's own
     transfer statuses are unaffected by the receiver's disconnect —
     the simulator models no delivery-failure backchannel, and §4's
     schema-limitation note applies.
   - **Quiescence before completion.** Implementations must cancel and
     join all in-flight work before completing/closing the event
     stream, so that an emission can never race stream completion:
     every emission either lands before the completion or its producer
     was already cancelled and joined. Losing an emission to a
     close race is a contract violation, not tolerated backpressure.

   **`start()` semantics (pinned).** `start()` is idempotent: repeated
   calls change nothing and schedule nothing. Discovery is armed only
   once BOTH clients of a pair have started; the moment the second
   client starts, each client's `peerFound` is scheduled at its §3
   scenario offset relative to that moment. A stopped client cannot be
   restarted.

   Both platforms must cover these rules with connect-then-disconnect,
   send-then-stop, passive-side handshake-cancellation, and
   receiver-disconnect-with-inbound-send tests, and the reason literals
   `"disconnected"`, `"stopped"`, and `"peerLost"` are pinned exactly.

## 3. Scenario scripts

Six scenarios, `happyPair`, `peerLoss`, `degradedThenRecovered`,
`sendFailure`, `duplicateIncoming`, `slowLink`. Each table below is the
exact, pinned virtual-time timeline for that scenario — the source of
truth an implementation must reproduce, and (once generated in the verify
stage, for `happyPair` and `peerLoss` only — see `README.md`) what
`fixtures/traces/*.jsonl` checks byte-for-byte between Swift and Kotlin.

Two kinds of rows appear:

- **Driver actions** (`→` in the Client column) are inputs from the test
  harness (e.g. "call `A.connect(...)`") — they are not part of the
  emitted `ChatEvent`/trace stream themselves, only shown for causal
  clarity.
- **Events** are actual `ChatEvent`s emitted by the named client, each
  with that client's own `eventSeq` (independently zero-based per client,
  per §1.7).

Symbols used below (values are PRNG-derived per §2, not literal in this
document): `peerA` = client A's identity as discovered by B, `peerB` =
client B's identity as discovered by A. Message IDs (`msg1`, `msgDup`) are
generated by the sending client at `send()` time from the message-ID
stream pinned in §2 ("Message-ID stream (pinned)"): 128-bit, two
big-endian SplitMix64 draws from the per-role-tagged generator.

Every scenario stays at or under 15 total emitted events (both clients
combined), per the design brief's guidance.

### 3.1 `happyPair`

Clean discovery → connect → send → deliver, no faults. Demonstrates every
non-error `ChatEvent` kind except `peerLost`/`peerUpdated`/`clientFailed`.

| t (ms) | Client | eventSeq | Row |
|---:|---|---:|---|
| 0 | → | — | driver: `A.start()`, `B.start()`; simulator pairs A+B |
| 50 | A | 0 | `peerFound(peerB)` |
| 50 | B | 0 | `peerFound(peerA)` |
| 100 | → | — | driver: `A.connect(toPeer: peerB.idHex)` |
| 100 | A | 1 | `connectionChanged(connecting)` |
| 150 | A | 2 | `connectionChanged(connected)` |
| 150 | B | 1 | `connectionChanged(connected)` |
| 200 | A | 3 | `linkBudgetChanged(classification: text, confidence: 0.7, ageMs: 0)` |
| 300 | → | — | driver: `A.send(body: "hello")` → returns `msg1` |
| 300 | A | 4 | `messageStatusChanged(msg1, queued)` |
| 320 | A | 5 | `messageStatusChanged(msg1, transmitting)` |
| 380 | B | 2 | `messageReceived(msg1, direction: incoming, body: "hello")` |
| 400 | A | 6 | `messageStatusChanged(msg1, delivered)` |

**DECISION (not pinned by the brief):** B does not pass through a
`connecting` `ChatConnectionState` of its own — the passive side of a
simulated pairing transitions `disconnected → connected` directly when it
accepts A's connection, rather than modeling a symmetric handshake delay
on both sides. This keeps the passive side's timeline simple; a future
stage may add a symmetric handshake if the live SDK's session semantics
require it.

Total events: 7 (A) + 3 (B) = 10.

### 3.2 `peerLoss`

Connected pair, then B goes silent long enough to trip a timeout on both
sides.

| t (ms) | Client | eventSeq | Row |
|---:|---|---:|---|
| 0 | → | — | driver: `A.start()`, `B.start()`; simulator pairs A+B |
| 50 | A | 0 | `peerFound(peerB)` |
| 50 | B | 0 | `peerFound(peerA)` |
| 100 | → | — | driver: `A.connect(toPeer: peerB.idHex)` |
| 100 | A | 1 | `connectionChanged(connecting)` |
| 150 | A | 2 | `connectionChanged(connected)` |
| 150 | B | 1 | `connectionChanged(connected)` |
| 500 | → | — | driver: simulated silence timeout fires on both sides |
| 500 | A | 3 | `connectionChanged(disconnected, reason: "peerSilenceTimeout")` |
| 500 | B | 2 | `connectionChanged(disconnected, reason: "peerSilenceTimeout")` |
| 510 | A | 4 | `peerLost(peerB.idHex, reason: "peerSilenceTimeout")` |
| 510 | B | 3 | `peerLost(peerA.idHex, reason: "peerSilenceTimeout")` |

Total events: 5 (A) + 4 (B) = 9.

### 3.3 `degradedThenRecovered`

Connected pair; link degrades (falls back to `controlOnly`), then
recovers back to `text`. No message send — this scenario isolates
connection/link-budget behavior.

| t (ms) | Client | eventSeq | Row |
|---:|---|---:|---|
| 0 | → | — | driver: `A.start()`, `B.start()`; simulator pairs A+B |
| 50 | A | 0 | `peerFound(peerB)` |
| 50 | B | 0 | `peerFound(peerA)` |
| 100 | → | — | driver: `A.connect(toPeer: peerB.idHex)` |
| 100 | A | 1 | `connectionChanged(connecting)` |
| 150 | A | 2 | `connectionChanged(connected)` |
| 150 | B | 1 | `connectionChanged(connected)` |
| 200 | A | 3 | `linkBudgetChanged(classification: text, confidence: 0.7, ageMs: 0)` |
| 400 | A | 4 | `connectionChanged(degraded)` |
| 410 | A | 5 | `linkBudgetChanged(classification: controlOnly, confidence: 0.4, ageMs: 0)` |
| 700 | A | 6 | `connectionChanged(connected)` |
| 710 | A | 7 | `linkBudgetChanged(classification: text, confidence: 0.65, ageMs: 0)` |

Total events: 8 (A) + 2 (B) = 10.

### 3.4 `sendFailure`

Connected pair; A sends a message that the simulated transport fails
after transmission is underway (e.g. representing a route that never
acknowledges).

| t (ms) | Client | eventSeq | Row |
|---:|---|---:|---|
| 0 | → | — | driver: `A.start()`, `B.start()`; simulator pairs A+B |
| 50 | A | 0 | `peerFound(peerB)` |
| 50 | B | 0 | `peerFound(peerA)` |
| 100 | → | — | driver: `A.connect(toPeer: peerB.idHex)` |
| 100 | A | 1 | `connectionChanged(connecting)` |
| 150 | A | 2 | `connectionChanged(connected)` |
| 150 | B | 1 | `connectionChanged(connected)` |
| 300 | → | — | driver: `A.send(body: "will-fail")` → returns `msg1` |
| 300 | A | 3 | `messageStatusChanged(msg1, queued)` |
| 320 | A | 4 | `messageStatusChanged(msg1, transmitting)` |
| 450 | A | 5 | `messageStatusChanged(msg1, failed, reason: "noAcknowledgment")` |

B never receives anything (no `messageReceived` for `msg1` — the failure
is realistic, not merely "B ignored it").

Total events: 6 (A) + 2 (B) = 8.

### 3.5 `duplicateIncoming`

Connected pair; the exact same encoded envelope bytes are delivered to B
**twice** (representing a link-layer retransmission), and B must suppress
the duplicate by `messageId`, producing **exactly one**
`messageReceived`.

| t (ms) | Client | eventSeq | Row |
|---:|---|---:|---|
| 0 | → | — | driver: `A.start()`, `B.start()`; simulator pairs A+B |
| 50 | A | 0 | `peerFound(peerB)` |
| 50 | B | 0 | `peerFound(peerA)` |
| 100 | → | — | driver: `A.connect(toPeer: peerB.idHex)` |
| 100 | A | 1 | `connectionChanged(connecting)` |
| 150 | A | 2 | `connectionChanged(connected)` |
| 150 | B | 1 | `connectionChanged(connected)` |
| 300 | → | — | driver: `A.send(body: "dup-test")` → returns `msgDup`, encodes envelope bytes `E` |
| 300 | A | 3 | `messageStatusChanged(msgDup, queued)` |
| 320 | A | 4 | `messageStatusChanged(msgDup, transmitting)` |
| 330 | → | — | driver: simulated transport delivers `E` to B (first delivery) |
| 330 | B | 2 | `messageReceived(msgDup, direction: incoming, body: "dup-test")` |
| 340 | → | — | driver: simulated transport re-delivers the SAME bytes `E` to B (fault-injected retransmit) |
| 340 | — | — | **no event**: B decodes `E` again, recognizes `messageId == msgDup` as already seen, and suppresses it — this row exists in the timeline to prove the absence of a second `messageReceived`, not to record one |
| 400 | A | 5 | `messageStatusChanged(msgDup, delivered)` |

Total events: 6 (A) + 3 (B) = 9. The dedup assertion this scenario exists
to pin: **B emits exactly one `messageReceived` for `msgDup`**, not zero,
not two.

### 3.6 `slowLink`

Connected pair on a `controlOnly`-classified link from the start; a sent
message dwells in `transmitting` for an extended virtual duration before
delivery, demonstrating that `transmitting` is a real, observable
intermediate state under slow-link conditions, not a same-tick pass-
through.

| t (ms) | Client | eventSeq | Row |
|---:|---|---:|---|
| 0 | → | — | driver: `A.start()`, `B.start()`; simulator pairs A+B |
| 50 | A | 0 | `peerFound(peerB)` |
| 50 | B | 0 | `peerFound(peerA)` |
| 100 | → | — | driver: `A.connect(toPeer: peerB.idHex)` |
| 100 | A | 1 | `connectionChanged(connecting)` |
| 150 | A | 2 | `connectionChanged(connected)` |
| 150 | B | 1 | `connectionChanged(connected)` |
| 160 | A | 3 | `linkBudgetChanged(classification: controlOnly, confidence: 0.5, ageMs: 0)` |
| 300 | → | — | driver: `A.send(body: "slow")` → returns `msg1` |
| 300 | A | 4 | `messageStatusChanged(msg1, queued)` |
| 320 | A | 5 | `messageStatusChanged(msg1, transmitting)` |
| 2450 | B | 2 | `messageReceived(msg1, direction: incoming, body: "slow")` |
| 2500 | A | 6 | `messageStatusChanged(msg1, delivered)` |

`transmitting` dwells for 2180 ms of virtual time (320 → 2500) before
`delivered` — the point of this scenario is that a UI observer polling or
snapshotting state mid-scenario (e.g. at t=1000) must see `transmitting`,
not skip straight to a terminal state.

Total events: 7 (A) + 3 (B) = 10.

## 4. JSON-lines trace schema

A trace is a file of one JSON object per line, one line per emitted
`ChatEvent` (driver-action rows in §3's tables are never written to a
trace — only real events are). Same `(scenarioName, seed)` on both
platforms must produce byte-identical trace files, compared with a plain
diff — this is the actual mechanism behind "Golden traces for `happyPair`
and `peerLoss` land in `fixtures/traces/`" (generated from the Swift
implementation in the verify stage; Kotlin asserts equality against the
same files — not part of this document's deliverables, see `README.md`).

**Canonical top-level field order** (every line, no exceptions):

```
eventSeq, virtualTimeMs, client, event
```

```json
{"eventSeq": 0, "virtualTimeMs": 50, "client": "A", "event": {"type": "peerFound", "peer": {"idHex": "b1a2c3d4", "displayName": "Peer-B1A2", "discoveredAtMs": 50}}}
{"eventSeq": 1, "virtualTimeMs": 100, "client": "A", "event": {"type": "connectionChanged", "state": "connecting", "reason": null}}
{"eventSeq": 2, "virtualTimeMs": 380, "client": "B", "event": {"type": "messageReceived", "message": {"idHex": "…", "direction": "incoming", "body": "hello", "senderPeerIdHex": "…", "sentAtWallClockMs": 380, "status": "delivered"}}}
```

- `eventSeq` — that emitting client's own `eventSeq` (NOT a global
  cross-client counter — two lines from different `client` values can
  legitimately share the same `eventSeq`).
- `virtualTimeMs` — the simulator's virtual clock reading when the event
  was emitted.
- `client` — `"A"` or `"B"`.
- `event` — object with `"type"` first, then the fields below, in order,
  for that type. Where two events could share the same
  `(virtualTimeMs, ...)` (no case in the current six scenarios, but as a
  general rule), the file's line order is authoritative and must not be
  reordered by either implementation.

**Per-type `event` payload field order:**

| `type` | Fields, in order | Notes |
|---|---|---|
| `peerFound` | `peer: {idHex, displayName, discoveredAtMs}` | |
| `peerUpdated` | `peer: {idHex, displayName, discoveredAtMs}` | Same shape as `peerFound`. |
| `peerLost` | `peerIdHex, reason` | |
| `connectionChanged` | `state, reason` | `state` is one of `"disconnected"`, `"connecting"`, `"connected"`, `"degraded"`; `reason` is `null` except when `state == "disconnected"`. |
| `linkBudgetChanged` | `budget: {classification, txLowerBoundBps, rxLowerBoundBps, confidence, ageMs}` | `classification` uses the wire strings from §1.3 (`controlOnly`, `text`, `thumbnail`, `bulk`); `txLowerBoundBps`/`rxLowerBoundBps` are `null` when unset. |
| `messageReceived` | `message: {idHex, direction, body, senderPeerIdHex, sentAtWallClockMs, status}` | `direction` is `"incoming"` or `"outgoing"`; `status` is the current `ChatMessageDisplayStatus` at receipt (normally `"delivered"` from the receiver's own point of view — receiving IS the receiver's delivery). |
| `messageStatusChanged` | `messageIdHex, status, failureReason` | `status` is one of `"queued"`, `"transmitting"`, `"delivered"`, `"failed"`; `failureReason` is `null` except when `status == "failed"`. |
| `clientFailed` | `reason` | |

**Golden trace fixtures (pinned).** The committed goldens are
`fixtures/traces/happyPair.jsonl` and `fixtures/traces/peerLoss.jsonl`,
generated with **seed 1** by `CyrinxChatKit`'s `chat-trace-gen`
executable. BOTH platforms assert their own generated traces
byte-identical to these files (Kotlin's
`ChatTraceGoldenComparisonTest` and a Swift twin), and both comparisons
FAIL — never skip — when a fixture file is missing, because they are
merge-gate evidence. Regeneration is permitted only together with a
change to this document, and the diff is reviewed like source.

**Known schema limitation.** `messageReceived.message` has no field for
a failure reason alongside `status`; in every §3 scenario an incoming
message carries status `"delivered"` (reception is the receiver's own
delivery observation), so the gap is not exercised. A future
receive-side failure status requires a versioned trace-schema change
adding that field, not an in-place reinterpretation.

## 5. Accessibility identifiers and launch arguments

Constants shared verbatim (identical string values) on both platforms.
Swift: `enum ChatAccessibilityID`. Kotlin: `object ChatTestTags`.

| Constant | String value |
|---|---|
| `peerList` | `chat.peerList` |
| `peerRow` | `chat.peerRow` |
| `connectButton` | `chat.connectButton` |
| `connectionBanner` | `chat.connectionBanner` |
| `linkBudgetBadge` | `chat.linkBudgetBadge` |
| `messageList` | `chat.messageList` |
| `messageRow` | `chat.messageRow` |
| `messageStatus` | `chat.messageStatus` |
| `composerField` | `chat.composerField` |
| `sendButton` | `chat.sendButton` |
| `diagnosticsButton` | `chat.diagnosticsButton` |
| `unauthenticatedNotice` | `chat.unauthenticatedNotice` |
| `errorBanner` | `chat.errorBanner` |

Launch/instrumentation arguments:

| Argument | Type | Notes |
|---|---|---|
| `chat.scenario` | `String` | One of the six scenario names in §3. |
| `chat.seed` | `UInt64` decimal | Seed for the scenario's PRNG, §2. |
| `chat.simulated` | `Bool`, default `true` | `false` selects the live SDK adapter — not available until C3-31; default stays `true` through C3-28–C3-30. |

Apple: process launch arguments, e.g. `-chat.scenario happyPair`. Android:
instrumentation args / `Intent` extras with the identical keys (e.g.
`am instrument ... -e chat.scenario happyPair`).

## 6. Cross-references

- [`README.md`](README.md) — product scope, non-security warning,
  directory map, test commands.
- [`ENVELOPE.md`](ENVELOPE.md) — the wire format `send()`/`messageReceived`
  carry; the codec this simulator's pairing exercises (§2, point 2 above).
- [`fixtures/chat-envelope-golden.json`](fixtures/chat-envelope-golden.json) —
  the committed golden vectors validating that codec.

import CyrinxChatKit
import Foundation
import Observation

/// The Apple chat sample's UI-facing projection model. `docs/CYRINX_3_PLAN.md`
/// Phase F's "Sample architecture": "Apple ChatModel (@MainActor,
/// @Observable) ... consume[s] a small injected application protocol and
/// do[es] not call C or JNI directly. [It] own[s] display state, while
/// `CyrinxTransport` owns protocol state."
///
/// Implements, verbatim, the pinned projection rules from the C3-29/C3-30
/// design brief's "Shared ChatModel/ChatViewModel projection" section --
/// identical semantics to the Android `ChatViewModel` this model has no
/// dependency on, but must agree with byte-for-byte on every scenario
/// (CONTRACT.md §3/§4's cross-platform trace guarantee is what the C3-30
/// merge gate checks). Every place the design brief left unpinned is
/// marked **DECISION (not pinned by the brief):**, matching CONTRACT.md's
/// own convention for the identical situation.
@MainActor
@Observable
public final class ChatModel {
    // MARK: - Projection state (design brief's pinned field list)

    /// Sorted by `(discoveredAtMs ascending, idHex ascending)` -- the design
    /// brief's pinned peer ordering.
    public private(set) var peers: [ChatPeer] = []
    /// Last `ChatConnectionState` received. Initial value: `disconnected`
    /// with a `nil` reason (the design brief's pinned initial value; no
    /// event is ever emitted for a client's implicit zero state --
    /// CONTRACT.md §1.7).
    public private(set) var connection: ChatConnectionState = .disconnected(reason: nil)
    /// Last `ChatLinkBudget` received. Initial value: classification
    /// `controlOnly`, both bounds `nil`, confidence `0.0`, `ageMs` `0` --
    /// the design brief's pinned initial value.
    public private(set) var budget = ChatModel.initialBudget
    /// Append-only, in event order. Outgoing messages are appended at
    /// `send()` acceptance (see `send(_:)` below); incoming messages are
    /// appended on `messageReceived`.
    public private(set) var messages: [ChatMessage] = []
    /// Derived; see `recomputeBanner()`. Exactly one of the design brief's
    /// pinned priority-ordered cases, or `nil`.
    public private(set) var banner: String?
    /// Sticky: becomes `true` the first time any `eventSeq` gap is
    /// observed on the consumed stream, and never resets -- the design
    /// brief's pinned "surfaced as a small 'events dropped' caption, not a
    /// banner" behavior (CONTRACT.md §1.7's gap-detection contract).
    public private(set) var eventSeqGapDetected = false
    /// Count of `messageStatusChanged` events whose `messageIdHex` did not
    /// match any message this model knows about -- the design brief's
    /// pinned "unknown idHex is ignored (already-terminal races) but
    /// counted in `droppedStatusUpdates`."
    public private(set) var droppedStatusUpdates = 0
    /// Count of `messageGap` events consumed so far (CONTRACT.md §1.7/§2's
    /// "Gap surfacing (pinned)"), C3-29 sequence amendment. Never resets;
    /// mirrors the model-trace schema's own top-level `messageGaps` field
    /// (`ChatModelTraceRecorder`'s doc comment). Not a `droppedStatusUpdates`-
    /// style "ignored" counter -- every `messageGap` also produces a
    /// `messageGapNotices` entry below, so this count and that array's
    /// length always agree.
    public private(set) var messageGaps = 0
    /// One entry per consumed `messageGap` event, in consumption order --
    /// see `ChatMessageGapNotice`'s doc comment and `conversationRows`
    /// below for how these are interleaved with `messages` for display.
    public private(set) var messageGapNotices: [ChatMessageGapNotice] = []
    /// Transient error string from a `send()`/`connect()`/`disconnect()`/
    /// `cancelSend()` call that threw synchronously -- the design brief's
    /// pinned "surface as a transient composer error string, not a message
    /// row" for `send()`'s three named failure cases (`notConnected`,
    /// `terminal`, `concurrentCommand`); this model applies the same
    /// treatment uniformly to every public command's synchronous throw,
    /// not `send()` alone, since none of them should ever produce a
    /// message row. Cleared at the start of every subsequent command
    /// attempt (the design brief's C3-29 test list names "send
    /// failure/retry" explicitly).
    public private(set) var composerError: String?

    /// The design brief's pinned initial `budget` value, factored out so
    /// both the stored property's initializer and any future reset path
    /// use the identical literal.
    static let initialBudget = ChatLinkBudget(
        classification: .controlOnly,
        txLowerBoundBps: nil,
        rxLowerBoundBps: nil,
        confidence: 0.0,
        ageMs: 0
    )

    // MARK: - Optional trace recording (design brief's model-trace schema)

    /// When set, `apply(_:)` appends one canonical JSON-lines record (the
    /// design brief's pinned model-trace schema) to this recorder after
    /// every consumed `ChatEvent`. `nil` in the production app (no
    /// recording overhead); set by `chat-model-trace-gen` and by this
    /// package's own tests.
    public var traceRecorder: ChatModelTraceRecorder?

    // MARK: - Dependencies

    /// `nonisolated(unsafe)`, not plain `private let`: `ChatTransportClient`
    /// (CONTRACT.md §1.8) is a plain, non-`Sendable` protocol -- its own
    /// documentation says implementations are driven by exactly one
    /// sequential caller (`SimulatedChatTransportClient`'s doc comment:
    /// "deliberately does NOT conform to `Sendable`... driven by one
    /// sequential caller"), never concurrently. Without this annotation,
    /// Swift 6's strict concurrency checker treats a plain `let` on this
    /// `@MainActor` class as MainActor-isolated storage and rejects every
    /// `await transport.<method>(...)` call site with "sending
    /// 'self.transport' risks causing data races," since the protocol's
    /// async requirements carry no actor isolation of their own (verified
    /// empirically: `swift build` fails at exactly those five call sites
    /// without this annotation). `nonisolated(unsafe)` is the accurate
    /// annotation, not a workaround: this model IS the transport's single
    /// sequential caller by construction (every call site below is itself
    /// on `@MainActor`, so calls remain strictly sequential in practice,
    /// matching the transport's own documented contract), so no real data
    /// race is introduced -- only the compiler's inability to prove that
    /// through a non-`Sendable` existential is being asserted away.
    private nonisolated(unsafe) let transport: any ChatTransportClient
    /// Wall-clock-ms provider for `sentAtWallClockMs` on outgoing messages
    /// this model itself originates (see `send(_:)`). `ChatTransportClient`
    /// exposes no "now" accessor of its own (CONTRACT.md §1.8's protocol
    /// surface has none), and `sentAtWallClockMs` is documented "display
    /// metadata only" (CONTRACT.md §1.6) that never affects ordering or
    /// (per the design brief's model-trace schema, which carries only
    /// `idHex`/`direction`/`status` per message) trace determinism -- so
    /// real wall-clock time is the correct default, injectable so tests
    /// and `chat-model-trace-gen` can supply a fixed value instead for
    /// fully reproducible output.
    private let now: @Sendable () -> Int64

    // MARK: - Internal projection bookkeeping (not part of the pinned field list)

    private var consumeTask: Task<Void, Never>?
    private var lastEventSeq: UInt64?
    /// The peer idHex this model most recently asked `transport.connect
    /// (toPeer:)` for -- CONTRACT.md's `ChatConnectionState` carries no
    /// peer identity of its own, so this model tracks it locally to know
    /// which peer a later `peerLost` event's "if it names the connected
    /// peer" pinned test (design brief) refers to. **DECISION (not pinned
    /// by the brief):** set when `connect(toPeerIdHex:)` is called (not
    /// only once `connectionChanged(connected)` actually arrives), so a
    /// `peerLost` racing an in-flight handshake still resolves correctly;
    /// cleared whenever `connection` transitions to `disconnected` or back
    /// toward a fresh `connecting`/`connected` (stale guidance should not
    /// outlive the state that produced it).
    private var connectedPeerIdHex: String?
    /// Set once a `clientFailed` event is ever observed; never cleared.
    /// **DECISION (not pinned by the brief):** `clientFailed` signals a
    /// fatal client-level condition with no scripted recovery in any of
    /// CONTRACT.md §3's six scenarios, so this model treats it as sticky
    /// and dominant for the lifetime of the instance, matching its
    /// top-priority position in the brief's pinned banner ordering.
    private var clientFailedReason: String?
    /// Set by a `peerLost` event that names `connectedPeerIdHex`, for the
    /// defensive banner fallback described on `recomputeBanner()`. Cleared
    /// alongside `connectedPeerIdHex` on the same transitions.
    private var peerLossReason: String?
    /// This model's own next outgoing envelope `sequence`, mirroring
    /// `ChatTransportClient`'s internal counter (CONTRACT.md §2's
    /// "Outgoing sequence assignment (pinned)": "starts at 1 ... and
    /// increments by exactly 1 per accepted `send()`"). `send(body:)`
    /// (CONTRACT.md §1.8) returns only `messageIdHex`, never the assigned
    /// `sequence`, so this model cannot read the transport's own counter --
    /// it instead keeps an identical local counter, starting at `1` and
    /// incremented only on the same event this model's `send(_:)` uses to
    /// append the local outgoing `ChatMessage` row (its own successful
    /// `transport.send(body:)` return). Since `send(body:)` can only ever
    /// succeed when the transport itself is about to assign the next
    /// sequence value (both are gated on the identical "connected or
    /// degraded" precondition, CONTRACT.md §2's "Send precondition
    /// (pinned)"), and there is no reconnection-scope reset in this sample
    /// (ENVELOPE.md §6's Reconciliation cross-reference) to desync them,
    /// this local mirror and the transport's own counter march in lockstep
    /// 1:1 for the lifetime of one model/transport pair.
    private var nextOutgoingSequence: UInt64 = 1

    /// Test/tooling-only observability hook: total events `apply(_:)` has
    /// processed. Not part of the design brief's pinned field list; exists
    /// so `consumeExactly(_:)` callers (this package's tests,
    /// `chat-model-trace-gen`) can assert they consumed what they expected.
    public private(set) var processedEventCount = 0

    public init(
        transport: any ChatTransportClient,
        now: @escaping @Sendable () -> Int64 = { Int64(Date().timeIntervalSince1970 * 1_000) }
    ) {
        self.transport = transport
        self.now = now
    }

    // MARK: - Event consumption

    /// Begins consuming `transport.events` on a background `Task`, applying
    /// each event to this model's projection in order, until the stream
    /// finishes or `teardown()` cancels it. The production consumption
    /// path -- the real app calls this once, at launch, and never needs to
    /// know how many events a scenario will produce. Idempotent: a second
    /// call while already consuming is a no-op.
    ///
    /// Mutually exclusive with `consumeExactly(_:)` -- an `AsyncStream` has
    /// exactly one logical consumer; a caller must use one or the other on
    /// a given `ChatModel` instance, never both.
    public func start() {
        guard consumeTask == nil else { return }
        consumeTask = Task { [weak self] in
            guard let self else { return }
            for await event in self.transport.events {
                self.apply(event)
            }
        }
    }

    /// Consumes exactly `count` events from `transport.events`, applying
    /// each in order, then returns -- without cancelling anything or
    /// requiring the stream to finish. Building block for this package's
    /// own tests and `chat-model-trace-gen`, both of which know a
    /// scenario's exact per-client pinned event count up front
    /// (CONTRACT.md §3's six tables) and want to observe this model's
    /// state at exactly that boundary -- deliberately excluding any later
    /// `stop()`-only lifecycle-cleanup event a subsequent transport call
    /// might emit, mirroring how CyrinxChatKit's own (package-internal)
    /// `ChatScenarioRunner` detaches its trace sink before its own cleanup
    /// `stop()` calls. See `start()`'s doc comment for the mutual-exclusion
    /// note.
    public func consumeExactly(_ count: Int) async {
        var iterator = transport.events.makeAsyncIterator()
        var consumed = 0
        while consumed < count, let event = await iterator.next() {
            apply(event)
            consumed += 1
        }
    }

    /// Cancels this model's own event consumption (if `start()` was used)
    /// and asks the transport to stop. Idempotent -- a repeat call finds
    /// `consumeTask` already `nil` and `transport.stop()` is itself
    /// idempotent (CONTRACT.md §2).
    public func teardown() async {
        consumeTask?.cancel()
        consumeTask = nil
        await transport.stop()
    }

    // MARK: - Commands (thin wrappers over `ChatTransportClient`)

    /// Requests a connection to the peer named by `idHex`. Records `idHex`
    /// as this model's own "connected peer" (see `connectedPeerIdHex`)
    /// optimistically, before the call resolves, so a `peerLost` racing an
    /// in-flight handshake still names the right peer; reverts that on
    /// failure. A synchronous throw from `transport.connect(toPeer:)`
    /// surfaces as `composerError`, per the design brief's pinned
    /// send-failure treatment applied uniformly to every command.
    public func connect(toPeerIdHex idHex: String) async {
        composerError = nil
        connectedPeerIdHex = idHex.lowercased()
        peerLossReason = nil
        do {
            try await transport.connect(toPeer: idHex)
        } catch {
            connectedPeerIdHex = nil
            composerError = Self.composerErrorDescription(for: error)
        }
    }

    /// Requests disconnection. `ChatTransportClient.disconnect()` cannot
    /// throw (CONTRACT.md §1.8), so this never sets `composerError`.
    public func disconnect() async {
        composerError = nil
        await transport.disconnect()
    }

    /// Sends `body`. On acceptance (the transport returning a
    /// `messageIdHex`, CONTRACT.md §1.5/§1.8's "queue acceptance, NOT
    /// delivery"), appends a new outgoing `ChatMessage` immediately -- the
    /// design brief's pinned "Outgoing appended at send() acceptance
    /// (status queued, body as composed, idHex from send's return)." A
    /// synchronous throw (`notConnected`, `terminal`, `concurrentCommand`
    /// -- the design brief's named cases) surfaces as `composerError`
    /// instead of a message row.
    ///
    /// **DECISION (not pinned by the brief):** `senderPeerIdHex` on the
    /// appended message is the empty string -- `ChatTransportClient`
    /// exposes no accessor for "my own local peer id hex" (CONTRACT.md
    /// §1.8's protocol surface has none), and the field is never rendered
    /// for an outgoing message in this sample's UI (`direction ==
    /// .outgoing` alone identifies "my own" messages -- see
    /// `MessageRowView`), so an empty placeholder is honest (visibly "not
    /// applicable") rather than a fabricated value.
    public func send(_ body: String) async {
        composerError = nil
        do {
            let messageIdHex = try await transport.send(body: body)
            guard let messageId = Data(hexString: messageIdHex) else {
                composerError = "Send failed: transport returned an invalid message id."
                return
            }
            let message = ChatMessage(
                id: messageId,
                sequence: nextOutgoingSequence,
                direction: .outgoing,
                body: body,
                senderPeerIdHex: "",
                sentAtWallClockMs: now(),
                status: .queued
            )
            nextOutgoingSequence += 1
            appendMessage(message)
        } catch {
            composerError = Self.composerErrorDescription(for: error)
        }
    }

    /// Requests cancellation of a still-in-flight outgoing message.
    /// `ChatTransportClient.cancelSend(messageIdHex:)` cannot throw
    /// (CONTRACT.md §1.8); the resulting `messageStatusChanged(failed,
    /// reason: "cancelled")` event (CONTRACT.md §2's pinned behavior)
    /// arrives later, through the normal event-consumption path, and
    /// updates the message row in place exactly like any other status
    /// change.
    public func cancelSend(messageIdHex: String) async {
        composerError = nil
        await transport.cancelSend(messageIdHex: messageIdHex)
    }

    // MARK: - Projection (design brief's pinned event-application rules)

    /// Applies one consumed `ChatEvent` to this model's projection, per
    /// the design brief's pinned rules, then (if `traceRecorder` is set)
    /// records one model-trace line. `internal` (not `private`) so this
    /// package's own tests can drive the projection directly with
    /// hand-built `ChatEvent` values, independent of any transport --
    /// the same pattern CyrinxChatKit's own tests use via `@testable
    /// import` (Apps/Chat/CyrinxChatKit/Tests/.../TestSupport.swift).
    func apply(_ event: ChatEvent) {
        defer { processedEventCount += 1 }

        if let lastEventSeq {
            let gap = Int64(event.eventSeq) - Int64(lastEventSeq) - 1
            if gap > 0 {
                eventSeqGapDetected = true
            }
        }
        lastEventSeq = event.eventSeq

        switch event.kind {
        case .peerFound(let peer):
            upsertPeer(peer, insertIfMissing: true)
        case .peerUpdated(let peer):
            // DECISION (not pinned by the brief): the brief's own wording
            // distinguishes peerFound's "inserts/replaces" from
            // peerUpdated's plain "replaces" -- read literally, an update
            // naming a peer this model has never seen is a no-op, not an
            // insert. None of CONTRACT.md §3's six scenario scripts emit
            // `peerUpdated` at all, so this branch is currently
            // unreachable by any pinned scenario; see
            // `ChatModelProjectionTests` for both the known-peer-replace
            // and unknown-peer-no-op cases.
            upsertPeer(peer, insertIfMissing: false)
        case .peerLost(let peerIdHex, let reason):
            peers.removeAll { $0.id.hexString == peerIdHex }
            if peerIdHex == connectedPeerIdHex {
                // Design brief: "peerLost removes AND, if it names the
                // connected peer, surfaces recovery guidance (see banner
                // rules)." See `recomputeBanner()`'s doc comment for why
                // this is a defensive fallback branch in practice.
                peerLossReason = reason
            }
        case .connectionChanged(let state):
            connection = state
            switch state {
            case .disconnected:
                connectedPeerIdHex = nil
                peerLossReason = nil
            case .connecting, .connected:
                // A fresh handshake or a completed connection supersedes
                // any stale peer-loss guidance from a prior connection to
                // the same peer id.
                peerLossReason = nil
            case .degraded:
                break
            }
        case .linkBudgetChanged(let newBudget):
            budget = newBudget
        case .messageReceived(let message):
            appendMessage(message)
        case .messageGap(let fromSequence, let toSequence):
            // CONTRACT.md §1.7/§2's "Gap surfacing (pinned)" -- C3-29
            // sequence amendment. ORCHESTRATOR PINS #3: "not an error; banner
            // priority rules unchanged" -- deliberately does NOT touch
            // `banner`/`clientFailedReason`/`peerLossReason`, only the
            // dedicated counter and notice list below.
            messageGaps += 1
            messageGapNotices.append(
                ChatMessageGapNotice(
                    fromSequence: fromSequence, toSequence: toSequence,
                    precedingMessageCount: messages.count
                )
            )
        case .messageStatusChanged(let messageIdHex, let status):
            if let index = messages.firstIndex(where: { $0.id.hexString == messageIdHex }) {
                messages[index].status = status
            } else {
                // Design brief: "unknown idHex is ignored (already-terminal
                // races) but counted in droppedStatusUpdates."
                droppedStatusUpdates += 1
            }
        case .clientFailed(let reason):
            clientFailedReason = reason
        }

        recomputeBanner()
        recordTraceLineIfNeeded(eventSeq: event.eventSeq)
    }

    /// Appends `message` to `messages` -- the single append point shared by
    /// `send(_:)`'s own outgoing acceptance and `apply(_:)`'s
    /// `messageReceived` handling, so both paths stay in exact lockstep for
    /// any future bookkeeping keyed off "a message was just appended" (today:
    /// none beyond the append itself, but see `conversationRows` below,
    /// which reads `messages` post-append via `ChatMessageGapNotice
    /// .precedingMessageCount`).
    private func appendMessage(_ message: ChatMessage) {
        messages.append(message)
    }

    /// Merges `messages` and `messageGapNotices` into a single,
    /// chronologically interleaved sequence for rendering inside one
    /// scrolling list -- ORCHESTRATOR PINS #1/#3 (C3-29 sequence amendment):
    /// "the caption is a message-list row with the new accessibility tag,"
    /// positioned where the missing messages would have appeared
    /// (`ChatMessageGapNotice.precedingMessageCount`), not always trailing
    /// at the end. A computed property (not a third stored array to keep in
    /// sync) so `messages`' own in-place status mutations
    /// (`messageStatusChanged` above) are reflected here for free on next
    /// read, with no separate update path to forget.
    public var conversationRows: [ChatConversationRow] {
        var noticesByPosition: [Int: [ChatMessageGapNotice]] = [:]
        for notice in messageGapNotices {
            noticesByPosition[notice.precedingMessageCount, default: []].append(notice)
        }
        var rows: [ChatConversationRow] = []
        rows.reserveCapacity(messages.count + messageGapNotices.count)
        for (index, message) in messages.enumerated() {
            for notice in noticesByPosition[index] ?? [] {
                rows.append(.messageGapNotice(notice))
            }
            rows.append(.message(message))
        }
        for notice in noticesByPosition[messages.count] ?? [] {
            rows.append(.messageGapNotice(notice))
        }
        return rows
    }

    /// Inserts (if `insertIfMissing`) or replaces (always, when already
    /// present) `peer` by `id` equality, then re-sorts `peers` by the
    /// design brief's pinned `(discoveredAtMs ascending, idHex ascending)`
    /// order.
    private func upsertPeer(_ peer: ChatPeer, insertIfMissing: Bool) {
        if let index = peers.firstIndex(where: { $0.id == peer.id }) {
            peers[index] = peer
        } else if insertIfMissing {
            peers.append(peer)
        } else {
            return
        }
        peers.sort { lhs, rhs in
            if lhs.discoveredAtMs != rhs.discoveredAtMs {
                return lhs.discoveredAtMs < rhs.discoveredAtMs
            }
            return lhs.id.hexString < rhs.id.hexString
        }
    }

    /// Derives `banner` from current state, in the design brief's pinned
    /// priority order: `clientFailed` reason (verbatim, sticky); else
    /// `connection == .disconnected(reason: <non-nil>)` mapped through
    /// `plainLanguageReason(for:)`; else `connection == .degraded`'s fixed
    /// string; else, defensively, a `peerLost`-derived reason (see below);
    /// else `nil`.
    ///
    /// The `peerLossReason` fallback is defensive, not exercised by any of
    /// CONTRACT.md §3's six pinned scenarios: in `peerLoss` (§3.2), the
    /// scripted `connectionChanged(disconnected, reason:
    /// "peerSilenceTimeout")` always fires (and so already sets the
    /// banner via the second priority tier above) strictly before the
    /// scripted `peerLost` event that follows it, so this model's banner
    /// is already resolved by the time `peerLossReason` would ever be
    /// consulted. It exists because the design brief's pinned
    /// plain-language map explicitly includes a `"peerLost"` entry
    /// (`"Peer lost."`), and the brief separately calls out that
    /// `peerLost` naming the connected peer "surfaces recovery guidance"
    /// -- this is this model's well-defined answer for a hypothetical
    /// future scenario, or the live adapter, that emits `peerLost` before
    /// any `connectionChanged(disconnected)` for the same peer.
    private func recomputeBanner() {
        if let clientFailedReason {
            banner = clientFailedReason
            return
        }
        if case .disconnected(let reason) = connection, let reason {
            banner = Self.plainLanguageReason(for: reason)
            return
        }
        if case .degraded = connection {
            banner = "Link degraded — move devices closer"
            return
        }
        if let peerLossReason {
            banner = Self.plainLanguageReason(for: peerLossReason)
            return
        }
        banner = nil
    }

    /// The design brief's pinned plain-language reason map, plus its
    /// pinned "unknown reason -> the raw reason string verbatim" fallback.
    static func plainLanguageReason(for reason: String) -> String {
        switch reason {
        case "peerSilenceTimeout":
            return "Peer stopped responding. Move the devices closer and reconnect."
        case "userInitiated":
            return "Disconnected."
        case "stopped":
            return "Session ended."
        case "peerLost":
            return "Peer lost."
        default:
            return reason
        }
    }

    /// Maps a synchronous throw from a `ChatTransportClient` command to a
    /// plain-language composer error string. Named cases are
    /// `ChatSimulatedTransportError`'s four transport-misuse cases (the
    /// only `Error` type any of the six canonical scenarios or this
    /// package's own tests ever throw); any other `Error` (e.g. a future
    /// live-adapter-specific error type, or `ChatEnvelopeError.oversizeBody`
    /// from an over-cap compose) falls back to its own description rather
    /// than crashing or silently swallowing it.
    static func composerErrorDescription(for error: Error) -> String {
        if let transportError = error as? ChatSimulatedTransportError {
            switch transportError {
            case .notConnected:
                return "Not connected. Connect to a peer before sending."
            case .terminal:
                return "This session has ended. Reconnect to try again."
            case .concurrentCommand:
                return "Still finishing the last action. Try again in a moment."
            case .unknownPeer:
                return "Unknown peer. Choose a peer from the list."
            }
        }
        if error is ChatEnvelopeError {
            return "Message could not be sent: it is too large or malformed."
        }
        return "Something went wrong: \(String(describing: error))"
    }

    // MARK: - Model-trace recording (design brief's pinned schema)

    private func recordTraceLineIfNeeded(eventSeq: UInt64) {
        guard let traceRecorder else { return }
        traceRecorder.record(
            eventSeq: eventSeq,
            connectionWire: Self.wireString(for: connection),
            budgetClassWire: budget.classification.rawValue,
            peerIdHexes: peers.map { $0.id.hexString },
            messageEntries: messages.map {
                (
                    idHex: $0.id.hexString, sequence: $0.sequence,
                    direction: Self.wireString(for: $0.direction),
                    status: Self.wireString(for: $0.status)
                )
            },
            messageGaps: messageGaps,
            banner: banner,
            gap: eventSeqGapDetected
        )
    }

    private static func wireString(for state: ChatConnectionState) -> String {
        switch state {
        case .disconnected: return "disconnected"
        case .connecting: return "connecting"
        case .connected: return "connected"
        case .degraded: return "degraded"
        }
    }

    private static func wireString(for direction: ChatMessage.Direction) -> String {
        switch direction {
        case .incoming: return "incoming"
        case .outgoing: return "outgoing"
        }
    }

    private static func wireString(for status: ChatMessageDisplayStatus) -> String {
        switch status {
        case .queued: return "queued"
        case .transmitting: return "transmitting"
        case .delivered: return "delivered"
        case .failed: return "failed"
        }
    }
}

import Foundation

/// Deterministic, in-process, paired `ChatTransportClient`. Apps/Chat/
/// CONTRACT.md §2.
///
/// Two instances (constructed together via `makePair`) share one
/// `VirtualClock` and reference each other directly: A's `send(body:)`
/// builds a real envelope v1 byte buffer via `ChatEnvelopeCodec.encode`
/// (the same codec validated against the golden vectors) and B decodes
/// those exact bytes via `ChatEnvelopeCodec.decode` -- CONTRACT.md §2 point
/// 2's "envelope bytes actually cross the codec boundary," the literal
/// mechanism behind the C3-28 merge gate. There is no real network, audio,
/// or IPC involved (CONTRACT.md §2 point 1).
///
/// **Concurrency note:** this type is a deliberately single-threaded
/// discrete-event simulation (see `VirtualClock`'s documentation) driven by
/// one sequential caller -- a scenario runner or a test -- never by
/// concurrent tasks calling into the same pair simultaneously. It
/// deliberately does NOT conform to `Sendable` (not even `@unchecked`): it
/// holds genuinely unsynchronized mutable state (`connectionState`,
/// `outgoingMessages`, `pendingSendTokens`, ...), and an `@unchecked
/// Sendable` conformance would be a false promise that lets a future
/// caller (e.g. a `@MainActor` UI model plus a background task) share one
/// instance across concurrency domains without the compiler catching the
/// resulting data race -- the opposite of what "single sequential caller"
/// requires. Verified empirically (C3-28 review): removing the prior
/// `@unchecked Sendable` conformance compiles clean under this package's
/// default Swift 6 language mode (`swift-tools-version: 6.0`, no
/// `swiftLanguageMode` override) with zero new warnings or errors, and
/// every `swift test` case still passes -- nothing in this package or its
/// tests actually crosses an actor-isolation boundary with an instance of
/// this class, so the conformance was unused, not load-bearing.
public final class SimulatedChatTransportClient: ChatTransportClient {
    /// ASCII "MSGIDA__" -- see the message-ID generator seeding note below.
    private static let messageIdSeedTagA: UInt64 = 0x4D53_4749_4441_5F5F
    /// ASCII "MSGIDB__" -- see the message-ID generator seeding note below.
    private static let messageIdSeedTagB: UInt64 = 0x4D53_4749_4442_5F5F

    public let role: ChatClientRole
    public let scenario: ChatScenario
    public let localPeerId: Data

    let clock: VirtualClock
    /// Set (weakly, to avoid a retain cycle) by `makePair` right after
    /// construction. `weak` because the pair's two clients reference each
    /// other; whichever owns them externally (a test, `ChatScenarioRunner`)
    /// is the sole strong-reference holder.
    weak var peer: SimulatedChatTransportClient?

    /// Same-module-only hook `ChatScenarioRunner` uses to build one
    /// combined, chronologically ordered trace across both clients in a
    /// pair, without consuming (and thereby racing) each client's
    /// independent `events` `AsyncStream`. Not part of the public
    /// `ChatTransportClient` contract.
    var traceSink: ((ChatClientRole, Int64, ChatEvent) -> Void)?

    private let emitter = ChatEventEmitter()
    /// CONTRACT.md §2's "Message-ID stream (pinned)": each client owns an
    /// independent message-ID `SplitMix64`, seeded at construction with
    /// `seed XOR roleTag` (`roleTag` is the big-endian u64 reading of the
    /// ASCII bytes `MSGIDA__`/`MSGIDB__`, `messageIdSeedTagA`/`B` above) --
    /// separate from the simulator-construction PRNG stream, which
    /// CONTRACT.md §2 pins to *exactly* two draws (both consumed for peer
    /// IDs; "No further scenario-independent draws" happen on that stream
    /// across all six scenarios, so message IDs could not reuse it without
    /// contradicting that statement). Each `send()` draws two consecutive
    /// u64 values from this stream (`generateMessageId()` below); the
    /// 16-byte message ID is the big-endian serialization of the first
    /// draw followed by the second. This construction -- the seed formula,
    /// the role tags, and the two-draw big-endian encoding -- is pinned
    /// identically across Swift and Kotlin (not merely this package's own
    /// choice), so a Swift-generated and a Kotlin-generated trace agree on
    /// every sent message's `idHex` for the same `(scenarioName, seed)`
    /// (§4's byte-identical-trace guarantee).
    private var messageIdPRNG: SplitMix64

    private(set) var started = false
    private var discoveryScheduled = false
    private var finished = false
    /// Guards `disconnect()`'s own idempotency (CONTRACT.md §2's Lifecycle
    /// cancellation: "a repeat `disconnect()` is a no-op"), independent of
    /// `finished` (which guards `stop()`).
    private var disconnectedByUser = false
    /// CONTRACT.md §2's "Target ownership": bumped once by this client's
    /// own `disconnect()` and once by its own `stop()` (never by the
    /// peer's). An effect that mutates THIS client but was scheduled by a
    /// peer's call -- currently, inbound message delivery, see
    /// `scheduleDelivery(atMs:encoded:)` -- captures this value at
    /// schedule time and re-checks it at fire time, silently dropping the
    /// effect if it no longer matches. Handshake transitions and
    /// scenario-scripted post-connect steps don't need this: they're
    /// already registered directly in the correct target's own
    /// `pendingActionTokens` via `scheduleOwned`, so that target's own
    /// `cancelAllScheduledActions()` already cancels them outright before
    /// they can fire (see `scheduleOwned`'s doc comment). This counter
    /// exists specifically for effects that must, for an independent
    /// reason (see `scheduleDelivery`'s doc comment), also stay registered
    /// in someone else's token bookkeeping.
    private(set) var lifecycleGeneration = 0
    public private(set) var connectionState: ChatConnectionState = .disconnected(reason: nil)
    private(set) var discoveredPeer: ChatPeer?

    private var outgoingMessages: [String: ChatMessage] = [:]
    private var seenIncomingMessageIds: Set<String> = []
    /// Scheduled `VirtualClock` tokens for each nonterminal outgoing
    /// message's remaining status transitions, by `messageIdHex`. An entry
    /// exists **iff** that message is currently nonterminal: `emitDelivered`
    /// and `emitFailed` remove their message's entry the moment it reaches a
    /// terminal status (the C3-28 review fix for this dictionary's prior
    /// leak, where entries were never removed except via `cancelSend`), so
    /// `pendingSendTokens.keys` is exactly the set of nonterminal outgoing
    /// messages with outstanding scheduled work at any given moment.
    private var pendingSendTokens: [String: [Int]] = [:]
    /// `messageIdHex`, in the order `send()` was called -- CONTRACT.md §2's
    /// "in send order" clause for `stop()`, `disconnect()`, and a scenario's
    /// scripted disconnect (peerLoss's "failed(peerLost)" clause). Never
    /// pruned (it is this client's own small send history, bounded by
    /// however many messages it ever sent -- not the same kind of unbounded
    /// growth `pendingSendTokens` had).
    private var sendOrder: [String] = []
    /// Every `VirtualClock` token currently scheduled to affect *this*
    /// client's own state or events -- handshake steps (`becomeConnected`,
    /// `emitPeerFound`) and scenario-scripted post-connect steps
    /// (`applyPostConnect`). NOT outgoing-message tokens, which live in
    /// `pendingSendTokens` (message-send tokens are cancelled/terminalized
    /// together as one unit by `cancelSend`/`terminalizeNonterminalSends`,
    /// not individually here). Populated exclusively through
    /// `scheduleOwned(atMs:_:)`, which also removes each token the moment it
    /// fires, so this set only ever holds genuinely still-pending work.
    private var pendingActionTokens: Set<Int> = []

    init(role: ChatClientRole, scenario: ChatScenario, seed: UInt64, clock: VirtualClock, localPeerId: Data) {
        self.role = role
        self.scenario = scenario
        self.clock = clock
        self.localPeerId = localPeerId
        let tag = role == .a ? Self.messageIdSeedTagA : Self.messageIdSeedTagB
        self.messageIdPRNG = SplitMix64(seed: seed ^ tag)
    }

    /// Constructs a paired, in-process simulated transport for `scenario`
    /// and `seed`, per CONTRACT.md §2's PRNG draw-order contract: draw 1's
    /// first 4 (big-endian) bytes become A's local peer ID (as later
    /// discovered by B); draw 2's, B's (as later discovered by A). Both
    /// draws happen here, at construction time, before virtual time starts
    /// advancing -- CONTRACT.md §2's "at simulator construction time"
    /// requirement.
    public static func makePair(
        scenario: ChatScenario,
        seed: UInt64,
        clock: VirtualClock = VirtualClock()
    ) -> (a: SimulatedChatTransportClient, b: SimulatedChatTransportClient) {
        var prng = SplitMix64(seed: seed)
        let draw1 = prng.next()
        let draw2 = prng.next()
        let peerIdA = Data(splitMix64BigEndianBytes(draw1).prefix(4))
        let peerIdB = Data(splitMix64BigEndianBytes(draw2).prefix(4))

        let clientA = SimulatedChatTransportClient(
            role: .a, scenario: scenario, seed: seed, clock: clock, localPeerId: peerIdA
        )
        let clientB = SimulatedChatTransportClient(
            role: .b, scenario: scenario, seed: seed, clock: clock, localPeerId: peerIdB
        )
        clientA.peer = clientB
        clientB.peer = clientA
        return (clientA, clientB)
    }

    public var events: AsyncStream<ChatEvent> { emitter.stream }

    // MARK: - ChatTransportClient

    /// CONTRACT.md §2's "`start()` semantics (pinned)": idempotent --
    /// repeated calls change nothing and schedule nothing (`!started`
    /// guard). Discovery is armed only once BOTH clients of a pair have
    /// started (`scheduleDiscoveryIfBothStarted()`'s own `peer.started`
    /// check). A stopped client cannot be restarted -- guarded here by
    /// `!finished` too, not just `!started`, so the guarantee holds even
    /// for the edge case of `stop()` being called before `start()` ever
    /// runs (`started` would still be `false` in that case; without the
    /// `!finished` half of this guard, a later `start()` would wrongly
    /// arm discovery on an already-finished client).
    public func start() async throws {
        guard !started, !finished else { return }
        started = true
        scheduleDiscoveryIfBothStarted()
    }

    /// CONTRACT.md §2's "Lifecycle cancellation (pinned)": cancels every
    /// scheduled action for this client, terminalizes each nonterminal
    /// outgoing message as `failed(reason: "stopped")` in send order, emits
    /// `connectionChanged(disconnected, reason: "stopped")` unless already
    /// disconnected, then finishes the event stream. No event of any kind
    /// may be observed after the stream finishes, and previously scheduled
    /// actions must never fire after `stop()` -- guaranteed here because
    /// cancellation happens before any of the emissions below, and
    /// `finished` (checked first, before any of this runs) makes a repeat
    /// call a total no-op.
    public func stop() async {
        guard !finished else { return }
        finished = true
        lifecycleGeneration += 1
        cancelAllScheduledActions()
        terminalizeNonterminalSends(reason: "stopped")
        if case .disconnected = connectionState {
            // Already disconnected (e.g. via `disconnect()` or a scenario's
            // scripted disconnect) -- CONTRACT.md's "unless the state is
            // already disconnected" clause; no further connectionChanged.
        } else {
            connectionState = .disconnected(reason: "stopped")
            emit(.connectionChanged(.disconnected(reason: "stopped")))
        }
        emitter.finish()
    }

    public func connect(toPeer idHex: String) async throws {
        guard let discoveredPeer, discoveredPeer.id.hexString == idHex.lowercased() else {
            throw ChatSimulatedTransportError.unknownPeer
        }
        connectionState = .connecting
        emit(.connectionChanged(.connecting))

        // CONTRACT.md's DECISION (§3.1): the passive side (B) transitions
        // disconnected -> connected directly, with no `connecting` state of
        // its own -- so only the caller (A) emits `.connecting` above; both
        // sides get `becomeConnected()` scheduled below. Each side's token
        // is registered via `scheduleOwned` called ON that side (not
        // `clock.schedule` directly), so a later `stop()`/`disconnect()` on
        // just one side of the pair only ever cancels that side's own copy
        // of this handshake step (CONTRACT.md §2's "for this client"
        // scoping).
        let connectedAtMs = clock.nowMs + ChatSimTiming.connectHandshakeDelayMs
        scheduleOwned(atMs: connectedAtMs) { [weak self] in self?.becomeConnected() }
        if let peer {
            peer.scheduleOwned(atMs: connectedAtMs) { [weak peer] in peer?.becomeConnected() }
        }
        scheduleScenarioPostConnect(connectedAtMs: connectedAtMs)
    }

    /// CONTRACT.md §2's "Lifecycle cancellation (pinned)": cancels every
    /// scheduled action for this client, terminalizes each nonterminal
    /// outgoing message as `failed(reason: "disconnected")` in send order,
    /// then emits `connectionChanged(disconnected, reason: "userInitiated")`.
    /// A repeat call is a no-op (`disconnectedByUser`, guarded first).
    ///
    /// DECISION (not pinned by CONTRACT.md -- see its §1.2 note that the
    /// `reason` vocabulary is free text, illustrated by a small fixed set
    /// including "userInitiated"): none of the six scenarios call
    /// `disconnect()` directly (peerLoss's disconnection is autonomous, not
    /// driver-initiated), so `"userInitiated"` is this package's own choice
    /// for the one connectionChanged reason CONTRACT.md doesn't script (the
    /// `"disconnected"` *failure* reason on nonterminal sends, by contrast,
    /// is pinned exactly).
    public func disconnect() async {
        guard !disconnectedByUser else { return }
        disconnectedByUser = true
        lifecycleGeneration += 1
        cancelAllScheduledActions()
        terminalizeNonterminalSends(reason: "disconnected")
        connectionState = .disconnected(reason: "userInitiated")
        emit(.connectionChanged(.disconnected(reason: "userInitiated")))
    }

    public func send(body: String) async throws -> String {
        // CONTRACT.md §2's "Send precondition (pinned)": accepted only
        // while `connected` or `degraded`; any other state throws the
        // transport-misuse "not connected" error and emits no event.
        switch connectionState {
        case .connected, .degraded:
            break
        case .connecting, .disconnected:
            throw ChatSimulatedTransportError.notConnected
        }
        let bodyBytes = [UInt8](body.utf8)
        guard bodyBytes.count <= ChatEnvelope.maxBodyLength else {
            throw ChatEnvelopeError.oversizeBody
        }

        let messageId = generateMessageId()
        let messageIdHex = messageId.hexString
        let envelope = ChatEnvelope(messageId: messageId, replyToId: nil, senderId: localPeerId, body: body)
        // CONTRACT.md §2 point 2: real envelope bytes, through the real
        // codec -- not a shortcut that skips it.
        let encoded = try ChatEnvelopeCodec.encode(envelope)

        let sentAtMs = clock.nowMs
        let message = ChatMessage(
            id: messageId,
            direction: .outgoing,
            body: body,
            senderPeerIdHex: localPeerId.hexString,
            sentAtWallClockMs: sentAtMs,
            status: .queued
        )
        outgoingMessages[messageIdHex] = message
        sendOrder.append(messageIdHex)
        emit(.messageStatusChanged(messageIdHex: messageIdHex, status: .queued))

        scheduleSendOutcome(
            scenario.sendOutcome, messageIdHex: messageIdHex, encoded: encoded, sentAtMs: sentAtMs
        )
        return messageIdHex
    }

    /// CONTRACT.md §2's "Behavior outside the six scenario tables
    /// (pinned)": cancels the message's remaining scheduled status
    /// transitions and emits `messageStatusChanged(failed, failureReason:
    /// "cancelled")`, unless `messageIdHex` is unknown or already terminal
    /// (`.delivered` or `.failed`), in which case this is a no-op.
    public func cancelSend(messageIdHex: String) async {
        guard let message = outgoingMessages[messageIdHex] else { return }
        switch message.status {
        case .delivered, .failed:
            // Already terminal -- CONTRACT.md's no-op case. `emitDelivered`/
            // `emitFailed` remove `pendingSendTokens[messageIdHex]` the
            // moment a message reaches either terminal status, so this
            // status check (not merely "is the key still present in
            // `pendingSendTokens`") is what makes this branch reachable at
            // all -- see that dictionary's doc comment.
            return
        case .queued, .transmitting:
            break
        }
        if let tokens = pendingSendTokens.removeValue(forKey: messageIdHex) {
            for token in tokens {
                clock.cancel(token)
            }
        }
        emitFailed(messageIdHex: messageIdHex, reason: "cancelled")
    }

    // MARK: - Lifecycle cancellation (CONTRACT.md §2, shared by stop()/disconnect())

    /// Registers `action` (whose effects apply to *this* client's own state
    /// or events, e.g. `self?.becomeConnected()` -- never `peer?...`) with
    /// `clock` and remembers the token in `pendingActionTokens` so a later
    /// `stop()`/`disconnect()` **on this same client** can cancel it before
    /// it fires, and removes the token from that set the instant it does
    /// fire (keeping the set bounded to genuinely still-pending work,
    /// mirroring the `pendingSendTokens` leak fix above).
    ///
    /// Call sites that schedule an action affecting the OTHER client in the
    /// pair must call this method ON that other client (e.g.
    /// `peer.scheduleOwned(...)`), not on `self` -- see
    /// `scheduleDiscoveryIfBothStarted()` and `scheduleScenarioPostConnect
    /// (connectedAtMs:)`, each of which resolves the correct receiver
    /// first. This is what makes CONTRACT.md §2's "cancels every scheduled
    /// action for this client" scoped correctly per client even though a
    /// single `connect()` call on one side schedules work for both.
    @discardableResult
    private func scheduleOwned(atMs dueAtMs: Int64, _ action: @escaping () -> Void) -> Int {
        // `token` is captured by reference by the closure below (a `var`
        // referenced from a nested closure defined in the same scope) --
        // `clock.schedule` never invokes it synchronously, so by the time it
        // does run, `token` already holds the value assigned immediately
        // after `clock.schedule` returns.
        var token = -1
        token = clock.schedule(atMs: dueAtMs) { [weak self] in
            self?.pendingActionTokens.remove(token)
            action()
        }
        pendingActionTokens.insert(token)
        return token
    }

    /// Cancels the underlying `VirtualClock` tokens for every currently
    /// nonterminal outgoing message's remaining transitions
    /// (`pendingSendTokens`), WITHOUT touching `pendingActionTokens`.
    /// Factored out of `cancelAllScheduledActions()` so a scenario's
    /// scripted disconnect (`applyPostConnect`'s `.connectionDisconnected`
    /// case) can use it alone: that case must NOT cancel this same
    /// client's other already-scheduled scenario steps (e.g. peerLoss's
    /// own subsequent `.peerLost` postConnect step, scheduled 10ms after
    /// the disconnect step in the very same `scheduleScenarioPostConnect`
    /// call) the way `stop()`/`disconnect()` correctly do for themselves.
    /// Cancelling here (rather than relying solely on `terminalizeNon
    /// terminalSends`'s `emitFailed` calls to remove the dictionary
    /// entries) is what prevents an already-scheduled `transmitting`/
    /// `receive`/`delivered` closure from firing after this message has
    /// gone terminal -- exactly the "connected, delivered, or any other
    /// post-disconnect transition for pre-disconnect work is a contract
    /// violation" case CONTRACT.md §2 calls out.
    private func cancelPendingSendTokens() {
        for tokens in pendingSendTokens.values {
            for token in tokens {
                clock.cancel(token)
            }
        }
    }

    /// Cancels every `VirtualClock` action currently scheduled to affect
    /// this client: its own handshake/discovery/post-connect actions
    /// (`pendingActionTokens`) and its own nonterminal outgoing messages'
    /// remaining transitions (`pendingSendTokens`, via
    /// `cancelPendingSendTokens()`). Shared by `stop()` and `disconnect()`
    /// -- CONTRACT.md §2's "cancels every scheduled action for this
    /// client," identical for both. Does not itself clear or terminalize
    /// `pendingSendTokens`'s entries; callers follow this with
    /// `terminalizeNonterminalSends(reason:)`, which does both as it
    /// processes each message.
    private func cancelAllScheduledActions() {
        for token in pendingActionTokens {
            clock.cancel(token)
        }
        pendingActionTokens.removeAll()
        cancelPendingSendTokens()
    }

    /// Terminalizes every currently nonterminal outgoing message as
    /// `.failed(reason:)`, in `sendOrder` -- shared by `stop()`,
    /// `disconnect()`, and a scenario's scripted disconnect
    /// (`applyPostConnect`'s `.connectionDisconnected` case, CONTRACT.md
    /// §2's peerLoss "failed(peerLost)" clause). `pendingSendTokens.keys`
    /// is exactly the set of nonterminal outgoing messages (see that
    /// dictionary's doc comment), so filtering `sendOrder` by dictionary
    /// membership both determines *which* messages qualify and preserves
    /// send order for the ones that do; `emitFailed` removes each message's
    /// `pendingSendTokens` entry as it terminalizes it. Callers are
    /// responsible for cancelling the underlying clock tokens first (via
    /// `cancelAllScheduledActions()` or `cancelPendingSendTokens()`) --
    /// this method only emits and cleans up dictionary/status state.
    private func terminalizeNonterminalSends(reason: String) {
        for messageIdHex in sendOrder where pendingSendTokens[messageIdHex] != nil {
            emitFailed(messageIdHex: messageIdHex, reason: reason)
        }
    }

    // MARK: - Discovery

    private func scheduleDiscoveryIfBothStarted() {
        guard let peer, peer.started, !discoveryScheduled, !peer.discoveryScheduled else { return }
        discoveryScheduled = true
        peer.discoveryScheduled = true
        let dueAtMs = clock.nowMs + ChatSimTiming.peerDiscoveryDelayMs
        // A's peerFound is always scheduled (and so always fires) before
        // B's, regardless of which client's start() happened to trigger
        // this, matching every one of CONTRACT.md §3's tables (A eventSeq0
        // then B eventSeq0, both at the same virtual time). Each token is
        // registered on its own target client via `scheduleOwned` so a
        // later per-client `stop()`/`disconnect()` cancels only that
        // client's own copy.
        let (first, second): (SimulatedChatTransportClient, SimulatedChatTransportClient) =
            role == .a ? (self, peer) : (peer, self)
        first.scheduleOwned(atMs: dueAtMs) { [weak first] in first?.emitPeerFound() }
        second.scheduleOwned(atMs: dueAtMs) { [weak second] in second?.emitPeerFound() }
    }

    private func emitPeerFound() {
        guard let peer else { return }
        let chatPeer = ChatPeer(id: peer.localPeerId, discoveredAtMs: clock.nowMs)
        discoveredPeer = chatPeer
        emit(.peerFound(chatPeer))
    }

    // MARK: - Connect handshake and scenario-scripted post-connect behavior

    private func becomeConnected() {
        connectionState = .connected
        emit(.connectionChanged(.connected))
    }

    private func scheduleScenarioPostConnect(connectedAtMs: Int64) {
        for step in scenario.postConnectSteps {
            let dueAtMs = connectedAtMs + step.deltaMs
            switch step.target {
            case .a:
                let target = role == .a ? self : peer
                target?.scheduleOwned(atMs: dueAtMs) { [weak target] in
                    target?.applyPostConnect(step.template)
                }
            case .b:
                let target = role == .b ? self : peer
                target?.scheduleOwned(atMs: dueAtMs) { [weak target] in
                    target?.applyPostConnect(step.template)
                }
            case .both:
                guard let peer else { continue }
                let (first, second): (SimulatedChatTransportClient, SimulatedChatTransportClient) =
                    role == .a ? (self, peer) : (peer, self)
                first.scheduleOwned(atMs: dueAtMs) { [weak first] in
                    first?.applyPostConnect(step.template)
                }
                second.scheduleOwned(atMs: dueAtMs) { [weak second] in
                    second?.applyPostConnect(step.template)
                }
            }
        }
    }

    private func applyPostConnect(_ template: ChatPostConnectStep.EventTemplate) {
        switch template {
        case .linkBudget(let budget):
            emit(.linkBudgetChanged(budget))
        case .connectionDegraded:
            connectionState = .degraded
            emit(.connectionChanged(.degraded))
        case .connectionRecoveredConnected:
            connectionState = .connected
            emit(.connectionChanged(.connected))
        case .connectionDisconnected(let reason):
            connectionState = .disconnected(reason: reason)
            emit(.connectionChanged(.disconnected(reason: reason)))
            // CONTRACT.md §2's third Lifecycle-cancellation bullet: "When a
            // scenario script disconnects a client ... every nonterminal
            // outgoing message on that client transitions to
            // messageStatusChanged(failed, failureReason: "peerLost")
            // immediately after the scripted disconnect event, in send
            // order ... any other post-disconnect transition for
            // pre-disconnect work is a contract violation." None of the six
            // scenarios combine a scripted disconnect with an in-flight
            // send (peerLoss, the only scenario using this template, never
            // calls `send()`), so this is a no-op in canonical playback and
            // only matters for ad hoc/future use -- verified directly by
            // `SimulatedChatTransportClientTests`. `cancelPendingSendTokens`
            // runs first so an already-scheduled `transmitting`/`receive`/
            // `delivered` closure for one of these messages can never fire
            // after this point (see that method's doc comment) --
            // `cancelAllScheduledActions()` is deliberately NOT used here,
            // since it would also cancel this same client's own later
            // scenario-scripted steps (e.g. peerLoss's `.peerLost` step,
            // due 10ms after this one).
            cancelPendingSendTokens()
            terminalizeNonterminalSends(reason: "peerLost")
        case .peerLost(let reason):
            guard let peer else { return }
            emit(.peerLost(peerIdHex: peer.localPeerId.hexString, reason: reason))
        }
    }

    // MARK: - Send lifecycle

    private func scheduleSendOutcome(
        _ outcome: ChatSendOutcome, messageIdHex: String, encoded: Data, sentAtMs: Int64
    ) {
        let transmittingAtMs = sentAtMs + ChatSimTiming.sendTransmittingDelayMs
        var tokens: [Int] = []
        tokens.append(
            clock.schedule(atMs: transmittingAtMs) { [weak self] in
                self?.emitTransmitting(messageIdHex: messageIdHex)
            }
        )
        switch outcome {
        case .deliverNormally(let receiveDeltaMs, let deliveredDeltaMs):
            if let token = scheduleDelivery(atMs: transmittingAtMs + receiveDeltaMs, encoded: encoded) {
                tokens.append(token)
            }
            tokens.append(
                clock.schedule(atMs: transmittingAtMs + deliveredDeltaMs) { [weak self] in
                    self?.emitDelivered(messageIdHex: messageIdHex)
                }
            )
        case .fail(let deltaMs, let reason):
            tokens.append(
                clock.schedule(atMs: transmittingAtMs + deltaMs) { [weak self] in
                    self?.emitFailed(messageIdHex: messageIdHex, reason: reason)
                }
            )
        case .deliverWithDuplicate(let receiveDeltaMs, let redeliverDeltaMs, let deliveredDeltaMs):
            if let token = scheduleDelivery(atMs: transmittingAtMs + receiveDeltaMs, encoded: encoded) {
                tokens.append(token)
            }
            // Fault-injected retransmit: the SAME bytes, decoded again.
            // `receiveEnvelope`'s messageId dedup (CONTRACT.md's
            // `duplicateIncoming` scenario) is what suppresses this one.
            if let token = scheduleDelivery(atMs: transmittingAtMs + redeliverDeltaMs, encoded: encoded) {
                tokens.append(token)
            }
            tokens.append(
                clock.schedule(atMs: transmittingAtMs + deliveredDeltaMs) { [weak self] in
                    self?.emitDelivered(messageIdHex: messageIdHex)
                }
            )
        }
        pendingSendTokens[messageIdHex, default: []].append(contentsOf: tokens)
    }

    /// Schedules delivery of `encoded` envelope bytes to `peer` -- the
    /// message's RECEIVING client -- at `dueAtMs`. CONTRACT.md §2's
    /// "Target ownership": inbound message delivery mutates the PEER, not
    /// `self` (the sender), regardless of the fact that it was `self`'s
    /// own `send()` that scheduled it. So this effect is owned by the
    /// peer's own lifecycle: `peer.lifecycleGeneration` is captured here,
    /// at schedule time, and re-checked at fire time. If the peer has
    /// since `disconnect()`ed or `stop()`ped (bumping its generation), the
    /// delivery is silently dropped -- CONTRACT.md's "a client never
    /// emits `messageReceived` after its own `disconnect()`/`stop()`,
    /// even for a message the peer's `send()` had already scheduled
    /// (receiver-side inbound cancellation)." This is the fix for the
    /// C3-28 review finding: previously this call was scheduled directly
    /// via `clock.schedule`, with its token folded into the SENDER's own
    /// `pendingSendTokens` -- so only the sender's own cancellation could
    /// suppress it, and a receiver's `disconnect()` (which only cancels
    /// tokens in the receiver's OWN `pendingActionTokens`/
    /// `pendingSendTokens`) never touched it, letting a receiver observe
    /// `messageReceived` after its own disconnect.
    ///
    /// The returned token is still folded into the SENDER's own
    /// `pendingSendTokens` by the caller (`scheduleSendOutcome`), so
    /// `self`'s own `cancelSend`/`disconnect()`/`stop()` continues to be
    /// able to cancel this delivery outright too -- CONTRACT.md pins
    /// target ownership for `disconnect()`/`stop()`'s lifecycle
    /// cancellation specifically, not for `cancelSend`, and a sender
    /// giving up on (or disconnecting mid-) a send it originated
    /// continuing to also prevent that delivery is this package's
    /// existing, unpinned-but-unbroken behavior, verified by
    /// `SimulatedChatTransportClientTests`. The two mechanisms are
    /// independent and either firing first is sufficient to suppress the
    /// delivery: the sender cancelling the underlying `VirtualClock`
    /// token outright, or the receiver's generation check catching it at
    /// fire time.
    @discardableResult
    private func scheduleDelivery(atMs dueAtMs: Int64, encoded: Data) -> Int? {
        guard let peer else { return nil }
        let targetGenerationAtSchedule = peer.lifecycleGeneration
        return clock.schedule(atMs: dueAtMs) { [weak peer] in
            guard let peer, peer.lifecycleGeneration == targetGenerationAtSchedule else { return }
            peer.receiveEnvelope(encoded)
        }
    }

    private func emitTransmitting(messageIdHex: String) {
        outgoingMessages[messageIdHex]?.status = .transmitting
        emit(.messageStatusChanged(messageIdHex: messageIdHex, status: .transmitting))
    }

    private func emitDelivered(messageIdHex: String) {
        outgoingMessages[messageIdHex]?.status = .delivered
        // C3-28 review fix: remove this message's now-irrelevant
        // `pendingSendTokens` entry the instant it goes terminal, rather
        // than leaving it to accumulate forever -- see that dictionary's
        // doc comment. Safe here: every `ChatSendOutcome` case schedules
        // its `delivered` token to fire at or after every other token for
        // the same message (see `ChatScenario.sendOutcome`'s per-case
        // delta-ordering comments), so nothing scheduled for this message
        // is still pending when this runs.
        pendingSendTokens.removeValue(forKey: messageIdHex)
        emit(.messageStatusChanged(messageIdHex: messageIdHex, status: .delivered))
    }

    private func emitFailed(messageIdHex: String, reason: String) {
        outgoingMessages[messageIdHex]?.status = .failed(reason: reason)
        // C3-28 review fix: same leak fix as `emitDelivered` above. Callers
        // that got here via cancellation (`cancelSend`,
        // `terminalizeNonterminalSends`) have typically already removed and
        // cancelled this message's tokens themselves; `removeValue` is a
        // harmless no-op in that case.
        pendingSendTokens.removeValue(forKey: messageIdHex)
        emit(.messageStatusChanged(messageIdHex: messageIdHex, status: .failed(reason: reason)))
    }

    /// Called on the RECEIVING client with real encoded bytes produced by
    /// the peer's `send()` (CONTRACT.md §2 point 2). Decodes via the same
    /// strict codec validated against the golden vectors, applies
    /// messageId dedup (CONTRACT.md's `duplicateIncoming` scenario: exactly
    /// one `messageReceived` survives per distinct messageId, not zero, not
    /// two), and emits `messageReceived` for a new message. Malformed
    /// inbound bytes are silently dropped -- not exercised by any of the
    /// six scenarios, which only ever deliver bytes this same codec just
    /// encoded, but a real transport can't assume well-formed bytes from
    /// the wire either.
    func receiveEnvelope(_ encoded: Data) {
        guard let envelope = try? ChatEnvelopeCodec.decode(encoded) else { return }
        let messageIdHex = envelope.messageId.hexString
        guard !seenIncomingMessageIds.contains(messageIdHex) else { return }
        seenIncomingMessageIds.insert(messageIdHex)
        let message = ChatMessage(
            id: envelope.messageId,
            direction: .incoming,
            body: envelope.body,
            senderPeerIdHex: envelope.senderId.hexString,
            sentAtWallClockMs: clock.nowMs,
            status: .delivered
        )
        emit(.messageReceived(message))
    }

    // MARK: - Helpers

    @discardableResult
    private func emit(_ kind: ChatEvent.Kind) -> ChatEvent {
        let event = emitter.emit(kind)
        traceSink?(role, clock.nowMs, event)
        return event
    }

    private func generateMessageId() -> Data {
        let hi = messageIdPRNG.next()
        let lo = messageIdPRNG.next()
        var bytes = splitMix64BigEndianBytes(hi)
        bytes.append(contentsOf: splitMix64BigEndianBytes(lo))
        return Data(bytes)
    }
}

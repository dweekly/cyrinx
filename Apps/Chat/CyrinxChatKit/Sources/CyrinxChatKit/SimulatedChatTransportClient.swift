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
/// this class, so the conformance was unused, not load-bearing. (Round 5
/// deliberately introduces the one exception: see "Command ownership
/// enforcement" below -- it does not cross a COMPILER-CHECKED
/// actor-isolation boundary, since nothing here or in that round's tests
/// gained `Sendable` conformance; it instead bypasses the checker
/// entirely, on purpose, via a small `@unchecked Sendable` test-only
/// wrapper, specifically to reach the real-multi-thread scenario the new
/// guard below must defend against.)
///
/// **Command ownership enforcement (CONTRACT.md §2's pinned "Command
/// ownership (pinned)" bullet, C3-28 round-5 fix).** The "Concurrency
/// note" above describes this type's INTENDED usage -- one sequential
/// caller, never concurrent entry. Round 5 tightens that from a merely
/// documented expectation into an actively enforced one: `commandLock`
/// (a plain `NSLock`) and the boolean it guards, `commandActive`,
/// implement a per-INSTANCE, non-blocking try-lock that every one of the
/// six public commands (`start()`, `connect()`, `send()`,
/// `cancelSend(messageIdHex:)`, `disconnect()`, `stop()`) acquires via
/// `tryEnterCommandSpan()` as its very first action and releases via
/// `exitCommandSpan()` (in a `defer`, covering every return/throw path)
/// as its very last. A command that finds the span already occupied -- a
/// second public command entering while a first is still inside its own
/// span, on any thread, including while the first is genuinely suspended
/// mid-span (see `testOnlyAfterQueuedEmissionLatch` below) -- is REJECTED
/// outright, never blocked-and-retried: `start()`/`connect()`/`send()`
/// throw `ChatSimulatedTransportError.concurrentCommand` (their
/// signatures permit it); `stop()`/`disconnect()`/
/// `cancelSend(messageIdHex:)` cannot throw (`ChatTransportClient`'s
/// protocol signature), so they instead call `trapConcurrentCommandMisuse()`
/// and return having done nothing -- by default (round 6: every production
/// caller and all six canonical scenario scripts) that method itself IS
/// the pin's "a documented deterministic trap": a `preconditionFailure`,
/// not a silent no-op. A test-injectable `misuseHandler`, installed only by
/// `SimulatedChatTransportClientCommandOwnershipTests` (round 5), can
/// substitute for that trap so those tests can observe the rejection
/// without crashing the test process outright -- see
/// `trapConcurrentCommandMisuse()`'s own doc comment. Either way, a
/// rejected entrant never
/// touches any other state (`connectionState`, `outgoingMessages`,
/// `pendingSendTokens`, ...), so exactly one thread's command body is
/// ever "inside the span" mutating this client's state at a time -- the
/// pin's "instead of corrupting state." This is also why the check-then-
/// set only needs `commandLock` held for the instant of the test-and-set
/// itself (inside `tryEnterCommandSpan()`/`exitCommandSpan()`), never
/// across the whole span or any `await` inside it (holding an `NSLock`
/// across a suspension point is itself a correctness hazard -- POSIX
/// mutex semantics do not guarantee a lock taken on one thread may be
/// released on another, which an `async` function resumed on a different
/// executor thread could otherwise do): once a command has WON entry, no
/// OTHER command can be concurrently mutating anything for the lock to
/// race against, by construction, so the span's own body needs no further
/// synchronization. Message-ID draws and every other command-span
/// mutation (the pin's own examples) therefore run inside the held span
/// without needing separate synchronization of their own. Production
/// callers must still honor the "Concurrency note" above and never
/// actually rely on this guard as a supported way to share one instance
/// across threads -- it exists to fail loudly and deterministically on
/// caller misuse, not to make concurrent use safe or sanctioned.
/// `SimulatedChatTransportClientCommandOwnershipTests` (round 5) is the
/// one place in this package that deliberately violates the single-
/// caller expectation, via a small `@unchecked Sendable` test-only
/// wrapper, specifically to prove this guard traps the violation instead
/// of corrupting state.
///
/// **Atomic validation (CONTRACT.md §2's pinned "Atomic validation"
/// bullet, C3-28 terminality review):** "Generation/terminality
/// validation and the target mutation it guards are atomic with respect
/// to lifecycle invalidation: under a caller-supplied concurrent scope,
/// implementations serialize check and mutation against
/// `disconnect()`/`stop()` invalidation (per-client lock, actor, or
/// serial dispatcher), so a validated effect can never interleave with an
/// invalidation between its check and its mutation." On this platform
/// that requirement is satisfied structurally, not by adding a lock,
/// actor, or serial dispatcher: as the "Concurrency note" above
/// documents, this type (and the `VirtualClock` it schedules against) is
/// single-sequential-caller by scope -- there is no concurrent scope in
/// which a `disconnect()`/`stop()` call could run *between* a
/// check-then-mutate pair's check and its mutation, because nothing else
/// runs at all until that pair's synchronous call stack returns to the
/// one sequential caller. Concretely, every check-then-mutate pair in
/// this file -- `scheduleDelivery(atMs:encoded:)`'s
/// `peer.isTerminal`/`peer.lifecycleGeneration` check immediately
/// followed by `peer.receiveEnvelope(encoded)`, and `becomeConnected()`'s
/// `isTerminal`/`peer.isTerminal` check immediately followed by the
/// `connectionState` mutation and emission -- runs to completion
/// uninterrupted before any other call into this pair is possible, so the
/// latch-style race the "Atomic validation" bullet guards against (a
/// `disconnect()` landing between a validated effect's check and its
/// mutation) cannot occur here, not merely "is made unlikely." Round 5's
/// "Command ownership enforcement" below is what makes "single-
/// sequential-caller by scope" an ENFORCED property rather than merely an
/// assumed one -- a caller that violates it (two public commands entered
/// concurrently) has the second rejected before it can touch anything,
/// so this section's "cannot occur here" continues to hold even under
/// caller misuse, not only under well-behaved single-caller use.
///
/// **Linearized admission and registration (CONTRACT.md §2's pinned
/// "Linearized admission and registration" bullet, C3-28 round-4 fix):**
/// "The same serialization covers a public command's admission (its
/// terminality check), the registration of any work it spawns
/// (background jobs, scheduled tokens, pending-transfer bookkeeping), and
/// lifecycle invalidation: a command admitted before an invalidation
/// registers its work where the invalidation sweep will find it (or
/// completes rejection before the sweep); a command arriving after is
/// rejected. No orphan may survive the sweep -- a `disconnect()` that
/// returns has terminalized every admitted nonterminal send and cancelled
/// every admitted job, and nothing (including `linkBudgetChanged`) fires
/// afterward." This is the same single-sequential-caller property as
/// "Atomic validation" above, applied to a wider span of each call rather
/// than just one check-then-mutate pair: on this platform, a public
/// command's admission (its `!isTerminal` guard -- `start()`, `connect()`,
/// `send()`), its registration of new work (e.g. `send()`'s
/// `pendingSendTokens[messageIdHex, default: []].append(...)`,
/// `scheduleOwned(atMs:_:)`'s `pendingActionTokens.insert(token)`), and a
/// later `disconnect()`/`stop()`'s invalidation sweep
/// (`cancelAllScheduledActions()` + `terminalizeNonterminalSends(...)`)
/// are all synchronous code running on the one sequential caller -- there
/// is no `await` (and so no suspension point letting another call
/// interleave) between a public command's terminality check and its
/// registration of the work that check admits, nor between
/// `disconnect()`/`stop()`'s own terminality flip (`disconnectedByUser`/
/// `finished = true`, the first statement of substance in each) and the
/// sweep that immediately follows it in that same synchronous call. A
/// command that observes `!isTerminal` therefore always finishes
/// registering its work -- into `pendingSendTokens`, `pendingActionTokens`,
/// or the `VirtualClock`'s own `scheduled` array -- before any later call,
/// including a `disconnect()`/`stop()`, can run at all; so that later
/// call's own sweep is guaranteed to find (and cancel/terminalize) it.
/// There is no window in which a command's admission has succeeded but its
/// registration is still pending when the sweep runs -- exactly the
/// ordering "no orphan may survive the sweep" requires, and exactly why
/// nothing (including `linkBudgetChanged`) can fire after `disconnect()`
/// returns. This covers `start()`'s terminal guard and
/// `scheduleDiscoveryIfBothStarted()`/`emitPeerFound()`'s registration and
/// fire-time re-checks identically to how it already covered
/// connect/send/disconnect before round 4 -- discovery-arming is a public
/// command's admission-then-registration exactly like any other. Round
/// 5's command-ownership guard (below) is the enforcement mechanism that
/// now backstops this section's "synchronous code running on the one
/// sequential caller" premise: a caller that tries to run two public
/// commands concurrently no longer gets an undefined interleaving of
/// admission and registration -- the second command is rejected before
/// its own admission check even runs.
///
/// **Post-connect script admission (CONTRACT.md §2's pinned "Post-connect
/// script admission" bullet, C3-28 round-5 fix):** "The §3 post-connect
/// timeline ... is admitted only by a successful joint `connected`
/// emission. If the handshake is dropped -- either endpoint terminal at
/// fire time -- the remainder of the scenario script is cancelled on BOTH
/// sides: no later scripted step fires, and in particular no later
/// `connected` or `degraded` may appear on either client."
/// `scheduleScenarioPostConnect(connectedAtMs:)` registers every one of a
/// scenario's post-connect steps (`applyPostConnect(_:)`, scheduled via
/// `scheduleOwned`) UNCONDITIONALLY at `connect()` time -- before it is
/// known whether the joint handshake will actually complete, since that
/// is only resolved later, at `connectedAtMs`, by `becomeConnected()`'s
/// own "Both endpoints live" check. Per-client token cancellation alone
/// (the existing `pendingActionTokens`/`cancelAllScheduledActions()`
/// machinery) is NOT sufficient to close this gap: it only cancels a
/// step in the bookkeeping of the client whose OWN `disconnect()`/
/// `stop()` ran, but a step whose scenario target is the OTHER
/// (non-disconnecting) endpoint was registered in THAT client's own
/// bookkeeping and is untouched by a peer's disconnect. `isEstablished`
/// closes this gap directly: set to `true` exactly once, inside
/// `becomeConnected()`, at the moment THIS client's own copy of the
/// joint-connected check passes (never reset -- a scenario's own later
/// post-connect steps, e.g. `degradedThenRecovered`'s recovery mutation,
/// must remain admitted once the handshake genuinely completed, even
/// though `connectionState` itself cycles through `.degraded` and back to
/// `.connected` in between). Every `applyPostConnect(_:)` invocation
/// re-checks `isEstablished` (plus this client's own `!isTerminal`) at ITS
/// OWN fire time, before touching `template` at all -- because every
/// post-connect step's `deltaMs` is strictly positive across all six
/// scenario tables (CONTRACT.md §3.1-3.6's smallest is `slowLink`'s 10 ms),
/// `becomeConnected()`'s own scheduled fire time (`connectedAtMs`, `deltaMs`
/// 0 relative to itself) always resolves `isEstablished` -- one way or the
/// other -- strictly before any post-connect step's fire time is reached,
/// so there is no ordering ambiguity to resolve here. None of the six
/// canonical scenario scripts (`ChatScenarioRunner`) ever calls
/// `disconnect()`/`stop()` before a scenario's own `scriptEndMs`, and the
/// handshake always completes jointly in canonical playback, so
/// `isEstablished` is always `true` well before any canonical post-connect
/// step's fire time -- this fix changes nothing about the six pinned golden
/// traces (§4's byte-identical guarantee), only the previously-unfixed
/// dropped-handshake edge case a scenario script's OWN driver never
/// exercises.
///
/// **Post-admission script locality (CONTRACT.md §2's pinned "Post-admission
/// script locality" bullet, C3-28 round-6 fix):** "Once the joint `connected`
/// emission succeeds, each client's post-connect scripted steps are that
/// client's OWN local timeline: they are validated against the owning
/// client's terminality/generation only, not re-checked against the peer."
/// Round 5's `applyPostConnect(_:)` (immediately below, in this type's own
/// body) additionally re-checked `!(peer?.isTerminal ?? false)` at every
/// step's fire time -- reasonable-looking defense in depth, but wrong per
/// this pin: it let a PEER's disconnect occurring strictly AFTER this
/// client's own successful joint `connected` retroactively cancel this
/// client's already-admitted local script, even though nothing about that
/// script's remaining steps reads or mutates the peer at all (every case
/// except `.peerLost` touches only `self`). Round 6 removes that peer check
/// outright -- `applyPostConnect(_:)` now gates solely on `isEstablished`
/// and this client's OWN `!isTerminal`, exactly matching the pin's "owning
/// client's terminality/generation only." The reviewer's regression
/// (`PostConnectScriptAdmissionTests
/// .postAdmissionScriptIsOwningClientsLocalTimelineUnaffectedByLaterPeerDisconnect`):
/// `degradedThenRecovered`, joint `connected` at t=150, B disconnects at
/// t=180 (strictly after admission) -- A's own remaining script
/// (`linkBudget@200`, `degraded@400`, `linkBudget@410`,
/// `connectionRecoveredConnected@700`, `linkBudget@710`) must still fire in
/// full; B, meanwhile, emits nothing after its own disconnect (B's
/// disconnect cancels B's own scheduled work exactly as before -- this fix
/// only removes the OTHER side's now-incorrect veto over A's own admitted
/// timeline). None of the six canonical scenario scripts ever disconnects
/// one side while the other still has pending post-connect steps targeting
/// itself in a way this check could have suppressed (`peerLoss`'s scripted
/// disconnect targets `.both` and is itself the step being applied, not a
/// later one being vetoed), so this fix changes nothing about the six
/// pinned golden traces (§4's byte-identical guarantee) either.
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

    /// CONTRACT.md §2's "Command ownership (pinned)" bullet: a
    /// test-injectable override for the "documented deterministic trap" a
    /// non-throwing public command (`stop()`, `disconnect()`,
    /// `cancelSend(messageIdHex:)`) reports a rejected concurrent entry
    /// through, since its signature cannot throw. `nil` by default (every
    /// non-test caller, and all six canonical scenario scripts, never trip
    /// the guard at all, so this never matters in ordinary use) --
    /// **C3-28 round 6:** `nil` no longer means "do nothing." A `nil`
    /// handler now means `trapConcurrentCommandMisuse()` reports the
    /// rejection via `preconditionFailure` instead, so an external
    /// (non-test) consumer that violates command ownership gets a loud,
    /// deterministic crash rather than a silent no-op it has no way to
    /// detect. Setting a non-`nil` handler is how a test observes the
    /// rejection WITHOUT crashing the test process outright -- the only
    /// production-shaped use of this hook is to leave it `nil` and let the
    /// trap fire. May be invoked from a DIFFERENT thread than whichever one
    /// is currently executing this client's active command span -- that is
    /// the entire scenario this hook exists to observe -- so a caller that
    /// sets it (only `SimulatedChatTransportClientCommandOwnershipTests`,
    /// round 5) is responsible for its own thread-safe handling of whatever
    /// it does inside the closure; this class contributes nothing to make
    /// the closure itself thread-safe beyond guaranteeing the call happens
    /// after `tryEnterCommandSpan()` has already lost its race under
    /// `commandLock`, so it's never invoked concurrently WITH ITSELF for
    /// the same client (each rejection is its own, independently
    /// serialized `tryEnterCommandSpan()` call).
    var misuseHandler: ((ChatSimulatedTransportError) -> Void)?

    /// Test-only hook (round-5 command-ownership review): when non-nil,
    /// `send()` awaits this closure, passing the message's own
    /// `messageIdHex`, immediately after emitting its initial `.queued`
    /// status and before scheduling its outcome. This is the ONLY `await`
    /// suspension point anywhere across this class's six public commands
    /// -- deliberately introduced so `SimulatedChatTransportClient
    /// CommandOwnershipTests` can pause a `send()` call provably mid-span
    /// (past its message-ID draw and `.queued` emission, both already
    /// inside the held command span -- see "Command ownership
    /// enforcement" above) and drive a REAL concurrent second command
    /// against the same client instance while the first has not yet
    /// called `exitCommandSpan()`, proving the guard's span really does
    /// stay open across a suspension and not merely across synchronous
    /// code. `nil` (the default, used by every non-test caller and all
    /// six canonical scenario scripts) makes `send()` behave exactly as
    /// it did before round 5: no suspension, so nothing external can ever
    /// observe this client mid-span in ordinary use, and the six pinned
    /// golden traces (§4) are unaffected.
    var testOnlyAfterQueuedEmissionLatch: ((String) async -> Void)?

    /// CONTRACT.md §2's "Command ownership (pinned)" bullet, C3-28 round-5
    /// fix: guards `commandActive`, the per-instance latch every public
    /// command acquires (via `tryEnterCommandSpan()`) as its first action
    /// and releases (via `exitCommandSpan()`) as its last -- see "Command
    /// ownership enforcement" in this type's own doc comment for the full
    /// design rationale, including why `commandLock` itself is only ever
    /// held for the instant of a test-and-set, never across a command's
    /// whole span or any `await` inside it.
    private let commandLock = NSLock()
    /// `true` for the entire duration of whichever public command
    /// currently owns this client's command span (from its
    /// `tryEnterCommandSpan()` call to its `exitCommandSpan()` call,
    /// inclusive of any suspension in between -- see
    /// `testOnlyAfterQueuedEmissionLatch` above). Always accessed only
    /// while holding `commandLock`.
    private var commandActive = false

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
    /// CONTRACT.md §2's new pinned bullet "`disconnect()` is terminal for
    /// the client instance," C3-28 terminality review: true once this
    /// client's own `disconnect()` or `stop()` has run. Deliberately a
    /// plain boolean check over `disconnectedByUser`/`finished`, NOT a
    /// comparison against `lifecycleGeneration` -- the reviewer's P2 probe
    /// is exactly the case where a generation-equality check alone is
    /// insufficient: an effect scheduled AFTER this client already went
    /// terminal captures that already-bumped generation value at schedule
    /// time, so it would still match at fire time even though the client
    /// is terminal (see `scheduleDelivery(atMs:encoded:)`'s guard, which
    /// checks both). `connect()`/`send()` reject outright when `self` is
    /// terminal, `connect()` also rejects when its `toPeer:` TARGET is
    /// terminal, and `becomeConnected()` checks both this client and its
    /// peer at fire time ("Both endpoints live for connection
    /// establishment").
    var isTerminal: Bool { disconnectedByUser || finished }
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
    /// CONTRACT.md §2's "Post-connect script admission (pinned)" bullet,
    /// C3-28 round-5 fix: set to `true` exactly once, inside
    /// `becomeConnected()`, at the moment THIS client's own copy of the
    /// joint "both endpoints live" check passes -- i.e. exactly when a
    /// successful joint `connected` transition is about to be emitted on
    /// this client. Never reset afterward (a scenario's later post-connect
    /// steps, e.g. `degradedThenRecovered`'s recovery mutation, remain
    /// admitted once the handshake genuinely completed, even after
    /// `connectionState` itself later cycles through `.degraded`). See
    /// this type's own "Post-connect script admission" doc-comment section
    /// above for the full design rationale, including why checking this
    /// flag at each post-connect step's own fire time (rather than only
    /// once, e.g. inside `scheduleScenarioPostConnect`) is both necessary
    /// and sufficient.
    private(set) var isEstablished = false
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

    // MARK: - Command ownership (CONTRACT.md §2's "Command ownership (pinned)", C3-28 round-5)

    /// Attempts to enter this client's command span on behalf of a public
    /// command. Returns `true` when the caller now OWNS the span -- it
    /// MUST call `exitCommandSpan()` exactly once on every path out
    /// (typically via `defer`, immediately after a successful call to
    /// this method) -- or `false` when another command is already active
    /// on this same client instance, per CONTRACT.md §2's "Command
    /// ownership (pinned)" bullet: the second entrant is rejected
    /// outright, never blocked-and-retried. See this type's own "Command
    /// ownership enforcement" doc-comment section for why `commandLock`
    /// only needs to be held for the duration of this one test-and-set,
    /// not across the span it guards.
    private func tryEnterCommandSpan() -> Bool {
        commandLock.lock()
        defer { commandLock.unlock() }
        guard !commandActive else { return false }
        commandActive = true
        return true
    }

    /// Releases the command span a prior, successful `tryEnterCommandSpan()`
    /// call on this same client acquired. Every public command calls this
    /// exactly once, via `defer`, immediately after entering successfully
    /// -- covering every return and throw path out of the command's body.
    private func exitCommandSpan() {
        commandLock.lock()
        defer { commandLock.unlock() }
        commandActive = false
    }

    /// CONTRACT.md §2's "Command ownership (pinned)" bullet's "documented
    /// deterministic trap," for the three public commands whose
    /// `ChatTransportClient` protocol signature does NOT permit `throw`
    /// (`stop()`, `disconnect()`, `cancelSend(messageIdHex:)`). The three
    /// throwing commands (`start()`, `connect()`, `send()`) don't call this
    /// at all -- they `throw ChatSimulatedTransportError.concurrentCommand`
    /// directly at their own call sites, which is itself the pin's
    /// deterministic report for a signature that CAN throw.
    ///
    /// **C3-28 round-6 fix.** Before this round, a non-throwing command's
    /// rejected concurrent entry only ever reached `misuseHandler`, which
    /// defaults to `nil` -- so for every caller except this package's own
    /// `SimulatedChatTransportClientCommandOwnershipTests`, a concurrent
    /// `disconnect()`/`stop()`/`cancelSend(messageIdHex:)` silently did
    /// nothing at all, with no way for an external (non-test) consumer to
    /// ever observe the misuse. That contradicts the pin's "reject it ...
    /// instead of corrupting state" -- silence is not rejection a caller can
    /// detect. The default now (`misuseHandler == nil`, true for every
    /// production caller and all six canonical scenario scripts) is a
    /// `preconditionFailure`: deterministic and loud, exactly matching how
    /// the three throwing commands already report the identical misuse via
    /// `throw`. `misuseHandler`, when installed, remains a test-only escape
    /// hatch (kept `internal`, `nil` by default) letting
    /// `SimulatedChatTransportClientCommandOwnershipTests` (round 5) observe
    /// a rejected concurrent entry without crashing the test process
    /// outright -- it is NOT a supported production API for suppressing the
    /// trap.
    private func trapConcurrentCommandMisuse() {
        if let misuseHandler {
            misuseHandler(.concurrentCommand)
            return
        }
        preconditionFailure(
            """
            SimulatedChatTransportClient: concurrent public-command entry detected \
            (Apps/Chat/CONTRACT.md §2 "Command ownership (pinned)"). Public commands \
            (start/connect/send/cancelSend/disconnect/stop) must be invoked strictly \
            sequentially by a single owner -- never concurrently, even across an internal \
            suspension. A second command entered this client instance's command span while a \
            prior one was still active. This is a caller bug, not a recoverable runtime \
            condition; see SimulatedChatTransportClient's own "Command ownership enforcement" \
            doc comment.
            """
        )
    }

    // MARK: - ChatTransportClient

    /// CONTRACT.md §2's "`start()` semantics (pinned)": idempotent --
    /// repeated calls on an already-started, non-terminal client change
    /// nothing and schedule nothing (`!started` guard). **C3-28 round-4
    /// fix:** `start()` on a terminal client -- this client's own
    /// `disconnect()` or `stop()` already ran -- is rejected as transport
    /// misuse (throws `.terminal`) and arms nothing on either side; that
    /// terminal check runs FIRST, ahead of the `started` idempotency
    /// check, so it fires even when this client was never `started` in
    /// the first place (`disconnect()` before any `start()` call is legal
    /// -- CONTRACT.md never requires a client to have started before it
    /// can be disconnected). Previously this guard only checked
    /// `!started, !finished` -- `finished` covers `stop()` but NOT
    /// `disconnect()` (which sets the separate `disconnectedByUser` flag),
    /// so a disconnected-but-never-`stop()`ped client could wrongly
    /// re-enter here, set `started = true`, and arm discovery once its
    /// peer also started -- reviewer round-4 Q1 probe (both platforms):
    /// `A.disconnect(); A.start(); B.start()` must throw on A's `start()`
    /// and arm nothing (see `TargetOwnershipAndStartSemanticsTests
    /// .disconnectThenOwnRestartRejectedAndArmsNothing`). Discovery is
    /// armed only once BOTH clients of a pair have started AND NEITHER is
    /// terminal (`scheduleDiscoveryIfBothStarted()`'s own guard, widened
    /// in this same fix). A stopped client cannot be restarted --
    /// `isTerminal` includes `finished`, so this still holds for the edge
    /// case of `stop()` being called before `start()` ever runs.
    public func start() async throws {
        guard tryEnterCommandSpan() else {
            throw ChatSimulatedTransportError.concurrentCommand
        }
        defer { exitCommandSpan() }
        guard !isTerminal else {
            throw ChatSimulatedTransportError.terminal
        }
        guard !started else { return }
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
        guard tryEnterCommandSpan() else {
            trapConcurrentCommandMisuse()
            return
        }
        defer { exitCommandSpan() }
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
        guard tryEnterCommandSpan() else {
            throw ChatSimulatedTransportError.concurrentCommand
        }
        defer { exitCommandSpan() }
        // CONTRACT.md §2's new pinned "disconnect() is terminal for the
        // client instance" bullet: a terminal client rejects connect()
        // outright, before touching any state -- the only call still
        // permitted on it is stop(). Checked first, ahead of the
        // `discoveredPeer` validation below, since this is about the
        // CALLER's own validity, independent of what it's trying to do.
        guard !isTerminal else {
            throw ChatSimulatedTransportError.terminal
        }
        guard let discoveredPeer, discoveredPeer.id.hexString == idHex.lowercased() else {
            throw ChatSimulatedTransportError.unknownPeer
        }
        // CONTRACT.md §2's new pinned bullet, continued: "a peer's
        // connect() targeting a terminal client is rejected (thrown to
        // the caller) and schedules nothing on either side." Checked
        // before any state mutation/emission below (mirroring how
        // `unknownPeer` above also rejects before touching state) so a
        // rejected connect() leaves BOTH clients exactly as it found
        // them -- reviewer probe P1 (disconnect-then-peer-connect).
        guard let peer, !peer.isTerminal else {
            throw ChatSimulatedTransportError.terminal
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
        // `peer` here is the non-optional local bound by this method's own
        // `guard let peer, !peer.isTerminal else { throw ... }` above (not
        // `self.peer` again) -- already known live, so no further optional
        // unwrap is needed before scheduling its copy of the handshake step.
        let connectedAtMs = clock.nowMs + ChatSimTiming.connectHandshakeDelayMs
        scheduleOwned(atMs: connectedAtMs) { [weak self] in self?.becomeConnected() }
        peer.scheduleOwned(atMs: connectedAtMs) { [weak peer] in peer?.becomeConnected() }
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
        guard tryEnterCommandSpan() else {
            trapConcurrentCommandMisuse()
            return
        }
        defer { exitCommandSpan() }
        guard !disconnectedByUser else { return }
        disconnectedByUser = true
        lifecycleGeneration += 1
        cancelAllScheduledActions()
        terminalizeNonterminalSends(reason: "disconnected")
        connectionState = .disconnected(reason: "userInitiated")
        emit(.connectionChanged(.disconnected(reason: "userInitiated")))
    }

    public func send(body: String) async throws -> String {
        guard tryEnterCommandSpan() else {
            throw ChatSimulatedTransportError.concurrentCommand
        }
        defer { exitCommandSpan() }
        // CONTRACT.md §2's new pinned "disconnect() is terminal for the
        // client instance" bullet: a terminal client rejects send()
        // outright. This is guaranteed equivalent to (but more direct
        // than) the `connectionState` check just below in every reachable
        // state -- a terminal client's `connectionState` is always
        // `.disconnected`, since `disconnect()`/`stop()` set it there and
        // `becomeConnected()`'s "Both endpoints live" check (below) is
        // the only other writer of `.connected` and itself refuses to run
        // for a terminal client -- but stating it explicitly here matches
        // CONTRACT.md's wording directly rather than leaning on that
        // invariant implicitly.
        guard !isTerminal else {
            throw ChatSimulatedTransportError.terminal
        }
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

        // Test-only suspension point -- see `testOnlyAfterQueuedEmissionLatch`'s
        // doc comment. `nil` (every non-test caller, all six canonical
        // scenario scripts) makes this an immediate no-op with no actual
        // suspension.
        await testOnlyAfterQueuedEmissionLatch?(messageIdHex)

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
        guard tryEnterCommandSpan() else {
            trapConcurrentCommandMisuse()
            return
        }
        defer { exitCommandSpan() }
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

    /// CONTRACT.md §2's "`start()` semantics (pinned)", C3-28 round-4 fix:
    /// "Discovery is armed only once BOTH clients of a pair have started
    /// AND neither is terminal." The `!isTerminal, !peer.isTerminal` half
    /// of this guard is new in round 4. `start()`'s own terminal check
    /// (above) covers `self` being terminal at the moment ITS `start()`
    /// call runs, but not the case where `self` started successfully
    /// while non-terminal and only became terminal afterward, with the
    /// PEER's later `start()` the one that tries to arm discovery (e.g.
    /// `A.start(); A.disconnect(); B.start()`): without this explicit
    /// check, `peer.started` alone would still read `true` and discovery
    /// would wrongly arm even though `peer` (A) is already gone.
    /// Symmetric for `peer` being the one that went terminal after
    /// starting.
    private func scheduleDiscoveryIfBothStarted() {
        guard let peer,
            !isTerminal, !peer.isTerminal,
            peer.started, !discoveryScheduled, !peer.discoveryScheduled
        else { return }
        discoveryScheduled = true
        peer.discoveryScheduled = true
        let dueAtMs = clock.nowMs + ChatSimTiming.peerDiscoveryDelayMs
        // A's peerFound is always scheduled (and so always fires) before
        // B's, regardless of which client's start() happened to trigger
        // this, matching every one of CONTRACT.md §3's tables (A eventSeq0
        // then B eventSeq0, both at the same virtual time). Each token is
        // registered on its own target client via `scheduleOwned` so a
        // later per-client `stop()`/`disconnect()` cancels only that
        // client's own copy -- `emitPeerFound()`'s own fire-time guard
        // (below) additionally covers the OTHER side going terminal after
        // this point, which per-client token cancellation alone cannot
        // catch.
        let (first, second): (SimulatedChatTransportClient, SimulatedChatTransportClient) =
            role == .a ? (self, peer) : (peer, self)
        first.scheduleOwned(atMs: dueAtMs) { [weak first] in first?.emitPeerFound() }
        second.scheduleOwned(atMs: dueAtMs) { [weak second] in second?.emitPeerFound() }
    }

    /// CONTRACT.md §2's "`start()` semantics (pinned)", C3-28 round-4 fix:
    /// "a scheduled `peerFound` is dropped at fire time if either endpoint
    /// has become terminal." Mirrors `becomeConnected()`'s own two-sided
    /// fire-time guard directly below it in this file, for the identical
    /// structural reason: this closure is registered in its OWNING
    /// client's own `pendingActionTokens` (via `scheduleOwned`), so that
    /// client's own `disconnect()`/`stop()` already cancels its own copy
    /// outright before it can fire -- but the PEER going terminal in the
    /// interim does NOT touch this client's token bookkeeping at all, so
    /// without this explicit `!peer.isTerminal` check, a client could
    /// still emit `peerFound` describing a peer that is already gone by
    /// fire time. (`!isTerminal` here is defense in depth for the
    /// self-terminality case cancellation already prevents -- provably
    /// always true whenever this closure actually runs, but stated
    /// explicitly to match `becomeConnected()`'s symmetry and CONTRACT.md's
    /// "either endpoint" wording directly.)
    private func emitPeerFound() {
        guard !isTerminal, let peer, !peer.isTerminal else { return }
        let chatPeer = ChatPeer(id: peer.localPeerId, discoveredAtMs: clock.nowMs)
        discoveredPeer = chatPeer
        emit(.peerFound(chatPeer))
    }

    // MARK: - Connect handshake and scenario-scripted post-connect behavior

    /// CONTRACT.md §2's new pinned "Both endpoints live for connection
    /// establishment" bullet (C3-28 terminality review): "A
    /// connectionChanged(connected) transition fires only if BOTH
    /// endpoints of the pair are still non-terminal at fire time: either
    /// endpoint's disconnect()/stop() before the transition fires drops
    /// the transition on both sides."
    ///
    /// `connect()` schedules one copy of this method per side, each
    /// registered via `scheduleOwned` in that side's OWN
    /// `pendingActionTokens` -- so if THIS client itself goes terminal
    /// before its own copy's fire time, `cancelAllScheduledActions()`
    /// already removes that copy from the `VirtualClock` outright and it
    /// never runs at all (`!isTerminal` below is checked anyway, for
    /// directness rather than leaning on that as an invisible invariant).
    /// The case that check-by-cancellation can NOT catch is the PEER
    /// going terminal: the peer's own `disconnect()`/`stop()` only
    /// cancels tokens in the PEER's own bookkeeping, never this client's,
    /// so without the explicit `peer.isTerminal` check here, this
    /// client's own copy of the handshake step would still fire on
    /// schedule even though the far end already disconnected
    /// mid-handshake -- reviewer probe P4 (active-side disconnect mid-
    /// handshake: neither side ever emits `connected`).
    private func becomeConnected() {
        guard !isTerminal, let peer, !peer.isTerminal else { return }
        connectionState = .connected
        // CONTRACT.md §2's "Post-connect script admission (pinned)" bullet,
        // C3-28 round-5 fix: this IS "a successful joint connected
        // emission" for this client -- the guard above just confirmed both
        // endpoints are live, so latch `isEstablished` here, before the
        // emission below, so every post-connect step's own fire-time check
        // (`applyPostConnect(_:)`) sees it admitted from this point on. See
        // this type's own "Post-connect script admission" doc-comment
        // section for why this can never be observed still `false` by a
        // post-connect step that SHOULD be admitted (every step's `deltaMs`
        // is strictly positive, so this always resolves first).
        isEstablished = true
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

    /// CONTRACT.md §2's "Post-connect script admission (pinned)" bullet,
    /// C3-28 round-5 fix: `scheduleScenarioPostConnect(connectedAtMs:)`
    /// registers every one of these unconditionally at `connect()` time,
    /// before it is known whether the joint handshake will actually
    /// complete -- so THIS method, running at each step's own fire time,
    /// is where admission is actually decided: `isEstablished` (latched by
    /// `becomeConnected()` the moment this client's own joint-connected
    /// check passed) plus this client's OWN `!isTerminal` -- re-checked
    /// here rather than assumed from admission time, matching every other
    /// post-connect effect's own fire-time re-validation in this file (see
    /// the round-6 addendum below for why `peer`'s terminality is
    /// deliberately NOT part of this check). A dropped handshake --
    /// `isEstablished` still `false` when this fires
    /// -- silently drops every step for this client, exactly matching
    /// CONTRACT.md's "the remainder of the scenario script is cancelled
    /// on BOTH sides" (each side's own copy of each step independently
    /// makes this same check on itself). See this type's own "Post-connect
    /// script admission" doc-comment section for the full design
    /// rationale.
    ///
    /// **C3-28 round 6:** gates solely on `isEstablished` and THIS client's
    /// OWN `!isTerminal` -- CONTRACT.md §2's "Post-admission script
    /// locality" bullet (see this type's own doc-comment section of that
    /// name, above): once admitted, a client's post-connect script is its
    /// own local timeline, validated against its own terminality/generation
    /// only, never re-checked against `peer`. Round 5 additionally guarded
    /// `!(peer?.isTerminal ?? false)` here; removed in round 6 because it
    /// let a peer's disconnect occurring AFTER this client's own successful
    /// joint `connected` retroactively cancel steps that don't even touch
    /// the peer (every case below except `.peerLost` reads/writes only
    /// `self`). Only `.peerLost` actually needs a live `peer` to read
    /// (`peer.localPeerId`), so it keeps its own explicit `guard let peer`
    /// below -- a peer that a caller no longer holds a strong reference to
    /// (ARC deallocation of the `weak peer` link, a Swift-specific
    /// memory-lifetime artifact, not a CONTRACT.md "terminal" client) simply
    /// makes that one case a no-op, exactly as before round 5.
    private func applyPostConnect(_ template: ChatPostConnectStep.EventTemplate) {
        guard isEstablished, !isTerminal else { return }
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
            // Unlike the other cases, this one actually needs to read
            // `peer`'s own state (`localPeerId`) -- the top-level guard
            // above deliberately does NOT bind a non-optional `peer` (see
            // its doc comment), so this case keeps its own explicit
            // unwrap, exactly as it did before round 5.
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
    /// token outright, or the receiver's terminality/generation check
    /// catching it at fire time.
    ///
    /// **Terminality, not generation equality alone, is the gate (C3-28
    /// terminality review, reviewer probe P2).** CONTRACT.md §2's
    /// "disconnect() is terminal for the client instance" bullet: "No
    /// peer-driven effect may target a terminal client REGARDLESS of when
    /// the effect was scheduled -- a terminal target drops the effect
    /// even if the effect captured the target's post-disconnect
    /// generation, so terminality, not generation equality alone, is the
    /// gate." Concretely: if `peer` is already terminal at the moment
    /// THIS method runs (e.g. the sender calls `send()` after the
    /// receiver already `disconnect()`d), `targetGenerationAtSchedule`
    /// below captures the peer's already-bumped, POST-disconnect
    /// generation -- which never changes again, since a client's
    /// generation only bumps on its OWN `disconnect()`/`stop()`, and this
    /// peer already used its one such bump. A generation-equality check
    /// alone would therefore still match at fire time and incorrectly
    /// deliver. The explicit `!peer.isTerminal` check below is what
    /// actually catches this case; the generation check remains
    /// alongside it per CONTRACT.md's "in addition to generation"
    /// phrasing, catching the (currently only theoretical, since nothing
    /// else bumps generation) case of a target that went terminal and
    /// somehow became non-terminal again with a new generation.
    @discardableResult
    private func scheduleDelivery(atMs dueAtMs: Int64, encoded: Data) -> Int? {
        guard let peer else { return nil }
        let targetGenerationAtSchedule = peer.lifecycleGeneration
        return clock.schedule(atMs: dueAtMs) { [weak peer] in
            guard let peer,
                !peer.isTerminal,
                peer.lifecycleGeneration == targetGenerationAtSchedule
            else { return }
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

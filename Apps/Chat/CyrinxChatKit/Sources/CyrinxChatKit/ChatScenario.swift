/// The six pinned scenario scripts. Apps/Chat/CONTRACT.md §3.
public enum ChatScenario: String, CaseIterable, Equatable, Sendable {
    case happyPair
    case peerLoss
    case degradedThenRecovered
    case sendFailure
    case duplicateIncoming
    case slowLink
}

/// Which side of a simulated pair a client is. CONTRACT.md §4's trace
/// schema uses exactly these two string values (`"A"` / `"B"`) for the
/// per-line `client` field.
public enum ChatClientRole: String, Equatable, Sendable {
    case a = "A"
    case b = "B"
}

/// Virtual-time deltas that are identical across all six scenario tables in
/// CONTRACT.md §3 (compare §3.1 through §3.6 row by row): peer discovery
/// fires 50 ms after both clients have started; a connect handshake takes
/// 50 ms from `connecting` to `connected`; a queued send transitions to
/// `transmitting` 20 ms later. Pinned once here rather than duplicated in
/// every scenario's plan below.
enum ChatSimTiming {
    /// CONTRACT.md §3.1-3.6: every table has `peerFound` at t=50 for both
    /// clients, 50 ms after the `t=0` `start()` driver actions.
    static let peerDiscoveryDelayMs: Int64 = 50
    /// CONTRACT.md §3.1-3.6: every table has `connectionChanged(connected)`
    /// 50 ms after `connectionChanged(connecting)` (e.g. §3.1: connecting@100,
    /// connected@150).
    static let connectHandshakeDelayMs: Int64 = 50
    /// CONTRACT.md §3.1, §3.4, §3.5, §3.6: every send transitions
    /// `queued` -> `transmitting` 20 ms later (e.g. §3.1: queued@300,
    /// transmitting@320).
    static let sendTransmittingDelayMs: Int64 = 20
}

/// A scenario-specific event that fires automatically once `connected` has
/// fired on both sides (CONTRACT.md §3's post-connect rows: `linkBudgetChanged`,
/// `degraded`/recovered `connectionChanged`, the `peerLoss` timeout pair).
/// `deltaMs` is relative to the moment `connected` fires (not to the
/// `connect()` call itself), matching how each table's post-`connected` rows
/// are naturally expressed once `connectHandshakeDelayMs` is subtracted out.
struct ChatPostConnectStep {
    enum Target {
        case a
        case b
        case both
    }

    enum EventTemplate {
        case linkBudget(ChatLinkBudget)
        case connectionDegraded
        case connectionRecoveredConnected
        case connectionDisconnected(reason: String)
        /// Resolved against the firing client's own `peer` at fire time
        /// (peerIdHex isn't known until the pair's PRNG draws happen).
        case peerLost(reason: String)
    }

    let deltaMs: Int64
    let target: Target
    let template: EventTemplate
}

/// Describes how a scenario's (sole, in all six tables) `send()` call
/// resolves. All deltas are relative to the moment `transmitting` fires
/// (`queued` time + `ChatSimTiming.sendTransmittingDelayMs`), matching how
/// each table's post-`transmitting` rows are naturally expressed.
enum ChatSendOutcome {
    /// The receiving peer decodes the envelope once and the sender sees
    /// `delivered`. Used by `happyPair` (deltas 60/80) and `slowLink`
    /// (deltas 2130/2180 -- the same shape, just much slower).
    case deliverNormally(receiveDeltaMs: Int64, deliveredDeltaMs: Int64)
    /// The sender sees `failed(reason:)`; the peer never receives anything.
    /// Used by `sendFailure`.
    case fail(deltaMs: Int64, reason: String)
    /// The receiving peer decodes the SAME envelope bytes twice
    /// (link-layer-retransmit fault injection) and must suppress the
    /// second by `messageId`, emitting exactly one `messageReceived`; the
    /// sender still sees a single `delivered`. Used by `duplicateIncoming`.
    case deliverWithDuplicate(receiveDeltaMs: Int64, redeliverDeltaMs: Int64, deliveredDeltaMs: Int64)
}

extension ChatScenario {
    /// Scenario-specific behavior scheduled once `connected` fires on both
    /// sides. CONTRACT.md §3's per-scenario tables, transcribed as data.
    var postConnectSteps: [ChatPostConnectStep] {
        switch self {
        case .happyPair:
            // §3.1: linkBudgetChanged(text, 0.7)@200 = connected(150)+50, A only.
            return [
                ChatPostConnectStep(
                    deltaMs: 50,
                    target: .a,
                    template: .linkBudget(
                        ChatLinkBudget(
                            classification: .text,
                            txLowerBoundBps: nil,
                            rxLowerBoundBps: nil,
                            confidence: 0.7,
                            ageMs: 0
                        )
                    )
                )
            ]
        case .peerLoss:
            // §3.2: disconnected(peerSilenceTimeout)@500 = connected(150)+350,
            // both; peerLost@510 = connected(150)+360, both.
            return [
                ChatPostConnectStep(
                    deltaMs: 350,
                    target: .both,
                    template: .connectionDisconnected(reason: "peerSilenceTimeout")
                ),
                ChatPostConnectStep(
                    deltaMs: 360,
                    target: .both,
                    template: .peerLost(reason: "peerSilenceTimeout")
                ),
            ]
        case .degradedThenRecovered:
            // §3.3: linkBudget(text,0.7)@200(+50), degraded@400(+250),
            // linkBudget(controlOnly,0.4)@410(+260), connected@700(+550),
            // linkBudget(text,0.65)@710(+560). A only throughout.
            return [
                ChatPostConnectStep(
                    deltaMs: 50,
                    target: .a,
                    template: .linkBudget(
                        ChatLinkBudget(
                            classification: .text,
                            txLowerBoundBps: nil,
                            rxLowerBoundBps: nil,
                            confidence: 0.7,
                            ageMs: 0
                        )
                    )
                ),
                ChatPostConnectStep(deltaMs: 250, target: .a, template: .connectionDegraded),
                ChatPostConnectStep(
                    deltaMs: 260,
                    target: .a,
                    template: .linkBudget(
                        ChatLinkBudget(
                            classification: .controlOnly,
                            txLowerBoundBps: nil,
                            rxLowerBoundBps: nil,
                            confidence: 0.4,
                            ageMs: 0
                        )
                    )
                ),
                ChatPostConnectStep(deltaMs: 550, target: .a, template: .connectionRecoveredConnected),
                ChatPostConnectStep(
                    deltaMs: 560,
                    target: .a,
                    template: .linkBudget(
                        ChatLinkBudget(
                            classification: .text,
                            txLowerBoundBps: nil,
                            rxLowerBoundBps: nil,
                            confidence: 0.65,
                            ageMs: 0
                        )
                    )
                ),
            ]
        case .sendFailure, .duplicateIncoming:
            // §3.4, §3.5: no linkBudgetChanged or autonomous connection event
            // appears between `connected` and the send sequence.
            return []
        case .slowLink:
            // §3.6: linkBudget(controlOnly,0.5)@160 = connected(150)+10, A only.
            return [
                ChatPostConnectStep(
                    deltaMs: 10,
                    target: .a,
                    template: .linkBudget(
                        ChatLinkBudget(
                            classification: .controlOnly,
                            txLowerBoundBps: nil,
                            rxLowerBoundBps: nil,
                            confidence: 0.5,
                            ageMs: 0
                        )
                    )
                )
            ]
        }
    }

    /// How this scenario's send resolves. CONTRACT.md §3's per-scenario
    /// send sequences, transcribed as data.
    ///
    /// DECISION (not pinned by CONTRACT.md): `peerLoss` and
    /// `degradedThenRecovered` never call `send()` in their pinned tables
    /// (§3.2, §3.3 isolate connection/peer behavior with no message
    /// exchange), so this property is unused for them in scenario-script
    /// playback. It still returns a sane, `happyPair`-shaped default so
    /// `send()` remains usable outside the six canned scenarios (ad hoc/
    /// unit tests constructing a pair with one of these two scenario names
    /// and calling `send()` anyway get ordinary successful delivery rather
    /// than a crash or an arbitrary choice).
    var sendOutcome: ChatSendOutcome {
        switch self {
        case .happyPair, .peerLoss, .degradedThenRecovered:
            // §3.1: B receives@380 (=transmitting(320)+60), A delivered@400 (+80).
            return .deliverNormally(receiveDeltaMs: 60, deliveredDeltaMs: 80)
        case .sendFailure:
            // §3.4: A failed@450 (=transmitting(320)+130), reason noAcknowledgment.
            return .fail(deltaMs: 130, reason: "noAcknowledgment")
        case .duplicateIncoming:
            // §3.5: B receives@330(+10), redeliver@340(+20, suppressed),
            // A delivered@400(+80).
            return .deliverWithDuplicate(receiveDeltaMs: 10, redeliverDeltaMs: 20, deliveredDeltaMs: 80)
        case .slowLink:
            // §3.6: B receives@2450 (=transmitting(320)+2130), A delivered@2500(+2180).
            return .deliverNormally(receiveDeltaMs: 2130, deliveredDeltaMs: 2180)
        }
    }

    /// Whether this scenario's canonical playback (`ChatScenarioRunner`)
    /// calls `send()` at all. `peerLoss` (§3.2) and `degradedThenRecovered`
    /// (§3.3) isolate connection/link-budget behavior with no message
    /// exchange ("No message send -- this scenario isolates connection/
    /// link-budget behavior," §3.3's preamble).
    var hasSendStep: Bool {
        switch self {
        case .peerLoss, .degradedThenRecovered:
            return false
        case .happyPair, .sendFailure, .duplicateIncoming, .slowLink:
            return true
        }
    }

    /// The literal body text each scenario's table pins for its driver
    /// `send()` row (CONTRACT.md §3.1, 3.4, 3.5, 3.6). Unused when
    /// `hasSendStep` is false.
    var canonicalSendBody: String {
        switch self {
        case .happyPair: return "hello"
        case .sendFailure: return "will-fail"
        case .duplicateIncoming: return "dup-test"
        case .slowLink: return "slow"
        case .peerLoss, .degradedThenRecovered: return ""
        }
    }

    /// A safe virtual-time target for `ChatScenarioRunner` to advance the
    /// shared clock to once all driver actions have been performed, past
    /// the last event any of this scenario's tables pin (with a small
    /// margin), so every scheduled action fires:
    /// happyPair last=400, peerLoss last=510, degradedThenRecovered
    /// last=710, sendFailure last=450, duplicateIncoming last=400,
    /// slowLink last=2500 (CONTRACT.md §3.1-3.6).
    var scriptEndMs: Int64 {
        switch self {
        case .happyPair: return 500
        case .peerLoss: return 600
        case .degradedThenRecovered: return 800
        case .sendFailure: return 550
        case .duplicateIncoming: return 500
        case .slowLink: return 2600
        }
    }
}

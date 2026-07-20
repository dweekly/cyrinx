/// Deterministic virtual-time scheduler for `SimulatedChatTransportClient`
/// pairs. Apps/Chat/CONTRACT.md §2, point 3: "A `VirtualClock` starts at
/// `0` ms ... Swift: `clock.advance(byMs:)` ... No test anywhere sleeps on
/// wall-clock time."
///
/// Implemented as a small discrete-event scheduler: callers `schedule(atMs:)`
/// a closure to run once the clock reaches a given virtual time, and
/// `advance(byMs:)` / `advance(toMs:)` fire every due closure, in
/// `(dueAtMs, insertion order)` order, cascading through any further
/// closures newly scheduled by ones that just fired (e.g. `connect()`
/// schedules `connecting` to fire immediately, which itself schedules
/// `connected` 50 ms later -- a single `advance` call sweeping past both
/// times fires both, in order).
///
/// Not thread-safe: a `SimulatedChatTransportClient` pair sharing one clock
/// is a deliberately single-threaded, sequential simulation (see that
/// type's documentation) -- `advance` must only ever be called from one
/// task at a time, matching how every one of Apps/Chat/CONTRACT.md §3's six
/// scenario scripts is itself a single sequential timeline.
public final class VirtualClock: @unchecked Sendable {
    public private(set) var nowMs: Int64

    private struct ScheduledAction {
        let dueAtMs: Int64
        let sequence: Int
        let token: Int
        let action: () -> Void
    }

    private var scheduled: [ScheduledAction] = []
    private var nextSequence = 0
    private var nextToken = 0

    public init(startAtMs: Int64 = 0) {
        self.nowMs = startAtMs
    }

    /// Schedules `action` to run when the clock reaches `dueAtMs` (which
    /// must not be in the virtual past). Returns a token `cancel(_:)` can
    /// use to remove it before it fires -- the mechanism behind
    /// `ChatTransportClient.cancelSend(messageIdHex:)`.
    @discardableResult
    func schedule(atMs dueAtMs: Int64, _ action: @escaping () -> Void) -> Int {
        precondition(dueAtMs >= nowMs, "cannot schedule an action in the virtual past")
        let token = nextToken
        nextToken += 1
        scheduled.append(
            ScheduledAction(dueAtMs: dueAtMs, sequence: nextSequence, token: token, action: action)
        )
        nextSequence += 1
        return token
    }

    /// Removes a previously scheduled action if it has not yet fired. A
    /// no-op if `token` already fired or was never valid.
    func cancel(_ token: Int) {
        scheduled.removeAll { $0.token == token }
    }

    public func advance(byMs deltaMs: Int64) {
        precondition(deltaMs >= 0, "virtual clock cannot advance by a negative amount")
        advance(toMs: nowMs + deltaMs)
    }

    public func advance(toMs targetMs: Int64) {
        precondition(targetMs >= nowMs, "virtual clock cannot move backward")
        while true {
            scheduled.sort {
                $0.dueAtMs != $1.dueAtMs ? $0.dueAtMs < $1.dueAtMs : $0.sequence < $1.sequence
            }
            guard let next = scheduled.first, next.dueAtMs <= targetMs else { break }
            scheduled.removeFirst()
            nowMs = next.dueAtMs
            next.action()
        }
        nowMs = targetMs
    }
}

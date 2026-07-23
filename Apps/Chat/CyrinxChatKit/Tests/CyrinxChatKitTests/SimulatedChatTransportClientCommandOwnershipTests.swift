import Dispatch
import Foundation
import Testing

@testable import CyrinxChatKit

/// C3-28 round-5 fix pass: direct tests of CONTRACT.md §2's "Command
/// ownership (pinned)" bullet -- `SimulatedChatTransportClient`'s
/// `commandLock`/`commandActive` guard (see that type's own "Command
/// ownership enforcement" doc-comment section). Every public command
/// (`start()`, `connect()`, `send()`, `cancelSend(messageIdHex:)`,
/// `disconnect()`, `stop()`) must reject a second, concurrent entrant
/// deterministically instead of corrupting state -- these tests drive that
/// concurrency for real (both via structured-concurrency `Task`s racing a
/// deliberately suspended `send()`, and via genuine, directly-owned OS
/// threads -- `runConcurrentlyOnDedicatedThreads` below, `Foundation.Thread`
/// under the hood, round 6's replacement for the original
/// `DispatchQueue.concurrentPerform`-based approach; see that function's own
/// doc comment for why) rather than merely asserting the
/// single-sequential-caller behavior every other test in this package
/// already exercises.
///
/// **Why a test-only `@unchecked Sendable` wrapper is needed here, and
/// nowhere else in this package:** `SimulatedChatTransportClient`
/// deliberately does NOT conform to `Sendable` (see its own "Concurrency
/// note") -- production code must never share one instance across
/// concurrency domains. `UncheckedSendableClientBox` below exists solely to
/// defeat the compiler's Sendable checker so these tests can legally
/// capture and call into the SAME client instance from multiple `Task`s
/// and real OS threads, which is exactly the misuse scenario the guard
/// must trap. It is not a safety claim about the wrapped client -- quite
/// the opposite, it exists to reach the one place in this package where
/// concurrent access is deliberately exercised, specifically to prove the
/// guard rejects it.
///
/// **C3-28 round-6 CI-deadlock fix.** The round-5 harness (below, prior to
/// this round) drove its "real OS threads" probes via
/// `DispatchQueue.concurrentPerform` and blocked EVERY racer's calling
/// thread on a `DispatchSemaphore.wait()` (`runAsyncAndWait`); its
/// `beginLatchedSend` helper additionally blocked ITS OWN caller -- this
/// suite's `@Test` `async` function bodies, themselves `Task`s on Swift's
/// cooperative pool -- on a second `DispatchSemaphore.wait()`.
/// `DispatchQueue.concurrentPerform`'s worker threads, and Swift
/// concurrency's own cooperative-pool worker threads, are drawn from the
/// SAME underlying, core-count-bounded thread budget on Darwin; blocking
/// threads from that shared budget while `async` work that needs the same
/// budget to make progress is still outstanding is a classic thread-pool
/// starvation deadlock, and it gets categorically worse on a low-core CI
/// runner (a smaller budget to begin with) multiplied by this suite's five
/// `@Test` functions running in parallel by Swift Testing's own default
/// (each opening its own 2-16 blocked racer threads independently). GitHub
/// killed both Swift CI jobs at their 6-hour cap. This round rewrites the
/// harness (bottom of this file) so NO cooperative-executor thread ever
/// blocks on a synchronous primitive: `AsyncLatchGate` is now a plain
/// `actor` offering only `async`, continuation-based suspension (no
/// `DispatchSemaphore` at all), and the "real OS threads" probes now run
/// via `runConcurrentlyOnDedicatedThreads`, which creates genuine,
/// directly-owned `Foundation.Thread`s -- NOT drawn from
/// `DispatchQueue.concurrentPerform`'s shared pool -- and bridges their
/// completion back to this suite's `async` test bodies through a
/// `DispatchGroup` + a single `CheckedContinuation`, never a blocking
/// `.wait()`. It remains safe for code running on one of THOSE dedicated
/// threads (never on the cooperative pool) to block on a
/// `DispatchSemaphore` bridging to a child `Task`, which is what
/// `runAsyncAndWait` (kept, but re-scoped to dedicated-thread callers only)
/// still does. Every test below also now carries `.timeLimit(.minutes(1))`
/// -- Swift Testing's `TimeLimitTrait.Duration` only exposes `.minutes(_:)`
/// (`seconds`/`milliseconds`/`microseconds`/`nanoseconds` are all marked
/// `unavailable` with "Time limit must be specified in minutes," verified
/// directly against this toolchain's installed `Testing.framework`
/// `.swiftinterface`), so one minute is the finest bound this API actually
/// allows -- a hard backstop, in addition to (not instead of) the harness
/// rewrite above that is meant to prevent a hang from happening at all. The
/// suite itself also now carries `.serialized`, removing "five async cases
/// running in parallel" as a multiplier on however many dedicated threads
/// are briefly alive at once.
@Suite(
    "Simulated transport client: command ownership (concurrent public-command entry)",
    .serialized
)
struct SimulatedChatTransportClientCommandOwnershipTests {
    /// ASCII "MSGIDA__" -- CONTRACT.md §2's "Message-ID stream (pinned)"
    /// role tag for client A, transcribed from the contract document
    /// itself (not reached into `SimulatedChatTransportClient`'s own
    /// `private` copy of this constant, which is file-scoped and
    /// unreachable from here even via `@testable import`) so the expected
    /// value below is derived independently of the implementation it
    /// checks.
    private static let messageIdSeedTagA: UInt64 = 0x4D53_4749_4441_5F5F

    @Test(
        """
        R1 (round-5 reviewer probe): latch probe pausing send() after its queued emission -- a \
        concurrent disconnect() attempted while that send() is still suspended mid-span is trapped \
        deterministically (misuseHandler receives .concurrentCommand; connectionState and the event \
        stream are untouched by it) rather than racing send()'s own state mutations. The winner \
        (send(), which entered its command span first) then resumes and completes its ordinary, \
        uninterrupted queued -> transmitting -> delivered lifecycle once released -- proving the \
        span really does stay open across a genuine suspension, not merely across synchronous code.
        """,
        .timeLimit(.minutes(1))
    )
    func r1LatchedSendSpanSurvivesSuspensionAndTrapsConcurrentDisconnect() async throws {
        let (clientA, clientB, clock) = try await connectedPair(seed: 300)
        clock.advance(toMs: 300)

        let box = UncheckedSendableClientBox(client: clientA)
        // A plain array capture is safe here (unlike the
        // runConcurrentlyOnDedicatedThreads-based probes below, which use
        // `LockedBox`): this test drives exactly one concurrent attempt
        // from one other Task, not many parallel ones, and
        // `misuseHandler`'s only call happens strictly before this
        // function reads `misuseErrors` again (the `await
        // box.client.disconnect()` call immediately below has already
        // returned by the time that read happens).
        var misuseErrors: [ChatSimulatedTransportError] = []
        clientA.misuseHandler = { misuseErrors.append($0) }

        let (winner, release) = await beginLatchedSend(on: box, body: "latched-r1")

        // send() is now suspended INSIDE its own command span (past its
        // message-ID draw and .queued emission, per
        // `tryEnterCommandSpan()`'s doc comment) -- a concurrent
        // disconnect() here must find `commandActive` still `true` and be
        // rejected outright, never blocked-and-retried.
        await box.client.disconnect()
        #expect(clientA.connectionState == .connected)
        #expect(misuseErrors == [.concurrentCommand])

        await release()
        let messageIdHex = try await winner.value

        clock.advance(toMs: 2000)
        await clientA.stop()
        await clientB.stop()

        var statusesForMessage: [ChatMessageDisplayStatus] = []
        var connectionChanges: [ChatConnectionState] = []
        for await event in clientA.events {
            switch event.kind {
            case .messageStatusChanged(let idHex, let status) where idHex == messageIdHex:
                statusesForMessage.append(status)
            case .connectionChanged(let state):
                connectionChanges.append(state)
            default:
                break
            }
        }
        // The rejected disconnect() left no trace whatsoever: A's only
        // connectionChanged events are its connectedPair setup plus the
        // FINAL, legitimate stop() below -- never a
        // disconnected(userInitiated) from the trapped attempt -- and the
        // message reaches its full, uncorrupted terminal state.
        #expect(statusesForMessage == [.queued, .transmitting, .delivered])
        #expect(connectionChanges == [.connecting, .connected, .disconnected(reason: "stopped")])

        var receivedOnB = false
        for await event in clientB.events {
            if case .messageReceived = event.kind { receivedOnB = true }
        }
        #expect(receivedOnB)
    }

    @Test(
        """
        Real multi-thread probe (round-5 task item (b)): 16 real OS threads each attempt a \
        concurrent send() against ONE client instance while a first send() is deliberately held \
        open mid-span via the latch harness. Every racer is deterministically rejected with \
        .concurrentCommand, the winner's message-ID draw is untouched -- still exactly the first \
        draw from the per-role message-ID stream, CONTRACT.md §2's pinned formula -- and no \
        racer's rejected attempt leaves any trace in the event stream: "at most the serialized \
        winner draws IDs."
        """,
        .timeLimit(.minutes(1))
    )
    func realConcurrentSendVsSendFromMultipleOSThreadsPreservesMessageIdStream() async throws {
        let seed: UInt64 = 301
        let (clientA, clientB, clock) = try await connectedPair(scenario: .happyPair, seed: seed)
        clock.advance(toMs: 300)

        let box = UncheckedSendableClientBox(client: clientA)
        let (winner, release) = await beginLatchedSend(on: box, body: "winner")

        let racerCount = 16
        let outcomes = LockedBox<[RacerOutcome]>([])
        await runConcurrentlyOnDedicatedThreads(count: racerCount) { index in
            runAsyncAndWait {
                do {
                    let messageIdHex = try await box.client.send(body: "racer-\(index)")
                    outcomes.withLock { $0.append(.succeeded(messageIdHex: messageIdHex)) }
                } catch let error as ChatSimulatedTransportError {
                    outcomes.withLock { $0.append(.rejected(error)) }
                } catch {
                    outcomes.withLock { $0.append(.unexpectedError(String(describing: error))) }
                }
            }
        }

        // All 16 racers ran (and were rejected) strictly while the
        // winner's span was still held -- `release()` only happens now,
        // after `runConcurrentlyOnDedicatedThreads` has returned every
        // iteration.
        let allOutcomes = outcomes.withLock { $0 }
        #expect(allOutcomes.count == racerCount)
        #expect(allOutcomes.allSatisfy { $0 == .rejected(.concurrentCommand) })

        await release()
        let winnerMessageIdHex = try await winner.value

        clock.advance(toMs: 2000)
        await clientA.stop()
        await clientB.stop()

        // CONTRACT.md §2's "Message-ID stream (pinned)": A's message-ID
        // generator is seeded with `seed XOR MSGIDA__`, and this was the
        // very FIRST send() this client instance ever made -- so the
        // winner's ID is exactly the stream's first two u64 draws, utterly
        // unperturbed by the 16 rejected racers (which never touched
        // `messageIdPRNG` at all, since they never got past
        // `tryEnterCommandSpan()`).
        var messageIdGenerator = SplitMix64(seed: seed ^ Self.messageIdSeedTagA)
        let expectedHi = messageIdGenerator.next()
        let expectedLo = messageIdGenerator.next()
        var expectedBytes = splitMix64BigEndianBytes(expectedHi)
        expectedBytes.append(contentsOf: splitMix64BigEndianBytes(expectedLo))
        #expect(winnerMessageIdHex == expectedBytes.hexString)

        // No corruption: exactly one message ever appears in the event
        // stream at all -- the 16 rejected racers left zero trace (no
        // queued/transmitting/delivered for any of them), only the
        // winner's own complete, uninterrupted lifecycle.
        var statusesForWinner: [ChatMessageDisplayStatus] = []
        var distinctMessageIdsSeen: Set<String> = []
        for await event in clientA.events {
            if case .messageStatusChanged(let idHex, let status) = event.kind {
                distinctMessageIdsSeen.insert(idHex)
                if idHex == winnerMessageIdHex { statusesForWinner.append(status) }
            }
        }
        #expect(distinctMessageIdsSeen == [winnerMessageIdHex])
        #expect(statusesForWinner == [.queued, .transmitting, .delivered])
    }

    @Test(
        """
        Real multi-thread probe (round-5 task item (b)): 16 real OS threads each attempt a \
        concurrent disconnect() against ONE client instance while a send() is deliberately held \
        open mid-span. Every attempt is deterministically trapped via misuseHandler -- none \
        touches connectionState or emits connectionChanged -- and the winner's send completes its \
        ordinary lifecycle once released, exactly as if the 16 concurrent disconnect() calls had \
        never happened at all.
        """,
        .timeLimit(.minutes(1))
    )
    func realConcurrentSendVsDisconnectFromMultipleOSThreadsTrapsEveryAttempt() async throws {
        let (clientA, clientB, clock) = try await connectedPair(scenario: .happyPair, seed: 302)
        clock.advance(toMs: 300)

        let box = UncheckedSendableClientBox(client: clientA)
        let misuseErrors = LockedBox<[ChatSimulatedTransportError]>([])
        // `misuseHandler`'s own doc comment: may be invoked from a
        // DIFFERENT thread than whichever is currently executing the
        // active command span -- a caller that sets it is responsible for
        // its own thread-safe handling, hence `LockedBox` (guarded by an
        // `NSLock`, mirroring `SimulatedChatTransportClient`'s own
        // `commandLock` pattern) rather than a plain array here.
        clientA.misuseHandler = { error in
            misuseErrors.withLock { $0.append(error) }
        }

        let (winner, release) = await beginLatchedSend(on: box, body: "winner")

        let racerCount = 16
        await runConcurrentlyOnDedicatedThreads(count: racerCount) { _ in
            runAsyncAndWait {
                await box.client.disconnect()
            }
        }

        // While the winner's send() is still latched open, none of the 16
        // concurrent disconnect() attempts can possibly have taken
        // effect -- the guard rejects every one of them outright, before
        // any of `disconnect()`'s own body (terminality flip, cancellation
        // sweep, emission) ever runs.
        #expect(clientA.connectionState == .connected)
        #expect(misuseErrors.withLock { $0 } == Array(repeating: .concurrentCommand, count: racerCount))

        await release()
        let winnerMessageIdHex = try await winner.value

        clock.advance(toMs: 2000)
        await clientA.stop()
        await clientB.stop()

        var statusesForWinner: [ChatMessageDisplayStatus] = []
        var connectionChanges: [ChatConnectionState] = []
        for await event in clientA.events {
            switch event.kind {
            case .messageStatusChanged(let idHex, let status) where idHex == winnerMessageIdHex:
                statusesForWinner.append(status)
            case .connectionChanged(let state):
                connectionChanges.append(state)
            default:
                break
            }
        }
        // None of the 16 rejected disconnect() attempts left ANY trace: no
        // connectionChanged(disconnected) from them -- only the FINAL,
        // legitimate stop() below, once the guard's span is free again --
        // and the winner's own send completed its full, uninterrupted
        // lifecycle.
        #expect(statusesForWinner == [.queued, .transmitting, .delivered])
        #expect(connectionChanges == [.connecting, .connected, .disconnected(reason: "stopped")])
    }

    @Test(
        """
        cancelSend() concurrent with an in-flight send() is trapped by the same guard (round-5 task \
        item (c)): a second send() call's span is held open via the latch; a concurrent \
        cancelSend() is rejected outright via misuseHandler before it ever looks up \
        outgoingMessages, so cancelSend()'s own CONTRACT.md-pinned "cancelled" outcome never \
        applies -- the winner's send completes normally, unaffected.
        """,
        .timeLimit(.minutes(1))
    )
    func concurrentCancelSendDuringInFlightSendIsTrapped() async throws {
        let (clientA, clientB, clock) = try await connectedPair(seed: 303)
        clock.advance(toMs: 300)

        let box = UncheckedSendableClientBox(client: clientA)
        var misuseErrors: [ChatSimulatedTransportError] = []
        clientA.misuseHandler = { misuseErrors.append($0) }

        let (winner, release) = await beginLatchedSend(on: box, body: "latched-cancel")

        // Any messageIdHex works here -- the guard rejects at the very top
        // of cancelSend(), before it ever consults `outgoingMessages`, so
        // even an unknown ID demonstrates the trap.
        await box.client.cancelSend(messageIdHex: "deadbeefdeadbeefdeadbeefdeadbeef")
        #expect(misuseErrors == [.concurrentCommand])

        await release()
        let winnerMessageIdHex = try await winner.value

        clock.advance(toMs: 2000)
        await clientA.stop()
        await clientB.stop()

        var statusesForWinner: [ChatMessageDisplayStatus] = []
        for await event in clientA.events {
            if case .messageStatusChanged(let idHex, let status) = event.kind, idHex == winnerMessageIdHex {
                statusesForWinner.append(status)
            }
        }
        #expect(statusesForWinner == [.queued, .transmitting, .delivered])
    }

    @Test(
        """
        disconnect() and stop() overlapping each other -- both racing a single held-open send() \
        span, fired from two real, dedicated OS threads via runConcurrentlyOnDedicatedThreads \
        (round-5 task item (c), round-6 dedicated-thread rewrite) -- are EACH independently \
        trapped by the same guard: neither disconnects/stops \
        the client, the event stream is not finished, and the winner's send completes its \
        ordinary lifecycle once released.
        """,
        .timeLimit(.minutes(1))
    )
    func concurrentDisconnectAndStopOverlappingAgainstInFlightSendAreBothTrapped() async throws {
        let (clientA, clientB, clock) = try await connectedPair(seed: 304)
        clock.advance(toMs: 300)

        let box = UncheckedSendableClientBox(client: clientA)
        let misuseErrors = LockedBox<[ChatSimulatedTransportError]>([])
        clientA.misuseHandler = { error in
            misuseErrors.withLock { $0.append(error) }
        }

        let (winner, release) = await beginLatchedSend(on: box, body: "latched-overlap")

        await runConcurrentlyOnDedicatedThreads(count: 2) { index in
            runAsyncAndWait {
                if index == 0 {
                    await box.client.disconnect()
                } else {
                    await box.client.stop()
                }
            }
        }

        // Both were rejected while the winner's span was still held --
        // neither touched connectionState, and the event stream is still
        // open (a successful stop() would have finished it).
        #expect(clientA.connectionState == .connected)
        #expect(misuseErrors.withLock { $0 } == [.concurrentCommand, .concurrentCommand])

        await release()
        let winnerMessageIdHex = try await winner.value

        clock.advance(toMs: 2000)
        await clientA.stop()
        await clientB.stop()

        var statusesForWinner: [ChatMessageDisplayStatus] = []
        var connectionChanges: [ChatConnectionState] = []
        for await event in clientA.events {
            switch event.kind {
            case .messageStatusChanged(let idHex, let status) where idHex == winnerMessageIdHex:
                statusesForWinner.append(status)
            case .connectionChanged(let state):
                connectionChanges.append(state)
            default:
                break
            }
        }
        #expect(statusesForWinner == [.queued, .transmitting, .delivered])
        #expect(connectionChanges == [.connecting, .connected, .disconnected(reason: "stopped")])
    }
}

// MARK: - Round-6 concurrency test harness (async-only; no cooperative-pool blocking)

/// One racer's outcome from a concurrent `send()` attempt against an
/// already-occupied command span -- `Equatable` so the real-multithread
/// probes above can assert the whole batch with one `#expect`.
private enum RacerOutcome: Equatable {
    case rejected(ChatSimulatedTransportError)
    case succeeded(messageIdHex: String)
    case unexpectedError(String)
}

/// Test-only wrapper making a `SimulatedChatTransportClient` reference
/// passable across real OS threads and unstructured `Task`s for this
/// file's probes. See this file's own top-of-file doc comment for why
/// `@unchecked Sendable` is the right (and only, for this specific
/// purpose) tool here: it is not a safety claim about the wrapped client,
/// only a way to defeat the compiler's Sendable checker so these tests can
/// drive genuinely concurrent calls into the SAME instance -- exactly the
/// misuse scenario CONTRACT.md §2's "Command ownership (pinned)" bullet
/// requires the guard to trap.
private struct UncheckedSendableClientBox: @unchecked Sendable {
    let client: SimulatedChatTransportClient
}

/// Test-only, thread-safe mutable box guarded by a plain `NSLock` --
/// mirrors `SimulatedChatTransportClient`'s own `commandLock`/
/// `commandActive` pattern. Used by the real-multithread probes above to
/// accumulate results (rejected-call counts, captured outcomes) from
/// `runConcurrentlyOnDedicatedThreads`'s parallel closures without a data
/// race.
private final class LockedBox<Value>: @unchecked Sendable {
    private let lock = NSLock()
    private var storage: Value

    init(_ initial: Value) {
        storage = initial
    }

    @discardableResult
    func withLock<Result>(_ body: (inout Value) -> Result) -> Result {
        lock.lock()
        defer { lock.unlock() }
        return body(&storage)
    }
}

/// **C3-28 round 6:** runs `work` once per index in `0..<count`, each on
/// its OWN, directly-created `Foundation.Thread` -- deliberately NOT
/// `DispatchQueue.global()`/`.concurrentPerform`, whose worker threads are
/// drawn from libdispatch's shared, QoS-capped "workqueue" thread pool, the
/// SAME underlying, core-count-bounded budget Swift's cooperative
/// concurrency executor also schedules `async` work onto by default. The
/// round-5 harness combined `DispatchQueue.concurrentPerform` with each
/// iteration blocking on a `DispatchSemaphore` (`runAsyncAndWait` below) --
/// so every blocked racer thread ALSO counted against that shared budget.
/// On a low-core CI runner (a small budget to begin with), multiplied by up
/// to 16 racers per test and up to five `@Test` functions in this suite
/// running in parallel (Swift Testing's own default), that budget could be
/// exhausted entirely: no threads left for the `Task`s the racers were
/// themselves waiting on to ever run, deadlocking the whole test binary
/// (GitHub killed both Swift CI jobs at their 6-hour cap). A directly
/// created `Thread` is a plain OS thread requested straight from the
/// kernel, entirely outside libdispatch's workqueue pool -- blocking one of
/// THESE threads never reduces the cooperative pool's available capacity,
/// no matter how many run at once. Completion is bridged back to this
/// `async` function via a `DispatchGroup` + a single `CheckedContinuation`
/// -- never a blocking `.wait()` -- so awaiting this function doesn't block
/// a cooperative-executor thread either; this suite's `@Test` bodies
/// genuinely suspend here, yielding their own thread back to the pool for
/// other work (including the racers' own child `Task`s) to use.
private func runConcurrentlyOnDedicatedThreads(
    count: Int, _ work: @escaping @Sendable (Int) -> Void
) async {
    let group = DispatchGroup()
    for index in 0..<count {
        group.enter()
        let thread = Thread {
            work(index)
            group.leave()
        }
        // Detached; default QoS and stack size are ample -- each thread
        // does one small, bounded amount of synchronous bridging work
        // (`runAsyncAndWait` below) and exits.
        thread.start()
    }
    await withCheckedContinuation { (continuation: CheckedContinuation<Void, Never>) in
        group.notify(queue: .global()) {
            continuation.resume()
        }
    }
}

/// Blocks the CALLING thread until `body` -- run as a new, unstructured
/// `Task` on Swift's cooperative pool -- completes. **Safe to call ONLY
/// from a thread that is NOT itself part of the cooperative pool or
/// libdispatch's shared workqueue pool** -- i.e. one of the dedicated
/// `Thread`s `runConcurrentlyOnDedicatedThreads` above creates. This is
/// what makes the "real OS threads" probes above actually mean genuinely
/// concurrent calls arriving from outside Swift's own scheduling domain,
/// without that blocking ever competing with the cooperative pool for
/// worker-thread capacity. **C3-28 round 6:** this helper itself is
/// unchanged from round 5 -- the bug was never this function blocking,
/// it was WHERE it used to be called from (`DispatchQueue.concurrentPerform`
/// closures, whose worker threads DO share the cooperative pool's budget;
/// see `runConcurrentlyOnDedicatedThreads`'s own doc comment). Calling it
/// from a dedicated `Thread` instead removes that hazard entirely.
private func runAsyncAndWait(_ body: @escaping @Sendable () async -> Void) {
    let semaphore = DispatchSemaphore(value: 0)
    Task {
        await body()
        semaphore.signal()
    }
    semaphore.wait()
}

/// Test-only, race-free, fully `async` gate combining two roles for
/// `beginLatchedSend` below: (1) lets the racing `send()` call signal "I
/// have entered my latch, suspended mid-span" to whoever is awaiting
/// `waitUntilEntered()`, and (2) suspends that `send()` call until
/// `release()` runs (or resolves immediately if `release()` already ran
/// first).
///
/// **C3-28 round-6 rewrite -- this is the fix for the OTHER half of the CI
/// deadlock.** An `actor`, not an `NSLock`-guarded class: actor isolation
/// serializes every method here for free, so there is no manual locking to
/// get wrong. More importantly, NEITHER side of this gate ever calls
/// `DispatchSemaphore.wait()` anymore. The round-5 version's
/// `waitForRelease(afterSignaling:)` signalled an `enteredSemaphore` that
/// `beginLatchedSend`'s own (synchronous) body then `.wait()`ed on --
/// meaning `beginLatchedSend`'s CALLER (always one of this suite's `@Test`
/// `async` function bodies, itself a `Task` running on the cooperative
/// pool) blocked a cooperative-pool thread while waiting for ANOTHER `Task`
/// (the racing `send()`'s own `winner` `Task`, needing a cooperative-pool
/// thread to even start running) to make progress -- precisely the
/// thread-pool starvation pattern this whole fix pass eliminates, and it
/// did not need `DispatchQueue.concurrentPerform` at all to bite: with
/// enough of this suite's five `@Test` functions running in parallel, each
/// blocking its own thread this way, a small enough cooperative pool (a
/// low-core CI runner) could still exhaust itself with zero racer threads
/// in the picture. `waitUntilEntered()` below replaces that blocking wait
/// with a `CheckedContinuation` suspension: the calling `Task` genuinely
/// suspends -- yielding its thread back to the pool -- rather than holding
/// one hostage.
private actor AsyncLatchGate {
    private var isReleased = false
    private var releaseContinuation: CheckedContinuation<Void, Never>?
    private var hasEntered = false
    private var enteredContinuation: CheckedContinuation<Void, Never>?

    /// Called by the racing `send()` itself (from inside
    /// `testOnlyAfterQueuedEmissionLatch`), already past its message-ID
    /// draw and `.queued` emission, both still inside the held command span
    /// (see `SimulatedChatTransportClient.tryEnterCommandSpan()`'s doc
    /// comment): signals "entered" to whoever is awaiting
    /// `waitUntilEntered()` below, then suspends until `release()` runs (or
    /// returns immediately if `release()` already ran first).
    func waitForRelease() async {
        if let enteredContinuation {
            self.enteredContinuation = nil
            enteredContinuation.resume()
        } else {
            hasEntered = true
        }
        guard !isReleased else { return }
        await withCheckedContinuation { (continuation: CheckedContinuation<Void, Never>) in
            releaseContinuation = continuation
        }
    }

    /// Awaited by `beginLatchedSend`'s caller: suspends -- never blocks --
    /// until the racing `send()` has actually reached `waitForRelease()`
    /// above (or returns immediately if it already has). Every racer a
    /// caller runs after this returns is therefore GUARANTEED to observe
    /// `commandActive == true` on the client, eliminating any timing
    /// flakiness (CONTRACT.md's "no wall-clock sleeps and no
    /// timeout-as-control-flow": this never sleeps or times out; it
    /// suspends only on a signal from the exact event it is waiting for).
    func waitUntilEntered() async {
        guard !hasEntered else { return }
        await withCheckedContinuation { (continuation: CheckedContinuation<Void, Never>) in
            enteredContinuation = continuation
        }
    }

    func release() {
        isReleased = true
        let continuation = releaseContinuation
        releaseContinuation = nil
        continuation?.resume()
    }
}

/// Shared harness for every probe above: begins a `send()` call on
/// `box.client` that suspends -- after emitting `.queued`, before
/// `scheduleSendOutcome` -- via `testOnlyAfterQueuedEmissionLatch`, `await`s
/// (never blocks) until that suspension is confirmed entered, and returns
/// the still-in-flight `Task` plus an `async` closure to release the latch.
/// **C3-28 round 6:** now `async` itself -- previously synchronous,
/// blocking its caller's thread on a `DispatchSemaphore` (see
/// `AsyncLatchGate`'s own doc comment for why that was the other half of
/// this file's CI-deadlock fix).
private func beginLatchedSend(
    on box: UncheckedSendableClientBox, body: String
) async -> (winner: Task<String, Error>, release: @Sendable () async -> Void) {
    let gate = AsyncLatchGate()
    box.client.testOnlyAfterQueuedEmissionLatch = { _ in
        await gate.waitForRelease()
    }
    let winner = Task { try await box.client.send(body: body) }
    await gate.waitUntilEntered()
    return (winner, { await gate.release() })
}

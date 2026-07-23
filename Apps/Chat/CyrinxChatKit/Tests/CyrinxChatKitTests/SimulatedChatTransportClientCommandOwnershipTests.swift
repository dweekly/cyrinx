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
/// **Concurrency-harness safety.** No cooperative-executor thread blocks on
/// a synchronous primitive. Real-thread probes use directly-created
/// `Foundation.Thread`s, and only those dedicated threads use the bounded
/// synchronous bridge in `runAsyncAndWait`. Every bridge and gate also has
/// its own short deadline and cancellation path. This is necessary because
/// Swift Testing's `.timeLimit` reports an issue and cancels cooperatively;
/// by itself it cannot resume an abandoned continuation or semaphore wait.
/// The per-test trait remains a final outer bound, while the harness's own
/// deadlines fail close and release all of its waiters promptly. The suite
/// is serialized to avoid multiplying its deliberately adversarial thread
/// count.
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

        let latchedSend = try await beginLatchedSend(on: box, body: "latched-r1")
        defer { latchedSend.cancel() }

        // send() is now suspended INSIDE its own command span (past its
        // message-ID draw and .queued emission, per
        // `tryEnterCommandSpan()`'s doc comment) -- a concurrent
        // disconnect() here must find `commandActive` still `true` and be
        // rejected outright, never blocked-and-retried.
        await box.client.disconnect()
        #expect(clientA.connectionState == .connected)
        #expect(misuseErrors == [.concurrentCommand])

        latchedSend.release()
        let messageIdHex = try await latchedSend.winner.value

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
        let latchedSend = try await beginLatchedSend(on: box, body: "winner")
        defer { latchedSend.cancel() }

        let racerCount = 16
        let outcomes = LockedBox<[RacerOutcome]>([])
        try await runConcurrentlyOnDedicatedThreads(count: racerCount) { index in
            try runAsyncAndWait {
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

        latchedSend.release()
        let winnerMessageIdHex = try await latchedSend.winner.value

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

        let latchedSend = try await beginLatchedSend(on: box, body: "winner")
        defer { latchedSend.cancel() }

        let racerCount = 16
        try await runConcurrentlyOnDedicatedThreads(count: racerCount) { _ in
            try runAsyncAndWait {
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

        latchedSend.release()
        let winnerMessageIdHex = try await latchedSend.winner.value

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

        let latchedSend = try await beginLatchedSend(on: box, body: "latched-cancel")
        defer { latchedSend.cancel() }

        // Any messageIdHex works here -- the guard rejects at the very top
        // of cancelSend(), before it ever consults `outgoingMessages`, so
        // even an unknown ID demonstrates the trap.
        await box.client.cancelSend(messageIdHex: "deadbeefdeadbeefdeadbeefdeadbeef")
        #expect(misuseErrors == [.concurrentCommand])

        latchedSend.release()
        let winnerMessageIdHex = try await latchedSend.winner.value

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

        let latchedSend = try await beginLatchedSend(on: box, body: "latched-overlap")
        defer { latchedSend.cancel() }

        try await runConcurrentlyOnDedicatedThreads(count: 2) { index in
            try runAsyncAndWait {
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

        latchedSend.release()
        let winnerMessageIdHex = try await latchedSend.winner.value

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

    @Test(
        "The default non-throwing-command misuse handler terminates the process instead of failing open",
        .timeLimit(.minutes(1))
    )
    func defaultMisuseHandlerTerminatesTheProcess() async {
        await #expect(processExitsWith: .failure) {
            let (clientA, _, clock) = try await connectedPair(seed: 305)
            clock.advance(toMs: 300)

            let box = UncheckedSendableClientBox(client: clientA)
            let latchedSend = try await beginLatchedSend(on: box, body: "default-trap")
            defer { latchedSend.cancel() }

            // The first send still owns the command span. With no
            // test-only misuseHandler installed, a non-throwing concurrent
            // command must take the production preconditionFailure path.
            await box.client.disconnect()
        }
    }

    @Test(
        "Cancelling a latched-send waiter unwinds without a spurious harness issue",
        .timeLimit(.minutes(1))
    )
    func cancellingLatchedSendWaiterUnwindsCleanly() async throws {
        let gate = AsyncLatchGate()
        let waiter = Task {
            await gate.waitForRelease()
        }
        try await gate.waitUntilEntered()

        // Cancellation must be visible before finishing the gate stream so
        // waitForRelease classifies the unwind as cleanup, not signal loss.
        waiter.cancel()
        gate.cancel()
        await waiter.value
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

private enum ConcurrencyHarnessError: Error, CustomStringConvertible {
    case signalEnded(String)
    case timedOut(String)
    case workerFailures([String])

    var description: String {
        switch self {
        case .signalEnded(let description):
            return "\(description) ended without its expected signal"
        case .timedOut(let description):
            return "\(description) did not complete within the harness deadline"
        case .workerFailures(let failures):
            return "dedicated-thread worker failures: \(failures.joined(separator: "; "))"
        }
    }
}

/// A buffered, one-shot async signal. `AsyncStream` makes a suspended wait
/// cancellation-aware: cancelling the waiter ends iteration instead of
/// leaving an unresumed checked continuation behind.
private final class CancellationAwareSignal: Sendable {
    private let stream: AsyncStream<Void>
    private let continuation: AsyncStream<Void>.Continuation

    init() {
        (stream, continuation) = AsyncStream<Void>.makeStream(bufferingPolicy: .bufferingNewest(1))
    }

    func signal() {
        continuation.yield()
        continuation.finish()
    }

    func cancel() {
        continuation.finish()
    }

    func wait(timeout: Duration, description: String) async throws {
        try await withThrowingTaskGroup(of: Void.self) { group in
            group.addTask {
                var iterator = self.stream.makeAsyncIterator()
                guard await iterator.next() != nil else {
                    try Task.checkCancellation()
                    throw ConcurrencyHarnessError.signalEnded(description)
                }
            }
            group.addTask {
                try await Task.sleep(for: timeout)
                throw ConcurrencyHarnessError.timedOut(description)
            }
            defer { group.cancelAll() }
            _ = try await group.next()
        }
    }
}

/// Runs `work` once per index on directly-created OS threads. Worker
/// completion reaches the async test through a cancellation-aware signal;
/// a missing completion fails after 15 seconds rather than relying on
/// Swift Testing's cooperative outer time limit.
private func runConcurrentlyOnDedicatedThreads(
    count: Int, _ work: @escaping @Sendable (Int) throws -> Void
) async throws {
    let group = DispatchGroup()
    let failures = LockedBox<[String]>([])
    for index in 0..<count {
        group.enter()
        let thread = Thread {
            defer { group.leave() }
            do {
                try work(index)
            } catch {
                failures.withLock {
                    $0.append("worker \(index): \(String(describing: error))")
                }
            }
        }
        thread.start()
    }

    let completion = CancellationAwareSignal()
    group.notify(queue: .global()) {
        completion.signal()
    }
    defer { completion.cancel() }
    try await completion.wait(
        timeout: .seconds(15),
        description: "\(count) dedicated concurrency workers"
    )

    let workerFailures = failures.withLock { $0 }
    if !workerFailures.isEmpty {
        throw ConcurrencyHarnessError.workerFailures(workerFailures)
    }
}

/// Bridges async work onto a dedicated caller-owned OS thread. This helper
/// must only be called from `runConcurrentlyOnDedicatedThreads`. Its
/// semaphore wait is bounded, and the child task is cancelled on timeout.
private func runAsyncAndWait(_ body: @escaping @Sendable () async -> Void) throws {
    let semaphore = DispatchSemaphore(value: 0)
    let task = Task {
        defer { semaphore.signal() }
        await body()
    }
    guard semaphore.wait(timeout: .now() + 10) == .success else {
        task.cancel()
        throw ConcurrencyHarnessError.timedOut("dedicated thread's async bridge")
    }
}

/// Cancellation-aware, two-edge latch for a deliberately suspended send.
/// Both edges are buffered so signal-before-wait is safe. The release edge
/// has its own deadline and records an issue before returning, ensuring a
/// broken test cannot strand the winner task.
private final class AsyncLatchGate: Sendable {
    private let entered = CancellationAwareSignal()
    private let released = CancellationAwareSignal()

    func waitForRelease() async {
        entered.signal()
        do {
            try await released.wait(timeout: .seconds(10), description: "latched send release")
        } catch is CancellationError {
            // Task cancellation is itself a valid cleanup path.
        } catch {
            Issue.record("Concurrency harness failed: \(error)")
        }
    }

    func waitUntilEntered() async throws {
        try await entered.wait(timeout: .seconds(10), description: "latched send entry")
    }

    func release() {
        released.signal()
    }

    func cancel() {
        entered.cancel()
        released.cancel()
    }
}

private struct LatchedSend: Sendable {
    let winner: Task<String, Error>
    let gate: AsyncLatchGate

    func release() {
        gate.release()
    }

    func cancel() {
        winner.cancel()
        gate.cancel()
    }
}

/// Starts a send and returns only after its test latch has signalled entry.
/// Failure to enter is bounded and cancels/releases the winner before
/// propagating the harness error.
private func beginLatchedSend(
    on box: UncheckedSendableClientBox, body: String
) async throws -> LatchedSend {
    let gate = AsyncLatchGate()
    box.client.testOnlyAfterQueuedEmissionLatch = { _ in
        await gate.waitForRelease()
    }
    let winner = Task { try await box.client.send(body: body) }
    do {
        try await gate.waitUntilEntered()
        return LatchedSend(winner: winner, gate: gate)
    } catch {
        winner.cancel()
        gate.cancel()
        throw error
    }
}

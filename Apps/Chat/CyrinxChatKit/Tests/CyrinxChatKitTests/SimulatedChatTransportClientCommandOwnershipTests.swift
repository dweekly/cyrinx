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
/// deliberately suspended `send()`, and via genuine OS threads through
/// `DispatchQueue.concurrentPerform`) rather than merely asserting the
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
@Suite("Simulated transport client: command ownership (concurrent public-command entry)")
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
        """
    )
    func r1LatchedSendSpanSurvivesSuspensionAndTrapsConcurrentDisconnect() async throws {
        let (clientA, clientB, clock) = try await connectedPair(seed: 300)
        clock.advance(toMs: 300)

        let box = UncheckedSendableClientBox(client: clientA)
        // A plain array capture is safe here (unlike the
        // DispatchQueue.concurrentPerform-based probes below, which use
        // `LockedBox`): this test drives exactly one concurrent attempt
        // from one other Task, not many parallel ones, and
        // `misuseHandler`'s only call happens strictly before this
        // function reads `misuseErrors` again (the `await
        // box.client.disconnect()` call immediately below has already
        // returned by the time that read happens).
        var misuseErrors: [ChatSimulatedTransportError] = []
        clientA.misuseHandler = { misuseErrors.append($0) }

        let (winner, release) = beginLatchedSend(on: box, body: "latched-r1")

        // send() is now suspended INSIDE its own command span (past its
        // message-ID draw and .queued emission, per
        // `tryEnterCommandSpan()`'s doc comment) -- a concurrent
        // disconnect() here must find `commandActive` still `true` and be
        // rejected outright, never blocked-and-retried.
        await box.client.disconnect()
        #expect(clientA.connectionState == .connected)
        #expect(misuseErrors == [.concurrentCommand])

        release()
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
        """
    )
    func realConcurrentSendVsSendFromMultipleOSThreadsPreservesMessageIdStream() async throws {
        let seed: UInt64 = 301
        let (clientA, clientB, clock) = try await connectedPair(scenario: .happyPair, seed: seed)
        clock.advance(toMs: 300)

        let box = UncheckedSendableClientBox(client: clientA)
        let (winner, release) = beginLatchedSend(on: box, body: "winner")

        let racerCount = 16
        let outcomes = LockedBox<[RacerOutcome]>([])
        DispatchQueue.concurrentPerform(iterations: racerCount) { index in
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
        // after `concurrentPerform` has returned every iteration.
        let allOutcomes = outcomes.withLock { $0 }
        #expect(allOutcomes.count == racerCount)
        #expect(allOutcomes.allSatisfy { $0 == .rejected(.concurrentCommand) })

        release()
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
        """
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

        let (winner, release) = beginLatchedSend(on: box, body: "winner")

        let racerCount = 16
        DispatchQueue.concurrentPerform(iterations: racerCount) { _ in
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

        release()
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
        """
    )
    func concurrentCancelSendDuringInFlightSendIsTrapped() async throws {
        let (clientA, clientB, clock) = try await connectedPair(seed: 303)
        clock.advance(toMs: 300)

        let box = UncheckedSendableClientBox(client: clientA)
        var misuseErrors: [ChatSimulatedTransportError] = []
        clientA.misuseHandler = { misuseErrors.append($0) }

        let (winner, release) = beginLatchedSend(on: box, body: "latched-cancel")

        // Any messageIdHex works here -- the guard rejects at the very top
        // of cancelSend(), before it ever consults `outgoingMessages`, so
        // even an unknown ID demonstrates the trap.
        await box.client.cancelSend(messageIdHex: "deadbeefdeadbeefdeadbeefdeadbeef")
        #expect(misuseErrors == [.concurrentCommand])

        release()
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
        span, fired from two real OS threads via DispatchQueue.concurrentPerform (round-5 task \
        item (c)) -- are EACH independently trapped by the same guard: neither disconnects/stops \
        the client, the event stream is not finished, and the winner's send completes its \
        ordinary lifecycle once released.
        """
    )
    func concurrentDisconnectAndStopOverlappingAgainstInFlightSendAreBothTrapped() async throws {
        let (clientA, clientB, clock) = try await connectedPair(seed: 304)
        clock.advance(toMs: 300)

        let box = UncheckedSendableClientBox(client: clientA)
        let misuseErrors = LockedBox<[ChatSimulatedTransportError]>([])
        clientA.misuseHandler = { error in
            misuseErrors.withLock { $0.append(error) }
        }

        let (winner, release) = beginLatchedSend(on: box, body: "latched-overlap")

        DispatchQueue.concurrentPerform(iterations: 2) { index in
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

        release()
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

// MARK: - Round-5 concurrency test harness

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
/// `DispatchQueue.concurrentPerform`'s parallel closures without a data
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

/// Blocks the CALLING thread until `body` -- run as a new, unstructured
/// `Task` on Swift's cooperative pool -- completes. Lets a
/// `DispatchQueue.concurrentPerform` closure (synchronous, running on a
/// real GCD worker thread, not Swift's cooperative pool) drive a genuinely
/// concurrent `async` call into a shared client and wait for its result
/// before that `concurrentPerform` iteration returns -- this is what makes
/// "16 real OS threads" above actually mean sixteen real OS threads each
/// making a real concurrent call, not sixteen `Task`s cooperatively
/// interleaved on however many threads Swift's own pool happens to have.
private func runAsyncAndWait(_ body: @escaping @Sendable () async -> Void) {
    let semaphore = DispatchSemaphore(value: 0)
    Task {
        await body()
        semaphore.signal()
    }
    semaphore.wait()
}

/// Test-only, race-free, `async`-context-safe one-shot gate: an `await`ed
/// suspension that a DIFFERENT context resumes later by calling
/// `release()` (or which resolves immediately if `release()` already ran
/// first). Used in place of a raw `DispatchSemaphore.wait()` inside the
/// `async` `testOnlyAfterQueuedEmissionLatch` closure `beginLatchedSend`
/// installs below -- this SDK marks `DispatchSemaphore.wait()` unavailable
/// from asynchronous contexts specifically to stop code from blocking a
/// thread Swift's cooperative pool still considers available for other
/// work; a `CheckedContinuation`-based suspension is the pool-friendly way
/// to pause an `async` closure until an external event fires.
/// `enteredSemaphore` (a plain, synchronous-context-only
/// `DispatchSemaphore` -- fine to `.wait()` on from `beginLatchedSend`'s
/// own non-`async` body) is signalled only once this gate is truly ready
/// to be released -- continuation stored, or the already-released fast
/// path -- closing what would otherwise be a "lost wakeup" race between
/// signalling "entered" and a caller immediately calling `release()`.
private final class AsyncLatchGate: @unchecked Sendable {
    private let lock = NSLock()
    private var isReleased = false
    private var continuation: CheckedContinuation<Void, Never>?

    /// `withCheckedContinuation`'s `body` parameter is a plain,
    /// SYNCHRONOUS closure (not `async`) -- its entire design point, so
    /// `NSLock.lock()`/`.unlock()` (unavailable from asynchronous contexts
    /// in this SDK) are fine to call directly inside it, unlike at
    /// `waitForRelease`'s own top level.
    func waitForRelease(afterSignaling enteredSemaphore: DispatchSemaphore) async {
        await withCheckedContinuation { (continuation: CheckedContinuation<Void, Never>) in
            lock.lock()
            if isReleased {
                lock.unlock()
                continuation.resume()
                enteredSemaphore.signal()
                return
            }
            self.continuation = continuation
            lock.unlock()
            enteredSemaphore.signal()
        }
    }

    /// Plain, non-`async` function -- `NSLock.lock()`/`.unlock()` here are
    /// in a synchronous context regardless of whichever (possibly `async`)
    /// caller invokes this, exactly like `SimulatedChatTransportClient`'s
    /// own `tryEnterCommandSpan()`/`exitCommandSpan()` pattern.
    func release() {
        lock.lock()
        isReleased = true
        let continuationToResume = continuation
        continuation = nil
        lock.unlock()
        continuationToResume?.resume()
    }
}

/// Shared harness for every probe above: begins a `send()` call on
/// `box.client` that suspends -- after emitting `.queued`, before
/// `scheduleSendOutcome` -- via `testOnlyAfterQueuedEmissionLatch`, blocks
/// the caller until that suspension is confirmed entered, and returns the
/// still-in-flight `Task` plus a closure to release the latch. Every racer
/// closure a caller runs between this function returning and calling the
/// returned `release` closure is therefore GUARANTEED to observe
/// `commandActive == true` on the client -- eliminating any timing
/// flakiness from the tests above (CONTRACT.md's "no wall-clock sleeps and
/// no timeout-as-control-flow": this harness never sleeps or times out; it
/// blocks/suspends only on signals from the exact events it is waiting
/// for).
private func beginLatchedSend(
    on box: UncheckedSendableClientBox, body: String
) -> (winner: Task<String, Error>, release: () -> Void) {
    let enteredSemaphore = DispatchSemaphore(value: 0)
    let gate = AsyncLatchGate()
    box.client.testOnlyAfterQueuedEmissionLatch = { _ in
        await gate.waitForRelease(afterSignaling: enteredSemaphore)
    }
    let winner = Task { try await box.client.send(body: body) }
    enteredSemaphore.wait()
    return (winner, { gate.release() })
}

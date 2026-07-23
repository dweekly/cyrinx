package com.dweekly.cyrinx.chat

import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.Job
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.asCoroutineDispatcher
import kotlinx.coroutines.cancel
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeout
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicReference
import kotlin.concurrent.thread
import kotlin.coroutines.CoroutineContext

/**
 * Real-thread regressions for lifecycle and complete-call linearization.
 *
 * Every synchronous wait is bounded and occurs on a dedicated test thread,
 * never on a coroutine dispatcher's cooperative worker pool.
 */
class SimulatedChatTransportLinearizationTest {
    private enum class BlockingCommand {
        START,
        CONNECT,
        SEND,
    }

    @Test(timeout = TEST_TIMEOUT_MS)
    fun startConnectAndSendRetainOwnershipThroughBlockingJobStart() {
        BlockingCommand.entries.forEach { command ->
            assertCompleteCallOwnership(command)
        }
    }

    @Test(timeout = TEST_TIMEOUT_MS)
    fun sendAdmissionIsAtomicWithPeerLossDisconnectedAndPendingSweep() {
        val executor = Executors.newFixedThreadPool(4)
        val dispatcher = executor.asCoroutineDispatcher()
        val scope = CoroutineScope(dispatcher + Job())
        val releaseAdmission = CountDownLatch(1)
        try {
            val recorder = ChatTraceRecorder()
            val pair =
                SimulatedChatPair.create(
                    ChatScenario.PEER_LOSS,
                    301L,
                    scope,
                    VirtualTimeSource { 0L },
                    recorder,
                )
            connectRealPair(pair, recorder, 301L)

            val admissionEntered = CountDownLatch(1)
            val scriptedDisconnectReached = CountDownLatch(1)
            pair.clientA.testOnlyInsideSendAdmissionCriticalSection = {
                admissionEntered.countDown()
                check(releaseAdmission.await(WAIT_SECONDS, TimeUnit.SECONDS))
            }
            pair.clientA.testOnlyBeforeScriptedDisconnectValidation = {
                scriptedDisconnectReached.countDown()
            }

            val sendError = AtomicReference<Throwable?>()
            val sendThread =
                thread(name = "send-admission-owner") {
                    try {
                        runBlocking { pair.clientA.send("admission-race") }
                    } catch (error: Throwable) {
                        sendError.set(error)
                    }
                }

            assertTrue(admissionEntered.await(WAIT_SECONDS, TimeUnit.SECONDS))
            assertTrue(scriptedDisconnectReached.await(WAIT_SECONDS, TimeUnit.SECONDS))
            assertFalse(
                "scripted Disconnected cannot interleave while send owns lifecycleLock",
                aEntries(recorder).any { it.event.isDisconnected() },
            )

            releaseAdmission.countDown()
            sendThread.join(WAIT_MILLIS)
            assertFalse("send thread did not return", sendThread.isAlive)
            assertNull("send failed unexpectedly", sendError.get())

            awaitCondition("peer-loss sweep did not terminalize the send") {
                aEntries(recorder).any { it.event.isFailed(ChatReasonStrings.PEER_LOST) }
            }
            awaitCondition("admitted send and peer-loss jobs did not quiesce") {
                pair.clientA.backgroundJobCount == 0
            }
            val entries = aEntries(recorder)
            val statuses = entries.mapNotNull { (it.event as? ChatEvent.MessageStatusChanged)?.status }
            assertEquals(
                listOf(
                    ChatMessageDisplayStatus.Queued,
                    ChatMessageDisplayStatus.Failed(ChatReasonStrings.PEER_LOST),
                ),
                statuses,
            )
            val queuedIndex = entries.indexOfFirst { it.event.isStatus(ChatMessageDisplayStatus.Queued) }
            val disconnectedIndex = entries.indexOfFirst { it.event.isDisconnected() }
            val failedIndex = entries.indexOfFirst { it.event.isFailed(ChatReasonStrings.PEER_LOST) }
            assertTrue("Queued must linearize before scripted Disconnected", queuedIndex < disconnectedIndex)
            assertEquals(
                "the pending sweep must immediately follow Disconnected",
                disconnectedIndex + 1,
                failedIndex,
            )
            assertFalse(
                "a peer must not receive a send swept by scripted disconnect",
                recorder.entries().any { it.client == 'B' && it.event is ChatEvent.MessageReceived },
            )
        } finally {
            releaseAdmission.countDown()
            scope.cancel()
            dispatcher.close()
            executor.shutdownNow()
        }
    }

    @Test(timeout = TEST_TIMEOUT_MS)
    fun resumedSendStatusCannotEmitAfterScriptedDisconnectSweep() {
        val executor = Executors.newFixedThreadPool(4)
        val dispatcher = executor.asCoroutineDispatcher()
        val scope = CoroutineScope(dispatcher + Job())
        val releaseStatus = CompletableDeferred<Unit>()
        try {
            val recorder = ChatTraceRecorder()
            val pair =
                SimulatedChatPair.create(
                    ChatScenario.PEER_LOSS,
                    302L,
                    scope,
                    VirtualTimeSource { 0L },
                    recorder,
                )
            connectRealPair(pair, recorder, 302L)

            val statusValidationReached = CountDownLatch(1)
            val holdFirstStatus = AtomicBoolean(true)
            val hookError = AtomicReference<Throwable?>()
            pair.clientA.testOnlyBeforeSendStatusValidation = {
                if (holdFirstStatus.compareAndSet(true, false)) {
                    statusValidationReached.countDown()
                    awaitGate(releaseStatus, hookError)
                }
            }

            runBlocking { pair.clientA.send("held-before-transmitting") }
            assertTrue(statusValidationReached.await(WAIT_SECONDS, TimeUnit.SECONDS))
            awaitCondition("scripted disconnect did not run while status validation was held") {
                aEntries(recorder).any { it.event.isFailed(ChatReasonStrings.PEER_LOST) }
            }

            releaseStatus.complete(Unit)
            awaitCondition("cancelled send and peer-loss jobs did not quiesce") {
                pair.clientA.backgroundJobCount == 0
            }
            assertNull("status-validation gate failed", hookError.get())
            assertEquals(
                listOf(
                    ChatMessageDisplayStatus.Queued,
                    ChatMessageDisplayStatus.Failed(ChatReasonStrings.PEER_LOST),
                ),
                aEntries(recorder).mapNotNull { (it.event as? ChatEvent.MessageStatusChanged)?.status },
            )
        } finally {
            releaseStatus.complete(Unit)
            scope.cancel()
            dispatcher.close()
            executor.shutdownNow()
        }
    }

    @Test(timeout = TEST_TIMEOUT_MS)
    fun cancelSendLinearizesAgainstAResumedStatusEmission() {
        val executor = Executors.newFixedThreadPool(4)
        val dispatcher = executor.asCoroutineDispatcher()
        val scope = CoroutineScope(dispatcher + Job())
        val releaseStatus = CompletableDeferred<Unit>()
        try {
            val recorder = ChatTraceRecorder()
            val pair =
                SimulatedChatPair.create(
                    ChatScenario.HAPPY_PAIR,
                    303L,
                    scope,
                    VirtualTimeSource { 0L },
                    recorder,
                )
            connectRealPair(pair, recorder, 303L)

            val statusValidationReached = CountDownLatch(1)
            val holdFirstStatus = AtomicBoolean(true)
            val hookError = AtomicReference<Throwable?>()
            pair.clientA.testOnlyBeforeSendStatusValidation = {
                if (holdFirstStatus.compareAndSet(true, false)) {
                    statusValidationReached.countDown()
                    awaitGate(releaseStatus, hookError)
                }
            }

            val messageId = runBlocking { pair.clientA.send("cancel-race") }
            assertTrue(statusValidationReached.await(WAIT_SECONDS, TimeUnit.SECONDS))
            runBlocking { pair.clientA.cancelSend(messageId) }
            releaseStatus.complete(Unit)
            awaitCondition("cancelled send job did not quiesce") { pair.clientA.backgroundJobCount == 0 }
            assertNull("status-validation gate failed", hookError.get())

            assertEquals(
                listOf(
                    ChatMessageDisplayStatus.Queued,
                    ChatMessageDisplayStatus.Failed(ChatReasonStrings.CANCELLED),
                ),
                aEntries(recorder).mapNotNull { (it.event as? ChatEvent.MessageStatusChanged)?.status },
            )
        } finally {
            releaseStatus.complete(Unit)
            scope.cancel()
            dispatcher.close()
            executor.shutdownNow()
        }
    }

    @Test(timeout = TEST_TIMEOUT_MS)
    fun inboundDeliveryHeldPastPeerLossIsDroppedAtFireTime() {
        val executor = Executors.newFixedThreadPool(4)
        val dispatcher = executor.asCoroutineDispatcher()
        val scope = CoroutineScope(dispatcher + Job())
        val releaseDelivery = CompletableDeferred<Unit>()
        try {
            val recorder = ChatTraceRecorder()
            val pair =
                SimulatedChatPair.create(
                    ChatScenario.PEER_LOSS,
                    304L,
                    scope,
                    VirtualTimeSource { 0L },
                    recorder,
                )
            connectRealPair(pair, recorder, 304L)

            val deliveryValidationReached = CountDownLatch(1)
            val holdFirstDelivery = AtomicBoolean(true)
            val hookError = AtomicReference<Throwable?>()
            pair.clientA.testOnlyBeforeDeliveryValidation = {
                if (holdFirstDelivery.compareAndSet(true, false)) {
                    deliveryValidationReached.countDown()
                    awaitGate(releaseDelivery, hookError)
                }
            }

            runBlocking { pair.clientA.send("held-before-delivery") }
            assertTrue(deliveryValidationReached.await(WAIT_SECONDS, TimeUnit.SECONDS))
            awaitCondition("peer-loss sweep did not run while delivery was held") {
                aEntries(recorder).any { it.event.isFailed(ChatReasonStrings.PEER_LOST) }
            }
            releaseDelivery.complete(Unit)
            awaitCondition("cancelled delivery and peer-loss jobs did not quiesce") {
                pair.clientA.backgroundJobCount == 0
            }
            assertNull("delivery-validation gate failed", hookError.get())

            assertFalse(
                "receiver must not observe delivery after its scripted disconnect",
                recorder.entries().any { it.client == 'B' && it.event is ChatEvent.MessageReceived },
            )
            assertEquals(
                listOf(
                    ChatMessageDisplayStatus.Queued,
                    ChatMessageDisplayStatus.Transmitting,
                    ChatMessageDisplayStatus.Failed(ChatReasonStrings.PEER_LOST),
                ),
                aEntries(recorder).mapNotNull { (it.event as? ChatEvent.MessageStatusChanged)?.status },
            )
        } finally {
            releaseDelivery.complete(Unit)
            scope.cancel()
            dispatcher.close()
            executor.shutdownNow()
        }
    }

    @Test(timeout = TEST_TIMEOUT_MS)
    fun selfOwnedEffectHeldPastActualInvalidationIsDroppedAtFireTime() {
        val executor = Executors.newFixedThreadPool(4)
        val dispatcher = executor.asCoroutineDispatcher()
        val scope = CoroutineScope(dispatcher + Job())
        val releaseEffect = CompletableDeferred<Unit>()
        try {
            val recorder = ChatTraceRecorder()
            val pair =
                SimulatedChatPair.create(
                    ChatScenario.HAPPY_PAIR,
                    305L,
                    scope,
                    VirtualTimeSource { 0L },
                    recorder,
                )
            val effectValidationReached = CountDownLatch(1)
            val hookError = AtomicReference<Throwable?>()
            pair.clientA.testOnlyBeforeScheduledSelfEffectValidation = {
                effectValidationReached.countDown()
                awaitGate(releaseEffect, hookError)
            }
            connectRealPair(pair, recorder, 305L)
            assertTrue(effectValidationReached.await(WAIT_SECONDS, TimeUnit.SECONDS))

            val disconnectError = AtomicReference<Throwable?>()
            val disconnectThread =
                thread(name = "disconnect-during-self-effect") {
                    try {
                        runBlocking { pair.clientA.disconnect() }
                    } catch (error: Throwable) {
                        disconnectError.set(error)
                    }
                }
            awaitCondition("disconnect did not commit lifecycle invalidation") { pair.clientA.isTerminal() }
            assertTrue("disconnect must still be joining the held effect", disconnectThread.isAlive)

            releaseEffect.complete(Unit)
            disconnectThread.join(WAIT_MILLIS)
            assertFalse("disconnect did not return", disconnectThread.isAlive)
            assertNull("disconnect failed unexpectedly", disconnectError.get())
            assertNull("self-effect validation gate failed", hookError.get())
            assertFalse(
                "the held post-connect effect must be dropped after invalidation",
                aEntries(recorder).any { it.event is ChatEvent.LinkBudgetChanged },
            )
        } finally {
            releaseEffect.complete(Unit)
            scope.cancel()
            dispatcher.close()
            executor.shutdownNow()
        }
    }

    private fun assertCompleteCallOwnership(command: BlockingCommand) {
        val executor = Executors.newFixedThreadPool(4)
        val delegate = executor.asCoroutineDispatcher()
        val dispatcher = CallerBlockingDispatcher(delegate)
        val scope = CoroutineScope(dispatcher + Job())
        val releaseDispatch = CountDownLatch(1)
        var firstThread: Thread? = null
        var racerThread: Thread? = null
        try {
            val recorder = ChatTraceRecorder()
            val seed = 400L + command.ordinal
            val pair =
                SimulatedChatPair.create(
                    ChatScenario.HAPPY_PAIR,
                    seed,
                    scope,
                    VirtualTimeSource { 0L },
                    recorder,
                )
            val (_, idB) = expectedPeerIds(seed)
            when (command) {
                BlockingCommand.START -> runBlocking { pair.clientA.start() }
                BlockingCommand.CONNECT -> startRealPair(pair, recorder)
                BlockingCommand.SEND -> {
                    connectRealPair(pair, recorder, seed)
                    awaitCondition("connect post-script did not quiesce before arming send") {
                        pair.clientA.backgroundJobCount == 0
                    }
                }
            }

            val dispatchEntered = CountDownLatch(1)
            dispatcher.arm(dispatchEntered, releaseDispatch)
            val firstError = AtomicReference<Throwable?>()
            val firstReturned = AtomicBoolean(false)
            val ownerThread =
                thread(name = "${command.name.lowercase()}-owner") {
                    try {
                        runBlocking {
                            when (command) {
                                BlockingCommand.START -> pair.clientB.start()
                                BlockingCommand.CONNECT -> pair.clientA.connect(idB.toHexString())
                                BlockingCommand.SEND -> pair.clientA.send("blocking-dispatch")
                            }
                        }
                    } catch (error: Throwable) {
                        firstError.set(error)
                    } finally {
                        firstReturned.set(true)
                    }
                }
            firstThread = ownerThread

            assertTrue(
                "$command never entered Job.start() dispatch",
                dispatchEntered.await(WAIT_SECONDS, TimeUnit.SECONDS),
            )
            assertTrue("$command caller thread must still be inside Job.start()", ownerThread.isAlive)
            assertFalse("$command returned before its blocking dispatch returned", firstReturned.get())

            val guardedClient = if (command == BlockingCommand.START) pair.clientB else pair.clientA
            val racerEntered = CountDownLatch(1)
            val racerReturned = CountDownLatch(1)
            val racerError = AtomicReference<Throwable?>()
            val competingThread =
                thread(name = "${command.name.lowercase()}-racer") {
                    racerEntered.countDown()
                    try {
                        runBlocking { guardedClient.disconnect() }
                    } catch (error: Throwable) {
                        racerError.set(error)
                    } finally {
                        racerReturned.countDown()
                    }
                }
            racerThread = competingThread
            assertTrue(
                "$command racer thread was not scheduled",
                racerEntered.await(WAIT_SECONDS, TimeUnit.SECONDS),
            )

            // Record the result before releasing dispatch, but release and join
            // both threads before asserting. On broken production code the racer
            // may be admitted into NonCancellable cancel/join; this shape still
            // fails closed without stranding the JUnit thread.
            val racerReturnedWhileOwnerBlocked = racerReturned.await(5, TimeUnit.SECONDS)
            val ownerStillBlocked = ownerThread.isAlive && !firstReturned.get()
            releaseDispatch.countDown()
            ownerThread.join(WAIT_MILLIS)
            competingThread.join(WAIT_MILLIS)

            assertTrue(
                "$command racer must return while the owner remains inside Job.start()",
                racerReturnedWhileOwnerBlocked && ownerStillBlocked,
            )
            val rejection = racerError.get() as? ChatTransportError
            assertTrue(
                "$command must reject an overlapping same-client command",
                rejection?.isConcurrentCommand == true,
            )
            assertFalse("$command caller did not return after dispatch release", ownerThread.isAlive)
            assertFalse("$command racer did not return after dispatch release", competingThread.isAlive)
            assertNull("$command failed unexpectedly", firstError.get())
        } finally {
            releaseDispatch.countDown()
            firstThread?.join(WAIT_MILLIS)
            racerThread?.join(WAIT_MILLIS)
            scope.cancel()
            delegate.close()
            executor.shutdownNow()
        }
    }

    private fun startRealPair(
        pair: SimulatedChatPair,
        recorder: ChatTraceRecorder,
    ) {
        runBlocking {
            pair.clientA.start()
            pair.clientB.start()
        }
        awaitCondition("peer discovery did not complete") {
            recorder.entries().count { it.event is ChatEvent.PeerFound } == 2
        }
    }

    private fun connectRealPair(
        pair: SimulatedChatPair,
        recorder: ChatTraceRecorder,
        seed: Long,
    ) {
        startRealPair(pair, recorder)
        val (_, idB) = expectedPeerIds(seed)
        runBlocking { pair.clientA.connect(idB.toHexString()) }
        awaitCondition("connection did not reach Connected") {
            aEntries(recorder).any {
                (it.event as? ChatEvent.ConnectionChanged)?.state == ChatConnectionState.Connected
            }
        }
    }

    private fun aEntries(recorder: ChatTraceRecorder): List<ChatTraceEntry> =
        recorder.entries().filter { it.client == 'A' }

    private fun ChatEvent.isDisconnected(): Boolean =
        this is ChatEvent.ConnectionChanged && state is ChatConnectionState.Disconnected

    private fun ChatEvent.isStatus(expected: ChatMessageDisplayStatus): Boolean =
        this is ChatEvent.MessageStatusChanged && status == expected

    private fun ChatEvent.isFailed(reason: String): Boolean =
        isStatus(ChatMessageDisplayStatus.Failed(reason))

    private fun awaitCondition(
        description: String,
        predicate: () -> Boolean,
    ) {
        val deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(WAIT_SECONDS)
        while (!predicate()) {
            Thread.onSpinWait()
            check(System.nanoTime() < deadline) { description }
        }
    }

    private suspend fun awaitGate(
        gate: CompletableDeferred<Unit>,
        errorSink: AtomicReference<Throwable?>,
    ) {
        try {
            withContext(NonCancellable) {
                withTimeout(WAIT_MILLIS) { gate.await() }
            }
        } catch (error: Throwable) {
            errorSink.set(error)
            throw error
        }
    }

    private class CallerBlockingDispatcher(
        private val delegate: CoroutineDispatcher,
    ) : CoroutineDispatcher() {
        private val armed = AtomicBoolean(false)

        @Volatile
        private var entered: CountDownLatch? = null

        @Volatile
        private var release: CountDownLatch? = null

        fun arm(
            entered: CountDownLatch,
            release: CountDownLatch,
        ) {
            this.entered = entered
            this.release = release
            armed.set(true)
        }

        override fun dispatch(
            context: CoroutineContext,
            block: Runnable,
        ) {
            if (armed.compareAndSet(true, false)) {
                entered!!.countDown()
                check(release!!.await(WAIT_SECONDS, TimeUnit.SECONDS)) {
                    "caller-blocking dispatcher was never released"
                }
            }
            delegate.dispatch(context, block)
        }
    }

    companion object {
        private const val WAIT_SECONDS = 10L
        private const val WAIT_MILLIS = WAIT_SECONDS * 1_000
        private const val TEST_TIMEOUT_MS = 20_000L
    }
}

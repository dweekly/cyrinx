package com.dweekly.cyrinx.chat

import kotlinx.coroutines.channels.BufferOverflow
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.asSharedFlow
import java.util.concurrent.atomic.AtomicLong

/**
 * The per-client-instance event stream backing [ChatTransportClient.events]:
 * bounded buffer of [EVENT_BUFFER_CAPACITY] events, drop-oldest, with `eventSeq`
 * assigned monotonically from 0 before an event is offered to the buffer so a slow
 * consumer can detect drops as `eventSeq` gaps even though it never observed the
 * dropped events themselves. ../../../CONTRACT.md section 1.7's
 * "`eventSeq` gap-detection contract and bounded-buffer policy".
 *
 * Kept as its own small class (rather than inlined into
 * [SimulatedChatTransportClient]) so the buffer/drop/gap-detection behavior itself
 * is unit-testable without any scenario, PRNG, or codec machinery -- see
 * ChatEventBusTest.
 */
class ChatEventBus {
    private val nextEventSeq = AtomicLong(0)

    // extraBufferCapacity=512 + onBufferOverflow=DROP_OLDEST is the literal Kotlin
    // realization CONTRACT.md section 1.7 names for the bounded, drop-oldest,
    // never-blocks-the-producer policy (mirroring Swift's
    // `AsyncStream(bufferingPolicy: .bufferingNewest(512))`).
    private val flow =
        MutableSharedFlow<ChatEvent>(
            replay = 0,
            extraBufferCapacity = EVENT_BUFFER_CAPACITY,
            onBufferOverflow = BufferOverflow.DROP_OLDEST,
        )

    val events: Flow<ChatEvent> = flow.asSharedFlow()

    /**
     * Assigns the next `eventSeq` and offers `build(eventSeq)`'s result to the
     * bounded buffer. Never suspends and never fails: under `DROP_OLDEST`,
     * `tryEmit` always succeeds (it makes room by discarding the oldest buffered
     * event rather than rejecting the new one), which is exactly the "never by
     * blocking the producer indefinitely" requirement in CONTRACT.md section 1.7.
     */
    fun emit(build: (eventSeq: Long) -> ChatEvent) {
        val seq = nextEventSeq.getAndIncrement()
        val accepted = flow.tryEmit(build(seq))
        check(accepted) { "MutableSharedFlow.tryEmit unexpectedly rejected an event under DROP_OLDEST" }
    }

    companion object {
        /** Bounded buffer size, pinned in ../../../CONTRACT.md section 1.7. */
        const val EVENT_BUFFER_CAPACITY: Int = 512
    }
}

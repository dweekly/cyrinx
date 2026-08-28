package com.dweekly.cyrinx.chat

import java.util.concurrent.CopyOnWriteArrayList

/**
 * One recorded trace line's constituent data, before JSON rendering. One entry
 * corresponds to one row of ../../../CONTRACT.md section 4's JSON-lines schema
 * (`eventSeq, virtualTimeMs, client, event`).
 */
data class ChatTraceEntry(
    val eventSeq: Long,
    val virtualTimeMs: Long,
    val client: Char,
    val event: ChatEvent,
)

/**
 * Collects [ChatTraceEntry] values from a [SimulatedChatPair] as they are
 * emitted, in emission order.
 *
 * Trace capture is wired through [SimulatedChatTransportClient]'s synchronous
 * `traceSink` hook (set via [SimulatedChatPair.create]'s `recorder` parameter)
 * rather than by collecting the public [ChatTransportClient.events] `Flow` after
 * the fact. That distinction matters under `kotlinx-coroutines-test`'s virtual
 * dispatcher: `advanceUntilIdle()`/`advanceTimeBy()` can run many scheduled
 * continuations -- across both clients, at several different virtual times --
 * before a separate `Flow`-collecting coroutine gets to drain what
 * `ChatEventBus`'s bounded buffer accumulated, which would attach the WRONG
 * (later) `virtualTimeMs` to earlier-emitted events if time were read at
 * collection time instead of emission time. Recording synchronously inside the
 * same call that constructs the event eliminates that drift entirely.
 *
 * Because every scheduled transition in [SimulatedChatTransportClient] is a
 * `kotlinx.coroutines.delay`-based continuation, and `TestCoroutineScheduler`
 * (kotlinx-coroutines-test) resolves queued continuations in non-decreasing
 * virtual-time order, entries recorded here across BOTH clients arrive already in
 * correct chronological order -- exactly what CONTRACT.md section 4's "the
 * file's line order is authoritative" requires, with no separate sort step
 * needed.
 */
class ChatTraceRecorder {
    private val entries = CopyOnWriteArrayList<ChatTraceEntry>()

    /** Returns a sink function bound to `client` ('A' or 'B'), suitable for
     * [SimulatedChatTransportClient]'s `traceSink` constructor parameter. */
    fun sinkFor(client: Char): (eventSeq: Long, virtualTimeMs: Long, event: ChatEvent) -> Unit =
        { eventSeq, virtualTimeMs, event ->
            entries.add(ChatTraceEntry(eventSeq, virtualTimeMs, client, event))
        }

    /** A snapshot of every entry recorded so far, in emission order. */
    fun entries(): List<ChatTraceEntry> = entries.toList()

    /** Renders every recorded entry to the canonical JSON-lines trace format
     * (../../../CONTRACT.md section 4), one line per entry, newline-terminated
     * (including after the last line, matching the conventional JSON-lines /
     * POSIX-text-file convention of a trailing newline) when non-empty. */
    fun toJsonLines(): String {
        if (entries.isEmpty()) return ""
        return entries.joinToString(separator = "\n", postfix = "\n") { ChatTraceJson.renderLine(it) }
    }
}

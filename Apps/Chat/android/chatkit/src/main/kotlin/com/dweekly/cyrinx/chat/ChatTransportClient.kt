package com.dweekly.cyrinx.chat

import kotlinx.coroutines.flow.Flow

/**
 * Platform-neutral chat transport client contract. ../../../CONTRACT.md
 * section 1.8. Implemented in this module by [SimulatedChatTransportClient]; the
 * live Cyrinx 3 SDK adapter is added in a later stage (C3-31) behind this same
 * interface.
 *
 * DECISION (not pinned by the brief, per ../../../CONTRACT.md section 1.8): the
 * [connect] parameter name (`peerIdHex`) matches the Swift argument label's
 * meaning (`toPeer idHex:`) since Kotlin has no external/internal label split;
 * error signaling uses plain thrown exceptions on `suspend fun`s (Kotlin has no
 * `async throws` distinction) rather than a `Result`-wrapping return type,
 * matching this repo's existing Kotlin conventions (no `Result<T>` usage found in
 * Apps/HIL/android).
 */
interface ChatTransportClient {
    suspend fun start()

    suspend fun stop()

    /** Bounded buffer ([ChatEventBus.EVENT_BUFFER_CAPACITY], drop-oldest); see
     * [ChatEvent]'s eventSeq gap-detection contract. */
    val events: Flow<ChatEvent>

    suspend fun connect(peerIdHex: String)

    suspend fun disconnect()

    /**
     * Returns on queue acceptance, NOT delivery -- see
     * [ChatMessageDisplayStatus]'s honest-delivery caveat. The returned string is
     * the new message's `messageIdHex`.
     */
    suspend fun send(body: String): String

    suspend fun cancelSend(messageIdHex: String)
}

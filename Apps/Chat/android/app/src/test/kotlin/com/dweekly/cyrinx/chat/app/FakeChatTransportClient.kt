package com.dweekly.cyrinx.chat.app

import com.dweekly.cyrinx.chat.ChatEvent
import com.dweekly.cyrinx.chat.ChatTransportClient
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableSharedFlow

/**
 * Test-only [ChatTransportClient] that lets a test push hand-built
 * [ChatEvent]s directly through [ChatViewModel]'s real `applyEvent`
 * collection loop -- see [ChatViewModel]'s `transportClientOverride`
 * constructor parameter for why this stands in for chatkit's own
 * [com.dweekly.cyrinx.chat.SimulatedChatTransportClient
 * .testOnlyInjectionSeam]: that property is `internal` to `:chatkit`, and
 * `:app` (this module) depends on `:chatkit`'s public API only
 * (`implementation(project(":chatkit"))`, no `-Xfriend-paths`), matching this
 * lane's read-only-`:chatkit` boundary. Used by ChatViewModelTest's
 * messageGap-consumption tests to emit [ChatEvent.MessageGap] the same way a
 * real client's reorder-window bypass or scope-end flush would
 * (../../../CONTRACT.md section 2's "Gap surfacing (pinned)"), without
 * needing chatkit internals.
 *
 * [start]/[stop]/[connect]/[disconnect]/[cancelSend] are no-ops -- nothing in
 * this fake's own test suite drives lifecycle through them; a test that needs
 * a specific [ChatConnectionState][com.dweekly.cyrinx.chat.ChatConnectionState]
 * emits the corresponding [ChatEvent.ConnectionChanged] via [emit] directly,
 * matching how every other event is driven.
 */
class FakeChatTransportClient : ChatTransportClient {
    /** Buffered so [emit] never suspends waiting for a collector to be ready
     * -- mirrors chatkit's own bounded/buffered event bus in spirit (see
     * `ChatEvent`'s eventSeq gap-detection contract), though this fake has no
     * drop-oldest policy of its own: tests here are small, hand-scripted
     * sequences, not the six pinned high-volume scenarios. */
    private val eventFlow = MutableSharedFlow<ChatEvent>(extraBufferCapacity = 64)

    override val events: Flow<ChatEvent> = eventFlow

    /** [send]'s canned return value -- tests that need a specific
     * `messageIdHex` (e.g. to later emit a matching
     * `messageStatusChanged`/`messageReceived`) set this before calling
     * [com.dweekly.cyrinx.chat.app.ChatViewModel.sendMessage]. */
    var sendResult: String = "aa".repeat(16)

    /** Every body passed to [send], in call order -- test regression
     * coverage only, not consulted by production code. */
    val sentBodies: MutableList<String> = mutableListOf()

    override suspend fun start() {}

    override suspend fun stop() {}

    override suspend fun connect(peerIdHex: String) {}

    override suspend fun disconnect() {}

    override suspend fun send(body: String): String {
        sentBodies.add(body)
        return sendResult
    }

    override suspend fun cancelSend(messageIdHex: String) {}

    /** Emits [event] to every current/future collector of [events] -- the
     * seam this fake exists for; see this class's own doc comment. */
    suspend fun emit(event: ChatEvent) {
        eventFlow.emit(event)
    }
}

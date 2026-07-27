package com.dweekly.cyrinx.chat.app

import android.os.SystemClock
import androidx.lifecycle.ViewModel
import com.dweekly.cyrinx.chat.ChatConnectionState
import com.dweekly.cyrinx.chat.ChatEvent
import com.dweekly.cyrinx.chat.ChatTransportClient
import com.dweekly.cyrinx.chat.ChatTransportError
import com.dweekly.cyrinx.chat.SimulatedChatPair
import com.dweekly.cyrinx.chat.VirtualTimeSource
import com.dweekly.cyrinx.chat.toHexString
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import java.util.concurrent.atomic.AtomicLong

/**
 * Lifecycle-aware `StateFlow<ChatUiState>` boundary over an injected
 * [ChatTransportClient] (design brief: "ChatViewModel with StateFlow<ChatUiState>
 * ... lifecycle aware collection"). Owns the [SimulatedChatPair] (this stage's
 * only transport -- the live adapter arrives in C3-31 behind the same
 * [ChatTransportClient] contract), the single event-consumption loop that drives
 * [ChatProjection.reduce], and the two user-action entry points
 * ([sendMessage]/[connectToPeer]) the pinned projection calls out as mutating
 * state OUTSIDE the plain event stream (outgoing-message insertion at `send()`
 * acceptance; transient action errors).
 *
 * **Why this class owns a private [CoroutineScope] instead of using
 * `androidx.lifecycle.viewModelScope`:** `viewModelScope` resolves
 * `Dispatchers.Main.immediate` lazily, which requires either a real Android
 * `Looper` (instrumentation/Robolectric) or an explicit `Dispatchers.setMain(...)`
 * install before first use in a plain JVM unit test -- and even then, unifying
 * that installed Main dispatcher with a `kotlinx-coroutines-test` `TestScope`'s
 * OWN virtual-time scheduler (needed so `SimulatedChatPair`'s internal
 * `delay()`-based scenario timeline and this class's own collection loop drain
 * under the SAME `advanceUntilIdle()`) adds a second, easy-to-get-wrong
 * synchronization requirement on top of the virtual-clock plumbing
 * [SimulatedChatPair] already needs. Accepting [scope] as a constructor
 * parameter (defaulting to a real, ViewModel-owned scope in production) sidesteps
 * both problems: JVM tests pass a `TestScope` directly, with zero Main-dispatcher
 * ceremony, and production callers ([ChatViewModelFactory]) get a working default
 * for free. [onCleared] cancels [scope] directly rather than calling
 * `transportClient.stop()` first for a graceful teardown (CONTRACT.md section 2's
 * documented terminal-lifecycle sequence) -- DECISION (not pinned by the brief):
 * acceptable for this in-memory sample (no persisted state or pending I/O a
 * graceful `stop()` would protect), and cancelling the scope still tears down
 * every one of [SimulatedChatPair]'s own background jobs (they are children of
 * this same scope).
 */
class ChatViewModel(
    config: ChatLaunchConfig,
    timeSource: VirtualTimeSource = VirtualTimeSource { SystemClock.elapsedRealtime() },
    scope: CoroutineScope = CoroutineScope(SupervisorJob() + Dispatchers.Default),
    /** Non-null only for tests and the eventual C3-31 diagnostics-sheet export
     * (see ../../CONTRACT.md section 5's reserved `diagnosticsButton` ID and
     * DiagnosticsSheet.kt's placeholder). */
    private val modelTraceRecorder: ChatModelTraceRecorder? = null,
    /**
     * Test-only DI seam: when non-null, THIS client is used instead of
     * constructing a [SimulatedChatPair] from [config], and [config.simulated]
     * is ignored. Exists so JVM tests can drive [ChatViewModel]'s real
     * [applyEvent] collection loop with hand-built [ChatEvent]s -- in
     * particular [ChatEvent.MessageGap], which chatkit's own
     * [com.dweekly.cyrinx.chat.SimulatedChatTransportClient
     * .testOnlyInjectionSeam] cannot reach from this module: that property is
     * `internal` to `:chatkit`, and `:app` depends only on `:chatkit`'s public
     * API (`implementation(project(":chatkit"))`, no `-Xfriend-paths`),
     * matching this lane's read-only-`:chatkit` boundary. See
     * `FakeChatTransportClient` (app's own test module) and
     * ChatViewModelTest's messageGap-consumption tests. `null` (every
     * production call site, via [ChatViewModelFactory]) reproduces the
     * existing [SimulatedChatPair]-backed behavior exactly.
     */
    private val transportClientOverride: ChatTransportClient? = null,
    /** Paired with [transportClientOverride]: this client's own idHex to use
     * as [localPeerIdHex] when the override is active, since a fake transport
     * client has no PRNG-derived identity a real [SimulatedChatPair] would
     * supply. Ignored when [transportClientOverride] is null. */
    private val overrideLocalPeerIdHex: String? = null,
) : ViewModel() {
    private val ownedScope: CoroutineScope = scope
    private val timeSourceRef: VirtualTimeSource = timeSource

    private val _uiState = MutableStateFlow(ChatUiState())
    val uiState: StateFlow<ChatUiState> = _uiState.asStateFlow()

    /** `null` when [ChatLaunchConfig.simulated] is `false` -- the live SDK
     * adapter is not available until C3-31 (CONTRACT.md section 5:
     * "`chat.simulated`, Bool, default true ... false selects the live SDK
     * adapter -- not available until C3-31"). Rather than crashing or ignoring
     * the flag, this ViewModel still starts up with an explanatory
     * disconnected banner (see `NOT_YET_AVAILABLE_REASON` below). DECISION
     * (not pinned by the brief). */
    private val transportClient: ChatTransportClient?

    /** This client's own idHex -- [com.dweekly.cyrinx.chat.ChatMessage
     * .senderPeerIdHex] for messages THIS client sends. See
     * [withOutgoingQueuedIfAbsent]'s doc comment for why the ViewModel, not
     * the reducer, must supply this (only the transport-client OWNER knows its
     * own identity; [ChatTransportClient]'s interface deliberately does not
     * expose an `id` property, to stay adapter-agnostic for C3-31). */
    private val localPeerIdHex: String?

    init {
        if (transportClientOverride != null) {
            // Test-only seam -- see that parameter's doc comment. Skips
            // SimulatedChatPair entirely; the fake's own `start()` (a no-op)
            // still needs calling for interface parity with the production
            // path, but nothing depends on it actually doing anything.
            transportClient = transportClientOverride
            localPeerIdHex = overrideLocalPeerIdHex
            ownedScope.launch { transportClientOverride.start() }
            ownedScope.launch { transportClientOverride.events.collect(::applyEvent) }
        } else if (config.simulated) {
            val pair = SimulatedChatPair.create(config.scenario, config.seed, ownedScope, timeSourceRef)
            transportClient = pair.clientA
            localPeerIdHex = pair.clientA.id.toHexString()
            // Both clients of a simulated pair must start for discovery to arm
            // (CONTRACT.md section 2's "start() semantics (pinned)": "Discovery
            // is armed only once BOTH clients of a pair have started"). Client
            // B represents "the other simulated device" and is never otherwise
            // exposed outside this class -- the design brief's product scope is
            // a single local device's view of one conversation.
            ownedScope.launch { pair.clientA.start() }
            ownedScope.launch { pair.clientB.start() }
            ownedScope.launch { pair.clientA.events.collect(::applyEvent) }
        } else {
            transportClient = null
            localPeerIdHex = null
            // Bypasses ChatProjection.reduce (there is no ChatEvent driving
            // this -- it is a static, app-level condition evaluated once at
            // startup), so `banner` is set directly here rather than via
            // ChatBanner.mapReason: for this specific reason string, that
            // function's own "unknown reason -> raw string verbatim" fallback
            // would produce exactly this same value anyway (see
            // LIVE_TRANSPORT_NOT_YET_AVAILABLE_REASON's doc comment).
            _uiState.value =
                ChatUiState(
                    connection = ChatConnectionState.Disconnected(LIVE_TRANSPORT_NOT_YET_AVAILABLE_REASON),
                    banner = LIVE_TRANSPORT_NOT_YET_AVAILABLE_REASON,
                )
        }
    }

    /**
     * This client's own outgoing-message `sequence` counter, mirrored locally
     * because [ChatTransportClient.send] returns only `messageIdHex`
     * (CONTRACT.md section 1.8), never the assigned `sequence` -- so
     * [sendMessage] cannot read it off the send() result the way it reads
     * `idHex`. DECISION (not pinned by any brief available to this lane):
     * starts at `1` and increments by exactly `1` per ACCEPTED `send()` (the
     * increment below only runs after `client.send(body)` has already
     * returned successfully), mirroring CONTRACT.md section 2's own pinned
     * "Outgoing sequence assignment" rule for the transport's real counter
     * exactly -- both start at 1 and increment once per accepted send(), and
     * this sample has no reconnection-scope reset (CONTRACT.md section 2:
     * "there is no reconnection-scope reset in this sample") and only ever
     * calls `send()` through this one client (see this class's own doc
     * comment: "the design brief's product scope is a single local device's
     * view of one conversation"), so nothing else can advance the transport's
     * real counter out from under this mirror. [AtomicLong] because
     * [sendMessage] launches a new coroutine per call on [ownedScope], which
     * MAY run on a multi-threaded dispatcher (matches
     * SimulatedChatTransportClient's own "[scope] ... MAY be backed by a
     * genuinely multi-threaded dispatcher" note) -- though in practice
     * chatkit's own per-client `commandInFlight` guard already serializes
     * concurrent `send()` attempts to at most one accepted call at a time, so
     * this is defense in depth, not the only thing preventing a race. */
    private val nextOutgoingSequence = AtomicLong(1L)

    /** Applies one consumed [ChatEvent] via [ChatProjection.reduce] and records
     * the resulting projection to [modelTraceRecorder] (if attached). Uses
     * [MutableStateFlow.update]'s compare-and-swap loop (not a plain `.value =`
     * assignment) so this is safe to run concurrently with [sendMessage]'s/
     * [connectToPeer]'s own state updates -- see those functions' doc
     * comments for the ordering this makes irrelevant to correctness. Recording
     * happens exactly once, using the state from whichever lambda invocation
     * `update` actually committed (its LAST invocation, by construction of
     * `MutableStateFlow.update`'s retry loop), never once per retry. */
    private fun applyEvent(event: ChatEvent) {
        var committed: ChatUiState? = null
        _uiState.update { current ->
            val next = ChatProjection.reduce(current, event)
            committed = next
            next
        }
        modelTraceRecorder?.record(requireNotNull(committed), event.eventSeq)
    }

    /**
     * Design brief: "Send failures thrown synchronously from send() (...)
     * surface as a transient composer error string, not a message row." On
     * success, inserts the outgoing message row directly (design brief:
     * "Outgoing appended at send() acceptance") via
     * [withOutgoingQueuedIfAbsent] -- idempotent, so this is correct
     * regardless of whether [applyEvent] has already processed the
     * corresponding `messageStatusChanged(queued)` transport event by the time
     * this runs (in practice it has not: [ChatTransportClient.send]'s `Queued`
     * self-emission happens synchronously, with no suspension point, before
     * `send()` returns control to this coroutine, so this call reaches
     * [MutableStateFlow.update] before [applyEvent]'s own collector coroutine
     * gets a chance to be redispatched -- but this function does not rely on
     * that ordering for correctness, only for avoiding one harmless spurious
     * [ChatUiState.droppedStatusUpdates] tick in the improbable case it is
     * ever violated).
     */
    fun sendMessage(body: String) {
        val client = transportClient ?: return
        if (body.isEmpty()) return
        ownedScope.launch {
            try {
                val idHex = client.send(body)
                // Only advances after client.send() has already returned
                // successfully -- see [nextOutgoingSequence]'s doc comment for
                // why this mirrors, rather than reads, the transport's own
                // "per accepted send()" counter.
                val sequence = nextOutgoingSequence.getAndIncrement()
                val sentAtMs = timeSourceRef.nowMs()
                val senderIdHex = localPeerIdHex
                _uiState.update { current ->
                    val withMessage =
                        if (senderIdHex != null) {
                            current.withOutgoingQueuedIfAbsent(idHex, sequence, body, senderIdHex, sentAtMs)
                        } else {
                            current
                        }
                    withMessage.copy(transientError = null)
                }
            } catch (e: ChatTransportError) {
                _uiState.update { it.copy(transientError = e.message ?: "Send failed") }
            }
        }
    }

    /** Design brief's `connectButton` flow. [ChatUiState.connectedPeerIdHex] is
     * set only on acceptance (mirrors [sendMessage]'s pattern), so a rejected
     * `connect()` (unknown peer, concurrent command, terminal client) never
     * records a bogus "connected peer" for [ChatProjection]'s peerLost-recovery
     * rule to key off of. */
    fun connectToPeer(peerIdHex: String) {
        val client = transportClient ?: return
        ownedScope.launch {
            try {
                client.connect(peerIdHex)
                _uiState.update { it.copy(connectedPeerIdHex = peerIdHex, transientError = null) }
            } catch (e: ChatTransportError) {
                _uiState.update { it.copy(transientError = e.message ?: "Connect failed") }
            }
        }
    }

    fun disconnect() {
        val client = transportClient ?: return
        ownedScope.launch { client.disconnect() }
    }

    /** Clears [ChatUiState.transientError] once the UI has shown it (e.g. after
     * a snackbar dismiss/timeout). */
    fun dismissTransientError() {
        _uiState.update { it.copy(transientError = null) }
    }

    override fun onCleared() {
        ownedScope.cancel()
    }

    companion object {
        /** DECISION (not pinned by the brief): CONTRACT.md section 1.2's
         * `reason` vocabulary is free-text, and this condition ("live mode
         * requested, but C3-31 has not landed yet") is entirely this app's own
         * -- not one of the six simulated scenarios -- so it is written as a
         * direct human-readable sentence rather than a short code, relying on
         * [ChatBanner.mapReason]'s "unknown reason -> raw string verbatim"
         * fallback to surface it as-is. */
        const val LIVE_TRANSPORT_NOT_YET_AVAILABLE_REASON: String =
            "Live acoustic transport is not available yet (arrives in C3-31). This build supports the simulated transport only."
    }
}

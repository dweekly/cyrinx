package com.dweekly.cyrinx.chat

/**
 * Every virtual-time delay [SimulatedChatTransportClient] schedules, transcribed
 * from ../../../CONTRACT.md section 3's six scenario tables. Each constant is the
 * INCREMENTAL delay from the previous step in that scenario's coroutine chain (not
 * the table's absolute `t` column) -- the doc comment on each gives both the
 * incremental value used in code and the absolute `t (ms)` pair from the table it
 * was derived from, so the arithmetic is auditable against CONTRACT.md directly.
 *
 * Centralized here rather than inlined at each `delay(...)` call site per this
 * repo's "heuristic/spec-derived numbers must be centralized and documented"
 * convention (AGENTS.md).
 */
object ChatScenarioTimings {
    // -- Common to every scenario (all six tables) --------------------------

    /** `start()` call (t=0) -> `peerFound` (t=50). Section 3, every table's first
     * event row. */
    const val PEER_FOUND_DELAY_MS: Long = 50L

    /** `connect()` call (t=100 in every table) -> `connectionChanged(connecting)`
     * is emitted synchronously at the call itself (delta 0, not a scheduled
     * delay); see [SimulatedChatTransportClient.connect]. */
    const val CONNECTING_DELAY_MS: Long = 0L

    /** `connectionChanged(connecting)` -> `connectionChanged(connected)`: t=100 ->
     * t=150 in every table that calls `connect()`. */
    const val CONNECTED_DELAY_MS: Long = 50L

    /** `send()` call -> `messageStatusChanged(queued)` is emitted synchronously at
     * the call itself (delta 0); see [SimulatedChatTransportClient.send]. */
    const val QUEUED_DELAY_MS: Long = 0L

    /** `messageStatusChanged(queued)` -> `messageStatusChanged(transmitting)`:
     * +20ms in every scenario that calls `send()` (happyPair t=300->320,
     * sendFailure t=300->320, duplicateIncoming t=300->320, slowLink t=300->320). */
    const val TRANSMITTING_DELAY_MS: Long = 20L

    // -- happyPair (section 3.1) ---------------------------------------------

    /** `connected` (t=150) -> `linkBudgetChanged(text)` (t=200). */
    const val HAPPY_PAIR_LINK_BUDGET_DELAY_MS: Long = 50L
    const val HAPPY_PAIR_LINK_BUDGET_CONFIDENCE: Double = 0.7

    /** `transmitting` (t=320) -> `messageReceived` on B (t=380). */
    const val HAPPY_PAIR_DELIVER_DELAY_MS: Long = 60L

    /** `messageReceived` on B (t=380) -> `messageStatusChanged(delivered)` on A
     * (t=400). */
    const val HAPPY_PAIR_DELIVERED_DELAY_MS: Long = 20L

    // -- peerLoss (section 3.2) ------------------------------------------------

    /** `connected` (t=150) -> `connectionChanged(disconnected, peerSilenceTimeout)`
     * on both sides (t=500). */
    const val PEER_LOSS_SILENCE_TIMEOUT_DELAY_MS: Long = 350L

    /** `disconnected` (t=500) -> `peerLost` on both sides (t=510). */
    const val PEER_LOSS_PEER_LOST_DELAY_MS: Long = 10L

    // -- degradedThenRecovered (section 3.3) ------------------------------------

    /** `connected` (t=150) -> `linkBudgetChanged(text)` (t=200). Same delay/value
     * as [HAPPY_PAIR_LINK_BUDGET_DELAY_MS]; kept as a separate named constant so
     * each scenario's chain reads as a self-contained, independently auditable
     * transcription of its own CONTRACT.md table rather than cross-referencing
     * another scenario's constant. */
    const val DEGRADED_LINK_BUDGET_TEXT_DELAY_MS: Long = 50L
    const val DEGRADED_LINK_BUDGET_TEXT_CONFIDENCE: Double = 0.7

    /** `linkBudgetChanged(text)` (t=200) -> `connectionChanged(degraded)` (t=400). */
    const val DEGRADED_DEGRADE_DELAY_MS: Long = 200L

    /** `degraded` (t=400) -> `linkBudgetChanged(controlOnly)` (t=410). */
    const val DEGRADED_LINK_BUDGET_LOW_DELAY_MS: Long = 10L
    const val DEGRADED_LINK_BUDGET_LOW_CONFIDENCE: Double = 0.4

    /** `linkBudgetChanged(controlOnly)` (t=410) -> `connectionChanged(connected)`
     * again (t=700). */
    const val DEGRADED_RECOVER_DELAY_MS: Long = 290L

    /** Recovered `connected` (t=700) -> `linkBudgetChanged(text)` (t=710). */
    const val DEGRADED_LINK_BUDGET_RECOVERED_DELAY_MS: Long = 10L
    const val DEGRADED_LINK_BUDGET_RECOVERED_CONFIDENCE: Double = 0.65

    // -- sendFailure (section 3.4) ----------------------------------------------

    /** `transmitting` (t=320) -> `messageStatusChanged(failed)` (t=450). */
    const val SEND_FAILURE_FAILED_DELAY_MS: Long = 130L

    // -- duplicateIncoming (section 3.5) -----------------------------------------

    /** `transmitting` (t=320) -> first `messageReceived` on B (t=330). */
    const val DUPLICATE_DELIVER_DELAY_MS: Long = 10L

    /** First `messageReceived` (t=330) -> the fault-injected duplicate redelivery
     * of the SAME bytes (t=340), suppressed by B's messageId dedup -- no event. */
    const val DUPLICATE_REDELIVER_DELAY_MS: Long = 10L

    /** Duplicate redelivery (t=340) -> `messageStatusChanged(delivered)` on A
     * (t=400). */
    const val DUPLICATE_DELIVERED_DELAY_MS: Long = 60L

    // -- slowLink (section 3.6) ---------------------------------------------------

    /** `connected` (t=150) -> `linkBudgetChanged(controlOnly)` (t=160). */
    const val SLOW_LINK_LINK_BUDGET_DELAY_MS: Long = 10L
    const val SLOW_LINK_LINK_BUDGET_CONFIDENCE: Double = 0.5

    /** `transmitting` (t=320) -> `messageReceived` on B (t=2450). The scenario's
     * point: `transmitting` dwells 2130ms before delivery. */
    const val SLOW_LINK_DELIVER_DELAY_MS: Long = 2130L

    /** `messageReceived` (t=2450) -> `messageStatusChanged(delivered)` on A
     * (t=2500). */
    const val SLOW_LINK_DELIVERED_DELAY_MS: Long = 50L

    /** All `linkBudgetChanged` events in every scenario fire with `ageMs=0`
     * (freshly computed at emission time). Section 3, all tables. */
    const val LINK_BUDGET_FRESH_AGE_MS: Int = 0
}

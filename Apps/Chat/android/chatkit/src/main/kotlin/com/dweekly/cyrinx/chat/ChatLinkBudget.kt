package com.dweekly.cyrinx.chat

/**
 * Coarse directional link budget classification. ../../../CONTRACT.md section 1.3.
 *
 * DECISION (not pinned by the brief, per ../../../CONTRACT.md section 1.3): Kotlin
 * enum constant identifiers follow Kotlin's `UPPER_SNAKE_CASE` convention rather
 * than literally reproducing `controlOnly` as an identifier; [wireName] is the
 * cross-language-identical string actually used in traces and any serialized form.
 */
enum class LinkBudgetClass(val wireName: String) {
    CONTROL_ONLY("controlOnly"),
    TEXT("text"),
    THUMBNAIL("thumbnail"),
    BULK("bulk"),
}

/**
 * The numeric `LinkEstimate` backing the coarse [LinkBudgetClass] classification.
 * ../../../CONTRACT.md section 1.4.
 */
data class ChatLinkBudget(
    val classification: LinkBudgetClass,
    /** Conservative outbound lower bound; `null` when not yet estimated. */
    val txLowerBoundBps: Int?,
    /** Conservative inbound lower bound; `null` when not yet estimated. */
    val rxLowerBoundBps: Int?,
    /** `0.0..1.0`. */
    val confidence: Double,
    /** How stale this estimate is, in virtual/monotonic ms. */
    val ageMs: Int,
)

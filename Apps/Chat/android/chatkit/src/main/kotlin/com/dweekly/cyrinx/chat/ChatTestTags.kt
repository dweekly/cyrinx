package com.dweekly.cyrinx.chat

/**
 * Accessibility/test-tag identifiers, string values identical to Swift's
 * `ChatAccessibilityID`. ../../../CONTRACT.md section 5.
 */
object ChatTestTags {
    const val PEER_LIST: String = "chat.peerList"
    const val PEER_ROW: String = "chat.peerRow"
    const val CONNECT_BUTTON: String = "chat.connectButton"
    const val CONNECTION_BANNER: String = "chat.connectionBanner"
    const val LINK_BUDGET_BADGE: String = "chat.linkBudgetBadge"
    const val MESSAGE_LIST: String = "chat.messageList"
    const val MESSAGE_ROW: String = "chat.messageRow"
    const val MESSAGE_STATUS: String = "chat.messageStatus"
    const val COMPOSER_FIELD: String = "chat.composerField"
    const val SEND_BUTTON: String = "chat.sendButton"
    const val DIAGNOSTICS_BUTTON: String = "chat.diagnosticsButton"
    const val UNAUTHENTICATED_NOTICE: String = "chat.unauthenticatedNotice"
    const val ERROR_BANNER: String = "chat.errorBanner"

    /**
     * The C3-29 sequence amendment's `messageGap` caption row
     * (../../../CONTRACT.md section 1.7/2's "Gap surfacing (pinned)"):
     * "Messages missing: sequences X-Y", rendered inline in the message
     * list. Landed in CONTRACT.md's section 5 registry table via the C3-29
     * merge (Apple lane); this Android module only consumes the string
     * literal here -- the C3-30 Android chat app is what will actually tag
     * a composable with it.
     */
    const val MESSAGE_GAP_NOTICE: String = "chat.messageGapNotice"
}

/**
 * Launch/instrumentation argument keys. ../../../CONTRACT.md section 5. Android:
 * instrumentation args / `Intent` extras with these exact keys, e.g.
 * `am instrument ... -e chat.scenario happyPair`.
 */
object ChatLaunchArgs {
    /** String, one of the six scenario names in ../../../CONTRACT.md section 3. */
    const val SCENARIO: String = "chat.scenario"

    /** `uint64` decimal seed for the scenario's PRNG (../../../CONTRACT.md
     * section 2). */
    const val SEED: String = "chat.seed"

    /** `Bool`, default `true`. `false` selects the live SDK adapter -- not
     * available until C3-31; default stays `true` through C3-28-C3-30. */
    const val SIMULATED: String = "chat.simulated"
}

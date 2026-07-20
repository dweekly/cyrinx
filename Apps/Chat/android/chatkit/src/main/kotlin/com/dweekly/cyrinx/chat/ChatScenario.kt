package com.dweekly.cyrinx.chat

/**
 * The six pinned deterministic scenario scripts. ../../../CONTRACT.md section 3.
 * [wireName] is the exact string used for the `chat.scenario` launch argument
 * (../../../CONTRACT.md section 5).
 */
enum class ChatScenario(val wireName: String) {
    HAPPY_PAIR("happyPair"),
    PEER_LOSS("peerLoss"),
    DEGRADED_THEN_RECOVERED("degradedThenRecovered"),
    SEND_FAILURE("sendFailure"),
    DUPLICATE_INCOMING("duplicateIncoming"),
    SLOW_LINK("slowLink"),
    ;

    companion object {
        fun fromWireName(name: String): ChatScenario =
            entries.firstOrNull { it.wireName == name }
                ?: throw IllegalArgumentException("unknown chat scenario name: \"$name\"")
    }
}

package com.dweekly.cyrinx.chat

/**
 * Transport-level failure thrown by [ChatTransportClient] implementations for
 * misuse that is not one of [ChatEnvelopeError]'s six codec errors (for example,
 * `connect()` to a peer ID that does not match the only known peer). Plain thrown
 * exception, matching CONTRACT.md section 1.8's "Kotlin error signaling uses plain
 * thrown exceptions on `suspend fun`s" decision. DECISION (not pinned by the
 * brief): this exception type and its use sites are not named in CONTRACT.md.
 */
class ChatTransportError(message: String) : Exception(message) {
    companion object {
        /**
         * Message prefix identifying ../../../CONTRACT.md section 2's pinned
         * "Command ownership (pinned)" bullet's "concurrent-command
         * transport-misuse error": a public command
         * ([SimulatedChatTransportClient.start],
         * [SimulatedChatTransportClient.connect],
         * [SimulatedChatTransportClient.send],
         * [SimulatedChatTransportClient.cancelSend],
         * [SimulatedChatTransportClient.disconnect], or
         * [SimulatedChatTransportClient.stop]) was invoked while another
         * public command was still inside its own serialized span on the SAME
         * client instance -- "the owner ... invokes them strictly
         * sequentially, never concurrently. This is an enforced model, not an
         * honor rule." [isConcurrentCommand] lets a caller (or a test)
         * distinguish this specific rejection from any other
         * [ChatTransportError] (unknown peer, not-connected, terminal client,
         * ...) without string-matching the full message; this constant is the
         * single source of truth both [concurrentCommand] and
         * [isConcurrentCommand] share, rather than two independently
         * maintained literals that could drift apart.
         */
        const val CONCURRENT_COMMAND_PREFIX: String = "concurrent-command: "

        /**
         * Builds the dedicated concurrent-command [ChatTransportError] for a
         * public command rejected under [caller]'s own name (e.g.
         * `"send()"`) -- see [CONCURRENT_COMMAND_PREFIX].
         */
        fun concurrentCommand(caller: String): ChatTransportError =
            ChatTransportError(
                "$CONCURRENT_COMMAND_PREFIX$caller rejected: another public command is already " +
                    "in flight on this client instance (../../../CONTRACT.md section 2's \"Command " +
                    "ownership (pinned)\" bullet -- public commands are owned by one caller at a " +
                    "time, invoked strictly sequentially, never concurrently)",
            )
    }

    /**
     * True iff this is specifically CONTRACT.md's "concurrent-command
     * transport-misuse error" (see [CONCURRENT_COMMAND_PREFIX]), as opposed
     * to any other transport-misuse condition this class also signals via a
     * plain thrown [ChatTransportError] (unknown peer, not-connected,
     * terminal client, ...).
     */
    val isConcurrentCommand: Boolean
        get() = message?.startsWith(CONCURRENT_COMMAND_PREFIX) == true
}

package com.dweekly.cyrinx.chat

/**
 * Transport-level failure thrown by [ChatTransportClient] implementations for
 * misuse that is not one of [ChatEnvelopeError]'s six codec errors (for example,
 * `connect()` to a peer ID that does not match the only known peer). Plain thrown
 * exception, matching CONTRACT.md section 1.8's "Kotlin error signaling uses plain
 * thrown exceptions on `suspend fun`s" decision. DECISION (not pinned by the
 * brief): this exception type and its use sites are not named in CONTRACT.md.
 */
class ChatTransportError(message: String) : Exception(message)

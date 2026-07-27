package com.dweekly.cyrinx.chat

/**
 * Outgoing-message display status. ../../../CONTRACT.md section 1.5.
 *
 * Honest-delivery caveat (must appear in every surface that renders this status,
 * per the design brief): [Delivered] means **transport acknowledgment only** --
 * queue acceptance ([ChatTransportClient.send] returning a `messageIdHex`) is
 * never delivery, and transport acknowledgment is not authenticated identity (the
 * acoustic link has no authentication; see ../../../README.md's security notice).
 */
sealed class ChatMessageDisplayStatus {
    object Queued : ChatMessageDisplayStatus()

    object Transmitting : ChatMessageDisplayStatus()

    object Delivered : ChatMessageDisplayStatus()

    data class Failed(val reason: String) : ChatMessageDisplayStatus()

    /** Wire/trace string form used in the JSON-lines trace's `status` field.
     * ../../../CONTRACT.md section 4. */
    val wireName: String
        get() =
            when (this) {
                Queued -> "queued"
                Transmitting -> "transmitting"
                Delivered -> "delivered"
                is Failed -> "failed"
            }
}

/**
 * A single chat message, incoming or outgoing. ../../../CONTRACT.md section 1.6.
 *
 * [id] is defensively copied on the way in and on the way out (every read returns
 * a fresh copy), same rationale as [ChatPeer.id]: a mutable `ByteArray` exposed by
 * reference would let a caller corrupt this instance's identity out from under
 * equality/hashing/lookup.
 */
class ChatMessage(
    /** Same value as the envelope's `messageId`. */
    id: ByteArray,
    /** The envelope's `sequence` field (../../../ENVELOPE.md section 1.2):
     * nonzero, sender-local, strictly increasing within the sending client's
     * current connection scope -- the real cross-message ordering evidence, NOT
     * [sentAtWallClockMs]. Represented as the exact `u64` wire bit pattern (see
     * [ChatEnvelope]'s doc comment). */
    val sequence: Long,
    val direction: Direction,
    /** Decoded UTF-8 text. */
    val body: String,
    /** Lowercase hex. */
    val senderPeerIdHex: String,
    /** Display metadata only -- see ../../../ENVELOPE.md section 6. Peer clocks are
     * not synchronized; never use this for cross-peer ordering. */
    val sentAtWallClockMs: Long,
    val status: ChatMessageDisplayStatus,
) {
    private val idBytes: ByteArray = id.copyOf()

    val id: ByteArray
        get() = idBytes.copyOf()

    enum class Direction(val wireName: String) {
        INCOMING("incoming"),
        OUTGOING("outgoing"),
    }

    override fun equals(other: Any?): Boolean =
        other is ChatMessage &&
            idBytes.contentEquals(other.idBytes) &&
            sequence == other.sequence &&
            direction == other.direction &&
            body == other.body &&
            senderPeerIdHex == other.senderPeerIdHex &&
            sentAtWallClockMs == other.sentAtWallClockMs &&
            status == other.status

    override fun hashCode(): Int = idBytes.contentHashCode()

    override fun toString(): String =
        "ChatMessage(id=${idBytes.toHexString()}, sequence=${sequence.toULong()}, direction=$direction, " +
            "body.length=${body.length}, senderPeerIdHex=$senderPeerIdHex, " +
            "sentAtWallClockMs=$sentAtWallClockMs, status=$status)"
}

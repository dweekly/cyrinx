package com.dweekly.cyrinx.chat

/**
 * An ephemeral, unauthenticated transport peer. ../../../CONTRACT.md section 1.1.
 *
 * Equality is by [id] only -- two [ChatPeer] values with the same [id] but a stale
 * [discoveredAtMs] are still "the same peer" for dictionary/set purposes;
 * `peerUpdated` exists precisely to carry a refreshed value for an already-known
 * [id].
 */
class ChatPeer(val id: ByteArray, val discoveredAtMs: Long) {
    /** `"Peer-" + uppercase-hex(id[0:2])`, e.g. id starting `0xb1 0xa2...` ->
     * `"Peer-B1A2"`. Purely cosmetic; never used for equality or lookup. */
    val displayName: String
        get() = "Peer-" + id.take(2).joinToString("") { "%02X".format(it) }

    override fun equals(other: Any?): Boolean = other is ChatPeer && id.contentEquals(other.id)

    override fun hashCode(): Int = id.contentHashCode()

    override fun toString(): String =
        "ChatPeer($displayName, id=${id.toHexString()}, discoveredAtMs=$discoveredAtMs)"
}

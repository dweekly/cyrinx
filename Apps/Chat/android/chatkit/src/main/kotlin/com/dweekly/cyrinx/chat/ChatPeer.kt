package com.dweekly.cyrinx.chat

/**
 * An ephemeral, unauthenticated transport peer. ../../../CONTRACT.md section 1.1.
 *
 * Equality is by [id] only -- two [ChatPeer] values with the same [id] but a stale
 * [discoveredAtMs] are still "the same peer" for dictionary/set purposes;
 * `peerUpdated` exists precisely to carry a refreshed value for an already-known
 * [id].
 *
 * [id] is defensively copied on the way in (the constructor argument's backing
 * array is never aliased) and on the way out (every [id] read returns a fresh
 * copy) so a caller holding a reference to either array cannot mutate this
 * instance's internal state -- `ByteArray` is mutable in both Kotlin and Java, and
 * [id] is exactly the kind of opaque-identity value (used for equality, hashing,
 * and cross-client lookup) that must not be corruptible through an aliased
 * reference.
 */
class ChatPeer(id: ByteArray, val discoveredAtMs: Long) {
    private val idBytes: ByteArray = id.copyOf()

    val id: ByteArray
        get() = idBytes.copyOf()

    /** `"Peer-" + uppercase-hex(id[0:2])`, e.g. id starting `0xb1 0xa2...` ->
     * `"Peer-B1A2"`. Purely cosmetic; never used for equality or lookup. */
    val displayName: String
        get() = "Peer-" + idBytes.take(2).joinToString("") { "%02X".format(it) }

    override fun equals(other: Any?): Boolean = other is ChatPeer && idBytes.contentEquals(other.idBytes)

    override fun hashCode(): Int = idBytes.contentHashCode()

    override fun toString(): String =
        "ChatPeer($displayName, id=${idBytes.toHexString()}, discoveredAtMs=$discoveredAtMs)"
}

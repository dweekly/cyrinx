package com.dweekly.cyrinx.chat

/**
 * SplitMix64, the seeded PRNG pinned for the simulated chat client
 * (../../../CONTRACT.md section 2, point 4). Public-domain reference algorithm:
 * Vigna, `splitmix64.c` <http://prng.di.unimi.it/splitmix64.c>; also the generator
 * from Steele, Lea & Flood, "Fast Splittable Pseudorandom Number Generators,"
 * OOPSLA 2014.
 *
 * `state` is a Kotlin `Long` used purely as a 64-bit bit-pattern container: all
 * arithmetic below (`+`, `*`) is taken modulo 2^64, which is exactly what JVM `Long`
 * addition/multiplication already does (two's-complement wraparound is bit-for-bit
 * identical whether the pattern is interpreted as signed or unsigned), and all
 * right-shifts use `ushr` (logical/unsigned shift), matching the reference
 * algorithm's unsigned `>>`. No behavior here depends on `Long` being signed.
 *
 * Constructed directly from the scenario's `seed` with no additional hashing of the
 * seed itself, per ../../../CONTRACT.md section 2.
 */
class SplitMix64(seed: Long) {
    // Per-call state increment ("golden gamma"), pinned in CONTRACT.md section 2.
    // Hex literal written via the ULong->Long bit-reinterpreting conversion because
    // 0x9E3779B97F4A7C15 has its top bit set and does not fit as a positive Kotlin
    // Long literal.
    private var state: Long = seed

    /** One u64 draw, per the reference `next()` step pinned in
     * ../../../CONTRACT.md section 2. */
    fun next(): Long {
        state += GOLDEN_GAMMA
        var z = state
        z = (z xor (z ushr 30)) * MIX_MUL_1
        z = (z xor (z ushr 27)) * MIX_MUL_2
        z = z xor (z ushr 31)
        return z
    }

    companion object {
        // Written as ULong hex literals then bit-reinterpreted to Long via toLong()
        // (rather than hand-converted to a signed decimal constant) so the source
        // shows the exact hex value from CONTRACT.md section 2 with no manual
        // two's-complement arithmetic to get wrong.

        /** Per-call state increment. ../../../CONTRACT.md section 2. */
        val GOLDEN_GAMMA: Long = 0x9E3779B97F4A7C15uL.toLong()

        /** First avalanche multiplier. ../../../CONTRACT.md section 2. */
        val MIX_MUL_1: Long = 0xBF58476D1CE4E5B9uL.toLong()

        /** Second avalanche multiplier. ../../../CONTRACT.md section 2. */
        val MIX_MUL_2: Long = 0x94D049BB133111EBuL.toLong()
    }
}

/** Encodes a `Long` as its 8-byte big-endian representation, matching "Draw N -> 8
 * bytes (big-endian) of the u64 result" in ../../../CONTRACT.md section 2. */
fun Long.toBigEndianBytes(): ByteArray {
    val out = ByteArray(8)
    for (i in 0 until 8) {
        out[i] = ((this ushr (8 * (7 - i))) and 0xFF).toByte()
    }
    return out
}

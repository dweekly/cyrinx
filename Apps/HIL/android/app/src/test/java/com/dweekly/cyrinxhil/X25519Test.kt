package com.dweekly.cyrinxhil

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Test

class X25519Test {
    private fun hex(s: String) = ByteArray(s.length / 2) { s.substring(2 * it, 2 * it + 2).toInt(16).toByte() }

    /** RFC 7748 section 5.2, first test vector. The scalar is given unclamped. */
    @Test
    fun matchesRfc7748VectorFromAnUnclampedScalar() {
        val scalar = hex("a546e36bf0527c9d3b16154b82465edd62144c0ac1fc5a18506a2244ba449ac4")
        val u = hex("e6db6867583030db3594c1a424b15f7c726624ec26b3353b10a903a6d0ab1c4c")
        val expected = hex("c3da55379de9c6908e94ea4df28d084f32eccf03491c71f754b4075577a28552")
        assertArrayEquals(expected, X25519.scalarMult(scalar, u))
    }

    /** RFC 7748 section 5.2, second vector: u has its top bit set and it must be ignored. */
    @Test
    fun masksTheTopBitOfU() {
        val scalar = hex("4b66e9d4d1b4673c5ad22691957d6af5c11b6421e0ea01d42ca4169e7918ba0d")
        val u = hex("e5210f12786811d3f4b7959d0538ae2c31dbe7106fc03c3efc4cd549c715a493")
        val expected = hex("95cbde9476e8907d7aade45cb4b873f88b595a68799fa152e6f8f7647aac7957")
        assertArrayEquals(expected, X25519.scalarMult(scalar, u))
    }

    /** An all-zero scalar (the config default when the envelope is off) used to throw. */
    @Test
    fun allZeroScalarDoesNotThrow() {
        val u = hex("e6db6867583030db3594c1a424b15f7c726624ec26b3353b10a903a6d0ab1c4c")
        assertEquals(32, X25519.scalarMult(ByteArray(32), u).size)
    }
}

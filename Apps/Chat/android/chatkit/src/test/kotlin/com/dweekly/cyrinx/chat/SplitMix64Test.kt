package com.dweekly.cyrinx.chat

import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * Known-answer tests for [SplitMix64] against a from-scratch Python reference
 * implementation of the exact algorithm pinned in ../../../CONTRACT.md section 2
 * (verified separately, per this repo's "write small test programs to validate a
 * hypothesis" convention, to reproduce the widely cited public splitmix64 test
 * vector seed=1234567 -> first draw 6457827717110365317 -- see the C3-28
 * spec-stage report). Expected values are written as `UL` hex literals (the exact
 * bit pattern) compared via [ULong] since several exceed [Long.MAX_VALUE].
 */
class SplitMix64Test {
    @Test
    fun seed0FirstThreeDraws() {
        val rng = SplitMix64(0L)
        assertEquals(0xE220A8397B1DCDAFuL, rng.next().toULong())
        assertEquals(0x6E789E6AA1B965F4uL, rng.next().toULong())
        assertEquals(0x06C45D188009454FuL, rng.next().toULong())
    }

    @Test
    fun seed42FirstTwoDraws() {
        val rng = SplitMix64(42L)
        assertEquals(0xBDD732262FEB6E95uL, rng.next().toULong())
        assertEquals(0x28EFE333B266F103uL, rng.next().toULong())
    }

    @Test
    fun seed1234567FirstThreeDraws() {
        // The specific, widely cited splitmix64 known-answer test: seed 1234567's
        // first draw is 6457827717110365317 (0x599ed017fb08fc85).
        val rng = SplitMix64(1234567L)
        assertEquals(0x599ED017FB08FC85uL, rng.next().toULong())
        assertEquals(0x2C73F08458540FA5uL, rng.next().toULong())
        assertEquals(0x883EBCE5A3F27C77uL, rng.next().toULong())
    }

    @Test
    fun sameSeedProducesIdenticalSequence() {
        val a = SplitMix64(99L)
        val b = SplitMix64(99L)
        repeat(10) {
            assertEquals(a.next(), b.next())
        }
    }

    @Test
    fun differentSeedsProduceDifferentFirstDraws() {
        assertEquals(false, SplitMix64(1L).next() == SplitMix64(2L).next())
    }

    @Test
    fun toBigEndianBytesRoundTripsKnownValue() {
        // 0x0102030405060708 -> bytes [01 02 03 04 05 06 07 08].
        val bytes = 0x0102030405060708L.toBigEndianBytes()
        assertEquals("0102030405060708", bytes.toHexString())
    }
}

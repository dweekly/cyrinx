package com.dweekly.cyrinx.chat.app

import com.dweekly.cyrinx.chat.ChatLaunchArgs
import com.dweekly.cyrinx.chat.ChatScenario
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** [ChatLaunchConfig.fromArgs] is the plain-Kotlin core exercised here with a
 * fake `(String) -> String?` lookup -- no `android.os.Bundle`/`Intent`
 * involved (see that class's doc comment for why). */
class ChatLaunchConfigTest {
    @Test
    fun emptyArgsYieldTheDocumentedDefaults() {
        val config = ChatLaunchConfig.fromArgs { null }
        assertEquals(ChatLaunchConfig.DEFAULT, config)
        assertEquals(ChatScenario.HAPPY_PAIR, config.scenario)
        assertEquals(1L, config.seed)
        assertTrue(config.simulated)
    }

    @Test
    fun parsesAllThreeArgumentsByTheirPinnedKeys() {
        val args =
            mapOf(
                ChatLaunchArgs.SCENARIO to "peerLoss",
                ChatLaunchArgs.SEED to "42",
                ChatLaunchArgs.SIMULATED to "false",
            )
        val config = ChatLaunchConfig.fromArgs { key -> args[key] }

        assertEquals(ChatScenario.PEER_LOSS, config.scenario)
        assertEquals(42L, config.seed)
        assertFalse(config.simulated)
    }

    @Test
    fun seedParsesTheFullUnsignedSixtyFourBitRange() {
        // CONTRACT.md section 5: "chat.seed: UInt64 decimal." 2^64 - 1 exceeds
        // Long.MAX_VALUE and must still round-trip as a raw 64-bit pattern.
        val maxU64Decimal = "18446744073709551615"
        val config = ChatLaunchConfig.fromArgs { key -> if (key == ChatLaunchArgs.SEED) maxU64Decimal else null }

        assertEquals(-1L, config.seed) // 0xFFFFFFFFFFFFFFFF as a signed Long bit pattern.
    }

    @Test
    fun everyScenarioWireNameRoundTrips() {
        for (scenario in ChatScenario.entries) {
            val config = ChatLaunchConfig.fromArgs { key -> if (key == ChatLaunchArgs.SCENARIO) scenario.wireName else null }
            assertEquals(scenario, config.scenario)
        }
    }
}

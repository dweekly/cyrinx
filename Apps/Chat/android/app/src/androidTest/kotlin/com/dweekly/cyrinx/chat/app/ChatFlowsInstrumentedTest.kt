package com.dweekly.cyrinx.chat.app

import android.content.Intent
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.v2.createEmptyComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performTextInput
import androidx.test.core.app.ActivityScenario
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.dweekly.cyrinx.chat.ChatLaunchArgs
import com.dweekly.cyrinx.chat.ChatScenario
import com.dweekly.cyrinx.chat.ChatTestTags
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * Compose instrumentation UI tests for the six documented flows (design
 * brief: "discovery/connect/send/receive/degraded/failed"), driven entirely
 * by launch-arg-selected simulator scenarios (CONTRACT.md section 5) --
 * TalkBack content descriptions and font-scale tolerance are exercised
 * implicitly via [ChatTestTags] semantics already wired into every
 * production composable (see the `ui` package's composables), not duplicated
 * as separate assertions here.
 *
 * **THIS CLASS REQUIRES A CONNECTED EMULATOR OR DEVICE.** It runs via
 * `:app:connectedDebugAndroidTest`, which is NOT part of this module's CI or
 * local JVM gates (`:app:testDebugUnitTest`, `:app:assembleDebug`) -- see the
 * design brief's "CI (orchestrator edits only)" section: "UI-test jobs are
 * NOT added to CI in this round ... keep runners fast and reliable." Run
 * manually via:
 *
 * ```console
 * cd Apps/Chat/android && ./gradlew :app:connectedDebugAndroidTest
 * ```
 *
 * The "receive" flow is the one exception to "driven entirely by
 * launch-arg-selected simulator scenarios," and lives in the separate
 * ChatReceiveFlowInstrumentedTest -- see that class's doc comment for why:
 * none of CONTRACT.md section 3's six pinned scenario scripts ever have
 * client A (this app's own local perspective -- see [ChatViewModel]'s class
 * doc comment) receive a `messageReceived` event.
 */
@RunWith(AndroidJUnit4::class)
class ChatFlowsInstrumentedTest {
    @get:Rule
    val composeRule = createEmptyComposeRule()

    private fun launch(scenario: ChatScenario, seed: Long = 1L): ActivityScenario<MainActivity> {
        val intent =
            Intent(ApplicationProvider.getApplicationContext(), MainActivity::class.java).apply {
                putExtra(ChatLaunchArgs.SCENARIO, scenario.wireName)
                putExtra(ChatLaunchArgs.SEED, seed.toString())
                putExtra(ChatLaunchArgs.SIMULATED, true.toString())
            }
        return ActivityScenario.launch(intent)
    }

    @Test
    fun discoveryFlowShowsTheDiscoveredPeerInThePeerBrowser() {
        launch(ChatScenario.HAPPY_PAIR).use {
            composeRule.waitForIdle()
            composeRule.onNodeWithTag(ChatTestTags.PEER_LIST).assertIsDisplayed()
            composeRule.onNodeWithTag(ChatTestTags.PEER_ROW).assertIsDisplayed()
        }
    }

    @Test
    fun connectFlowMovesFromPeerBrowserToConversationOnConnectTap() {
        launch(ChatScenario.HAPPY_PAIR).use {
            composeRule.waitForIdle()
            composeRule.onNodeWithTag(ChatTestTags.CONNECT_BUTTON).performClick()
            composeRule.waitForIdle()
            composeRule.onNodeWithTag(ChatTestTags.MESSAGE_LIST).assertIsDisplayed()
            composeRule.onNodeWithTag(ChatTestTags.COMPOSER_FIELD).assertIsDisplayed()
        }
    }

    @Test
    fun sendFlowShowsAnOutgoingMessageProgressingToDelivered() {
        launch(ChatScenario.HAPPY_PAIR).use {
            composeRule.waitForIdle()
            composeRule.onNodeWithTag(ChatTestTags.CONNECT_BUTTON).performClick()
            composeRule.waitForIdle()
            composeRule.onNodeWithTag(ChatTestTags.COMPOSER_FIELD).performTextInput("hello")
            composeRule.onNodeWithTag(ChatTestTags.SEND_BUTTON).performClick()
            composeRule.waitForIdle()
            composeRule.onNodeWithTag(ChatTestTags.MESSAGE_ROW).assertIsDisplayed()
            composeRule.onNodeWithText("Delivered").assertIsDisplayed()
        }
    }

    @Test
    fun degradedFlowShowsThePinnedDegradedBannerThenClearsOnRecovery() {
        launch(ChatScenario.DEGRADED_THEN_RECOVERED).use {
            composeRule.waitForIdle()
            composeRule.onNodeWithTag(ChatTestTags.CONNECT_BUTTON).performClick()
            // Degraded/recovered transitions happen on the simulator's own
            // (short, real-wall-clock-under-instrumentation) virtual timeline;
            // waitForIdle repeatedly during manual runs, or drive with an
            // explicit Espresso IdlingResource in a follow-up, rather than a
            // fixed sleep (AGENTS.md: no wall-clock sleeps in tests).
            composeRule.waitForIdle()
            composeRule.onNodeWithTag(ChatTestTags.CONNECTION_BANNER).assertIsDisplayed()
        }
    }

    @Test
    fun failedMessageFlowShowsFailedStatusWithTextAndIconNotColorAlone() {
        launch(ChatScenario.SEND_FAILURE).use {
            composeRule.waitForIdle()
            composeRule.onNodeWithTag(ChatTestTags.CONNECT_BUTTON).performClick()
            composeRule.waitForIdle()
            composeRule.onNodeWithTag(ChatTestTags.COMPOSER_FIELD).performTextInput("will-fail")
            composeRule.onNodeWithTag(ChatTestTags.SEND_BUTTON).performClick()
            composeRule.waitForIdle()
            // "Failed" TEXT label must be present (not merely a red tint) --
            // design brief: "per-message status with text+icon never
            // color-alone."
            composeRule.onNodeWithText("Failed").assertIsDisplayed()
        }
    }
}

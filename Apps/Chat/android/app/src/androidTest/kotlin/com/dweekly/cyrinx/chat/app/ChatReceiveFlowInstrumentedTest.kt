package com.dweekly.cyrinx.chat.app

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.dweekly.cyrinx.chat.ChatMessage
import com.dweekly.cyrinx.chat.ChatMessageDisplayStatus
import com.dweekly.cyrinx.chat.app.ui.ChatTheme
import com.dweekly.cyrinx.chat.app.ui.MessageList
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * The "receive" flow (design brief: "discovery/connect/send/receive/degraded/
 * failed"), exercised directly against `ui.MessageList` rather than through a
 * full scenario-driven [MainActivity] launch (unlike every test in
 * ChatFlowsInstrumentedTest, which shares this file's sibling): none of
 * CONTRACT.md section 3's six pinned scenario scripts ever have client A
 * (this app's own local perspective -- see [ChatViewModel]'s class doc
 * comment) receive a `messageReceived` event; every scenario scripts client A
 * as the sender and client B as the passive receiver. A live two-device
 * conversation (C3-31) will exercise A-receives naturally; until then, this
 * is the most honest thing this single-process, simulated-only build can
 * demonstrate for the incoming-message bubble rendering.
 *
 * A separate class (rather than a test in ChatFlowsInstrumentedTest) because
 * it needs [createComposeRule] (which hosts its own blank activity and
 * exposes `setContent`) instead of [androidx.compose.ui.test.junit4
 * .createEmptyComposeRule] (which hosts nothing, letting
 * ChatFlowsInstrumentedTest's own tests launch [MainActivity] directly via a
 * custom `Intent`) -- the two rule flavors do not mix within one test class.
 *
 * **THIS CLASS REQUIRES A CONNECTED EMULATOR OR DEVICE**, same as its sibling
 * -- see that class's doc comment for the manual run command.
 */
@RunWith(AndroidJUnit4::class)
class ChatReceiveFlowInstrumentedTest {
    @get:Rule
    val composeRule = createComposeRule()

    @Test
    fun receiveFlowShowsAnIncomingMessageBubble() {
        composeRule.setContent {
            ChatTheme {
                MessageList(
                    messages =
                        listOf(
                            ChatMessage(
                                id = byteArrayOf(0x01, 0x02, 0x03, 0x04),
                                direction = ChatMessage.Direction.INCOMING,
                                body = "hi from the other device",
                                senderPeerIdHex = "01020304",
                                sentAtWallClockMs = 0,
                                status = ChatMessageDisplayStatus.Delivered,
                            ),
                        ),
                )
            }
        }
        composeRule.onNodeWithText("hi from the other device").assertIsDisplayed()
    }
}

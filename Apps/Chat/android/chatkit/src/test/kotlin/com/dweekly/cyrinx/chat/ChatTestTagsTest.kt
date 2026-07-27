package com.dweekly.cyrinx.chat

import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * Verbatim conformance of [ChatTestTags]/[ChatLaunchArgs] to
 * ../../../CONTRACT.md section 5's registry table -- both platforms must
 * share these exact string values, since they cross into UI-test/
 * instrumentation tooling that matches on the literal string, not the
 * Swift/Kotlin identifier. Twin of Swift's `ChatAccessibilityIDTests`
 * (Apps/Chat/CyrinxChatKit/Tests/CyrinxChatKitTests/ChatAccessibilityIDTests.swift).
 */
class ChatTestTagsTest {
    /** CONTRACT.md section 5's accessibility-identifier table, transcribed
     * verbatim as (constant name, actual value, expected value) triples. */
    private val expectedTestTags =
        listOf(
            Triple("PEER_LIST", ChatTestTags.PEER_LIST, "chat.peerList"),
            Triple("PEER_ROW", ChatTestTags.PEER_ROW, "chat.peerRow"),
            Triple("CONNECT_BUTTON", ChatTestTags.CONNECT_BUTTON, "chat.connectButton"),
            Triple("CONNECTION_BANNER", ChatTestTags.CONNECTION_BANNER, "chat.connectionBanner"),
            Triple("LINK_BUDGET_BADGE", ChatTestTags.LINK_BUDGET_BADGE, "chat.linkBudgetBadge"),
            Triple("MESSAGE_LIST", ChatTestTags.MESSAGE_LIST, "chat.messageList"),
            Triple("MESSAGE_ROW", ChatTestTags.MESSAGE_ROW, "chat.messageRow"),
            Triple("MESSAGE_STATUS", ChatTestTags.MESSAGE_STATUS, "chat.messageStatus"),
            Triple("COMPOSER_FIELD", ChatTestTags.COMPOSER_FIELD, "chat.composerField"),
            Triple("SEND_BUTTON", ChatTestTags.SEND_BUTTON, "chat.sendButton"),
            Triple("DIAGNOSTICS_BUTTON", ChatTestTags.DIAGNOSTICS_BUTTON, "chat.diagnosticsButton"),
            Triple(
                "UNAUTHENTICATED_NOTICE",
                ChatTestTags.UNAUTHENTICATED_NOTICE,
                "chat.unauthenticatedNotice",
            ),
            Triple("ERROR_BANNER", ChatTestTags.ERROR_BANNER, "chat.errorBanner"),
            Triple("MESSAGE_GAP_NOTICE", ChatTestTags.MESSAGE_GAP_NOTICE, "chat.messageGapNotice"),
        )

    @Test
    fun eachTestTagMatchesContractTableVerbatim() {
        for ((name, actual, expected) in expectedTestTags) {
            assertEquals("ChatTestTags.$name", expected, actual)
        }
    }

    @Test
    fun testTagRegistryHasExactlyTheFourteenConstantsContractTableLists() {
        assertEquals(14, expectedTestTags.size)
    }

    /** CONTRACT.md section 5's launch/instrumentation-argument table,
     * transcribed verbatim. */
    private val expectedLaunchArgs =
        listOf(
            Triple("SCENARIO", ChatLaunchArgs.SCENARIO, "chat.scenario"),
            Triple("SEED", ChatLaunchArgs.SEED, "chat.seed"),
            Triple("SIMULATED", ChatLaunchArgs.SIMULATED, "chat.simulated"),
        )

    @Test
    fun eachLaunchArgMatchesContractTableVerbatim() {
        for ((name, actual, expected) in expectedLaunchArgs) {
            assertEquals("ChatLaunchArgs.$name", expected, actual)
        }
    }
}

package com.dweekly.cyrinx.chat.app.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import com.dweekly.cyrinx.chat.ChatMessage
import com.dweekly.cyrinx.chat.ChatTestTags
import com.dweekly.cyrinx.chat.app.ChatMessageGapNotice
import com.dweekly.cyrinx.chat.toHexString

/**
 * `chat.messageGapNotice` accessibility/test-tag identifier for a consumed
 * `messageGap` event's system caption row (see [GapNoticeRow]) -- orchestrator
 * pin 2: the shared registry constant of this exact name is added to
 * CONTRACT.md section 5's table and to Swift `ChatAccessibilityID`/Kotlin
 * `ChatTestTags` ONLY in the Apple lane's worktree (lands first); this Android
 * lane consumes the literal string here because `:chatkit`'s `ChatTestTags`
 * (this lane's read-only dependency) does not have the constant yet -- it
 * arrives via the C3-29 merge, at which point this literal should be replaced
 * with `ChatTestTags.MESSAGE_GAP_NOTICE` (or whatever identifier name that
 * merge lands).
 */
private const val MESSAGE_GAP_NOTICE_TEST_TAG: String = "chat.messageGapNotice"

/** One row this list can render, in display order: either a real
 * [ChatMessage] or a [ChatMessageGapNotice]'s system caption -- pin 3: "the
 * caption is a message-list row with the new accessibility tag," not a
 * banner. Built by [interleaveWithGapNotices]. */
private sealed class ChatMessageListRow {
    data class MessageRow(val message: ChatMessage) : ChatMessageListRow()

    data class GapNoticeRow(val notice: ChatMessageGapNotice) : ChatMessageListRow()
}

/** Interleaves [gapNotices] into [messages] by each notice's own
 * [ChatMessageGapNotice.afterMessageCount] -- e.g. `afterMessageCount == 0`
 * renders before the first message row; `afterMessageCount == messages.size`
 * renders after the last. Multiple notices anchored at the same position
 * render in their own (already consumption-ordered) relative order. */
private fun interleaveWithGapNotices(
    messages: List<ChatMessage>,
    gapNotices: List<ChatMessageGapNotice>,
): List<ChatMessageListRow> {
    if (gapNotices.isEmpty()) return messages.map { ChatMessageListRow.MessageRow(it) }
    val noticesByPosition = gapNotices.groupBy { it.afterMessageCount }
    val rows = mutableListOf<ChatMessageListRow>()
    noticesByPosition[0]?.forEach { rows.add(ChatMessageListRow.GapNoticeRow(it)) }
    messages.forEachIndexed { index, message ->
        rows.add(ChatMessageListRow.MessageRow(message))
        noticesByPosition[index + 1]?.forEach { rows.add(ChatMessageListRow.GapNoticeRow(it)) }
    }
    return rows
}

/** The one conversation's message list (design brief: "message list with
 * per-message status"), interleaved with any [gapNotices] system captions
 * (sequence amendment; see [interleaveWithGapNotices]). Auto-scrolls to the
 * newest row as the list grows. [gapNotices] defaults to empty so existing
 * callers that only ever showed [messages] (no gap has ever been consumed)
 * are unaffected. */
@Composable
fun MessageList(
    messages: List<ChatMessage>,
    gapNotices: List<ChatMessageGapNotice> = emptyList(),
    modifier: Modifier = Modifier,
) {
    val rows = interleaveWithGapNotices(messages, gapNotices)
    val listState = rememberLazyListState()
    LaunchedEffect(rows.size) {
        if (rows.isNotEmpty()) listState.animateScrollToItem(rows.size - 1)
    }
    LazyColumn(
        state = listState,
        modifier = modifier.testTag(ChatTestTags.MESSAGE_LIST),
        contentPadding = PaddingValues(vertical = 8.dp),
    ) {
        itemsIndexed(
            rows,
            key = { index, row ->
                when (row) {
                    is ChatMessageListRow.MessageRow -> row.message.id.toHexString()
                    is ChatMessageListRow.GapNoticeRow -> "gapNotice-$index"
                }
            },
        ) { _, row ->
            when (row) {
                is ChatMessageListRow.MessageRow -> MessageRow(row.message)
                is ChatMessageListRow.GapNoticeRow -> GapNoticeRow(row.notice)
            }
        }
    }
}

/** The sequence amendment's system caption row for one consumed `messageGap`
 * event -- pin 3: "not a banner," pin 1: "TalkBack description" required
 * alongside the literal [MESSAGE_GAP_NOTICE_TEST_TAG]. Icon + text together,
 * matching this file's/[ConnectionBanner]'s own "never color/icon alone"
 * convention. */
@Composable
private fun GapNoticeRow(notice: ChatMessageGapNotice) {
    Box(
        modifier =
            Modifier
                .fillMaxWidth()
                .padding(horizontal = 16.dp, vertical = 6.dp)
                .testTag(MESSAGE_GAP_NOTICE_TEST_TAG)
                .semantics(mergeDescendants = true) { contentDescription = notice.text },
        contentAlignment = Alignment.Center,
    ) {
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            Icon(
                Icons.Filled.Warning,
                contentDescription = null,
                tint = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(bottom = 2.dp),
            )
            Text(
                text = notice.text,
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

@Composable
private fun MessageRow(message: ChatMessage) {
    val isOutgoing = message.direction == ChatMessage.Direction.OUTGOING
    val bubbleColor = if (isOutgoing) MaterialTheme.colorScheme.primaryContainer else MaterialTheme.colorScheme.surfaceVariant
    val bubbleTextColor =
        if (isOutgoing) MaterialTheme.colorScheme.onPrimaryContainer else MaterialTheme.colorScheme.onSurfaceVariant
    val rowDescription = (if (isOutgoing) "You: " else "Peer: ") + message.body

    Box(
        modifier =
            Modifier
                .fillMaxWidth()
                .padding(horizontal = 12.dp, vertical = 4.dp)
                .testTag(ChatTestTags.MESSAGE_ROW)
                .semantics { contentDescription = rowDescription },
        contentAlignment = if (isOutgoing) Alignment.CenterEnd else Alignment.CenterStart,
    ) {
        Column(
            horizontalAlignment = if (isOutgoing) Alignment.End else Alignment.Start,
            modifier = Modifier.widthIn(max = 280.dp),
        ) {
            Box(
                modifier =
                    Modifier
                        .background(bubbleColor, RoundedCornerShape(12.dp))
                        .padding(horizontal = 12.dp, vertical = 8.dp),
            ) {
                Text(text = message.body, color = bubbleTextColor, style = MaterialTheme.typography.bodyLarge)
            }
            if (isOutgoing) {
                MessageStatusView(status = message.status, modifier = Modifier.padding(top = 2.dp, end = 4.dp))
            }
        }
    }
}

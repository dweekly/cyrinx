package com.dweekly.cyrinx.chat.app.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
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
import com.dweekly.cyrinx.chat.toHexString

/** The one conversation's message list (design brief: "message list with
 * per-message status"). Auto-scrolls to the newest message as the list grows. */
@Composable
fun MessageList(messages: List<ChatMessage>, modifier: Modifier = Modifier) {
    val listState = rememberLazyListState()
    LaunchedEffect(messages.size) {
        if (messages.isNotEmpty()) listState.animateScrollToItem(messages.size - 1)
    }
    LazyColumn(
        state = listState,
        modifier = modifier.testTag(ChatTestTags.MESSAGE_LIST),
        contentPadding = PaddingValues(vertical = 8.dp),
    ) {
        items(messages, key = { it.id.toHexString() }) { message -> MessageRow(message) }
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

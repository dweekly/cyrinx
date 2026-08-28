package com.dweekly.cyrinx.chat.app.ui

import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.CloudUpload
import androidx.compose.material.icons.filled.Error
import androidx.compose.material.icons.filled.Schedule
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import com.dweekly.cyrinx.chat.ChatMessageDisplayStatus
import com.dweekly.cyrinx.chat.ChatTestTags

/**
 * Per-message outgoing status: icon AND text label together -- design brief:
 * "per-message status with text+icon never color-alone." Color never carries
 * meaning a colorblind or grayscale-display user would miss; [label] is the
 * same information rendered visibly, and [StatusVisual.longDescription] is
 * read by TalkBack. CONTRACT.md section 1.5's honest-delivery caveat
 * ("delivered means transport acknowledgment only") is surfaced via that
 * longer description rather than squeezed into the always-visible short label.
 */
@Composable
fun MessageStatusView(status: ChatMessageDisplayStatus, modifier: Modifier = Modifier) {
    val visual = status.toStatusVisual()
    Row(
        modifier =
            modifier
                .testTag(ChatTestTags.MESSAGE_STATUS)
                .semantics(mergeDescendants = true) { contentDescription = visual.longDescription },
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Icon(visual.icon, contentDescription = null, tint = visual.tint, modifier = Modifier.size(14.dp))
        Text(
            text = visual.label,
            style = MaterialTheme.typography.labelSmall,
            color = visual.tint,
            modifier = Modifier.padding(start = 4.dp),
        )
    }
}

private data class StatusVisual(val icon: ImageVector, val label: String, val tint: Color, val longDescription: String)

@Composable
private fun ChatMessageDisplayStatus.toStatusVisual(): StatusVisual =
    when (this) {
        ChatMessageDisplayStatus.Queued ->
            StatusVisual(Icons.Filled.Schedule, "Queued", MaterialTheme.colorScheme.onSurfaceVariant, "Queued to send")
        ChatMessageDisplayStatus.Transmitting ->
            StatusVisual(
                Icons.Filled.CloudUpload,
                "Transmitting",
                MaterialTheme.colorScheme.primary,
                "Transmitting over the acoustic link",
            )
        ChatMessageDisplayStatus.Delivered ->
            StatusVisual(
                Icons.Filled.CheckCircle,
                "Delivered",
                MaterialTheme.colorScheme.tertiary,
                "Delivered — transport acknowledged receipt of these bytes; this is not proof of who received them",
            )
        is ChatMessageDisplayStatus.Failed ->
            StatusVisual(Icons.Filled.Error, "Failed", MaterialTheme.colorScheme.error, "Failed to send: $reason")
    }

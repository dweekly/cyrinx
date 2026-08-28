package com.dweekly.cyrinx.chat.app.ui

import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.unit.dp
import com.dweekly.cyrinx.chat.ChatTestTags

/**
 * Composer bar (design brief's product scope: "composer" + `composerField`/
 * `sendButton` accessibility IDs, CONTRACT.md section 5). [enabled] gates
 * sending while not connected/degraded (CONTRACT.md section 2's "Send
 * precondition (pinned)": `send()` is only accepted while `connected` or
 * `degraded`) -- the composer stays visible but the send action is disabled
 * rather than the whole conversation screen changing shape, so a user typing a
 * draft never loses it to a transient state change.
 */
@Composable
fun Composer(text: String, onTextChange: (String) -> Unit, enabled: Boolean, onSend: () -> Unit, modifier: Modifier = Modifier) {
    val canSend = enabled && text.isNotEmpty()
    Row(
        modifier = modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        OutlinedTextField(
            value = text,
            onValueChange = onTextChange,
            modifier = Modifier.weight(1f).testTag(ChatTestTags.COMPOSER_FIELD),
            placeholder = { Text("Message") },
            singleLine = true,
            enabled = enabled,
            keyboardOptions = KeyboardOptions(imeAction = ImeAction.Send),
            keyboardActions = KeyboardActions(onSend = { if (canSend) onSend() }),
        )
        IconButton(
            onClick = onSend,
            enabled = canSend,
            modifier =
                Modifier
                    .testTag(ChatTestTags.SEND_BUTTON)
                    .semantics { contentDescription = "Send message" },
        ) {
            Icon(
                Icons.AutoMirrored.Filled.Send,
                contentDescription = null,
                tint = if (canSend) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.size(22.dp),
            )
        }
    }
}

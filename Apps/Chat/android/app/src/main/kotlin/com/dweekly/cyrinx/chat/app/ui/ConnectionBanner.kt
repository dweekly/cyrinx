package com.dweekly.cyrinx.chat.app.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import com.dweekly.cyrinx.chat.ChatTestTags

/**
 * The derived plain-language banner (design brief's pinned projection; text
 * comes from [com.dweekly.cyrinx.chat.app.ChatBanner]/[com.dweekly.cyrinx.chat.app.ChatProjection]).
 * Shown only when [text] is non-null; icon + text together, never a color-only
 * signal (design brief: "per-message status ... never color-alone" -- applied
 * here too for consistency, even though the brief's explicit "never
 * color-alone" example is about message status specifically).
 *
 * Deliberately does NOT also render [com.dweekly.cyrinx.chat.app.ChatUiState
 * .gapCaption] -- the design brief pins the gap caption as "a small 'events
 * dropped' caption, not a banner," so it must be able to appear even while
 * [banner] itself is null; see [GapCaption], rendered as its own independent
 * element by `ChatScreen`.
 */
@Composable
fun ConnectionBanner(text: String, modifier: Modifier = Modifier) {
    Row(
        modifier =
            modifier
                .fillMaxWidth()
                .background(MaterialTheme.colorScheme.errorContainer)
                .padding(horizontal = 16.dp, vertical = 8.dp)
                .testTag(ChatTestTags.CONNECTION_BANNER)
                .semantics(mergeDescendants = true) { contentDescription = text },
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Icon(
            Icons.Filled.Warning,
            contentDescription = null,
            tint = MaterialTheme.colorScheme.onErrorContainer,
            modifier = Modifier.size(18.dp),
        )
        Text(
            text = text,
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onErrorContainer,
            modifier = Modifier.padding(start = 8.dp),
        )
    }
}

/** "A small 'events dropped' caption, not a banner" (design brief) -- rendered
 * independently of [ConnectionBanner] so it can appear whether or not a banner
 * is also showing. Not part of CONTRACT.md section 5's tag registry (the brief
 * does not pin a dedicated identifier for it), so it carries only a
 * content-description for accessibility, no `testTag`. */
@Composable
fun GapCaption(text: String, modifier: Modifier = Modifier) {
    Text(
        text = text,
        style = MaterialTheme.typography.labelSmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
        modifier =
            modifier
                .fillMaxWidth()
                .padding(horizontal = 16.dp, vertical = 2.dp)
                .semantics { contentDescription = text },
    )
}

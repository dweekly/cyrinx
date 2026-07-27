package com.dweekly.cyrinx.chat.app.ui

import androidx.compose.material3.AssistChip
import androidx.compose.material3.AssistChipDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import com.dweekly.cyrinx.chat.ChatLinkBudget
import com.dweekly.cyrinx.chat.ChatTestTags
import com.dweekly.cyrinx.chat.LinkBudgetClass

/**
 * Coarse directional link budget, backed by the numeric [ChatLinkBudget]
 * (design brief: "coarse link-budget badge backed by the numeric
 * ChatLinkBudget"). Text-only label (the classification's own human name),
 * so "never color-alone" is trivially satisfied -- there is no color-coded
 * chip variant here to begin with.
 */
@Composable
fun LinkBudgetBadge(budget: ChatLinkBudget, modifier: Modifier = Modifier) {
    val label = budget.classification.displayLabel()
    val description = buildString {
        append("Link budget: ")
        append(label)
        if (budget.txLowerBoundBps != null || budget.rxLowerBoundBps != null) {
            append(". ")
            budget.txLowerBoundBps?.let { append("At least $it bits per second up. ") }
            budget.rxLowerBoundBps?.let { append("At least $it bits per second down.") }
        }
    }
    AssistChip(
        onClick = {},
        enabled = false,
        label = { Text(label, style = MaterialTheme.typography.labelMedium) },
        colors = AssistChipDefaults.assistChipColors(disabledLabelColor = MaterialTheme.colorScheme.onSurface),
        modifier =
            modifier
                .testTag(ChatTestTags.LINK_BUDGET_BADGE)
                .semantics { contentDescription = description },
    )
}

/** DECISION (not pinned by the brief): CONTRACT.md section 1.3 pins the wire
 * string form (`controlOnly`/`text`/`thumbnail`/`bulk`) for traces and launch
 * arguments, not a user-facing display label; these are this app's own choice. */
private fun LinkBudgetClass.displayLabel(): String =
    when (this) {
        LinkBudgetClass.CONTROL_ONLY -> "Control only"
        LinkBudgetClass.TEXT -> "Text"
        LinkBudgetClass.THUMBNAIL -> "Thumbnail"
        LinkBudgetClass.BULK -> "Bulk"
    }

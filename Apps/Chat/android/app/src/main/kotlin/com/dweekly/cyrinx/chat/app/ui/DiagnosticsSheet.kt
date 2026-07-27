package com.dweekly.cyrinx.chat.app.ui

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Text
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp

/**
 * Advanced diagnostics sheet -- placeholder text only for C3-30 (design brief:
 * "diagnostics sheet placeholder"; README.md: "Export a redacted support bundle
 * from an advanced diagnostics sheet (later stage; diagnostics-button
 * accessibility ID is reserved now)"). C3-26/C3-31 wire the real redacted
 * support-bundle export; this sheet exists so the reserved
 * `ChatTestTags.DIAGNOSTICS_BUTTON` has somewhere to navigate to now.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun DiagnosticsSheet(onDismiss: () -> Unit) {
    val sheetState = rememberModalBottomSheetState()
    ModalBottomSheet(onDismissRequest = onDismiss, sheetState = sheetState) {
        Column(modifier = Modifier.padding(24.dp)) {
            Text("Diagnostics", style = MaterialTheme.typography.titleLarge)
            Text(
                text =
                    "Support-bundle export is not implemented in this build. A future stage " +
                        "wires a redacted diagnostic export here (scenario, event trace, and link " +
                        "metrics — never message contents or peer identity beyond the ephemeral, " +
                        "unauthenticated transport ID).",
                style = MaterialTheme.typography.bodyMedium,
                modifier = Modifier.padding(top = 12.dp),
            )
        }
    }
}

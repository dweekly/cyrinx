package com.dweekly.cyrinx.chat.app.ui

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.BugReport
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.dweekly.cyrinx.chat.ChatConnectionState
import com.dweekly.cyrinx.chat.ChatTestTags
import com.dweekly.cyrinx.chat.app.ChatViewModel

/**
 * Top-level screen: peer browser -> connect -> one conversation (design
 * brief's product scope), plus the always-on chrome (unauthenticated notice,
 * connection banner, link-budget badge, diagnostics sheet). Observes
 * [ChatViewModel.uiState] with [collectAsStateWithLifecycle] -- the design
 * brief's "lifecycle-aware collection" requirement: collection automatically
 * pauses/resumes with this screen's own lifecycle rather than running while
 * backgrounded.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ChatScreen(viewModel: ChatViewModel, modifier: Modifier = Modifier) {
    val uiState by viewModel.uiState.collectAsStateWithLifecycle()
    var composerText by rememberSaveable { mutableStateOf("") }
    var showDiagnostics by rememberSaveable { mutableStateOf(false) }

    val showingConversation = uiState.connection !is ChatConnectionState.Disconnected
    val canSend = uiState.connection is ChatConnectionState.Connected || uiState.connection is ChatConnectionState.Degraded

    Scaffold(
        modifier = modifier,
        topBar = {
            TopAppBar(
                title = { Text("Cyrinx Chat") },
                navigationIcon = {
                    if (showingConversation) {
                        IconButton(onClick = { viewModel.disconnect() }) {
                            Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Back to peer list")
                        }
                    }
                },
                actions = {
                    IconButton(
                        onClick = { showDiagnostics = true },
                        modifier = Modifier.testTag(ChatTestTags.DIAGNOSTICS_BUTTON),
                    ) {
                        Icon(Icons.Filled.BugReport, contentDescription = "Diagnostics")
                    }
                },
            )
        },
    ) { innerPadding ->
        Column(modifier = Modifier.padding(innerPadding)) {
            UnauthenticatedNotice()

            uiState.banner?.let { banner -> ConnectionBanner(text = banner) }
            if (uiState.eventSeqGapDetected) {
                uiState.gapCaption?.let { caption -> GapCaption(text = caption) }
            }
            uiState.transientError?.let { error ->
                ErrorBanner(text = error, onDismiss = { viewModel.dismissTransientError() })
            }

            LinkBudgetBadge(budget = uiState.budget, modifier = Modifier.padding(horizontal = 16.dp, vertical = 6.dp))

            if (showingConversation) {
                MessageList(
                    messages = uiState.messages,
                    gapNotices = uiState.messageGapNotices,
                    modifier = Modifier.weight(1f),
                )
                Composer(
                    text = composerText,
                    onTextChange = { composerText = it },
                    enabled = canSend,
                    onSend = {
                        viewModel.sendMessage(composerText)
                        composerText = ""
                    },
                )
            } else {
                PeerBrowser(
                    peers = uiState.peers,
                    onConnect = { peerIdHex -> viewModel.connectToPeer(peerIdHex) },
                    modifier = Modifier.weight(1f),
                )
            }
        }
    }

    if (showDiagnostics) {
        DiagnosticsSheet(onDismiss = { showDiagnostics = false })
    }
}

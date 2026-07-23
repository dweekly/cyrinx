package com.dweekly.cyrinx.chat.app

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.viewModels
import com.dweekly.cyrinx.chat.app.ui.ChatScreen
import com.dweekly.cyrinx.chat.app.ui.ChatTheme

/**
 * Single-activity host, per the design brief's small-sample-app scope. Reads
 * `chat.scenario`/`chat.seed`/`chat.simulated` from this Activity's own
 * launching `Intent` extras (CONTRACT.md section 5: "Android: instrumentation
 * args / `Intent` extras with the identical keys, e.g. `am start -e
 * chat.scenario happyPair`"). No audio permission is requested anywhere in this
 * class or the manifest -- see AndroidManifest.xml's reserved-for-C3-31 comment.
 */
class MainActivity : ComponentActivity() {
    private val viewModel: ChatViewModel by viewModels {
        ChatViewModelFactory(ChatLaunchConfig.fromIntent(intent))
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            ChatTheme {
                ChatScreen(viewModel = viewModel)
            }
        }
    }
}

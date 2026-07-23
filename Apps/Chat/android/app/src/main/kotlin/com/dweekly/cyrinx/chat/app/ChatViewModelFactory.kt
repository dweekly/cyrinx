package com.dweekly.cyrinx.chat.app

import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewmodel.CreationExtras

/**
 * Production [ViewModelProvider.Factory]: resolves [ChatLaunchConfig] from
 * [MainActivity]'s own launching `Intent` and constructs [ChatViewModel] with
 * every other constructor parameter left at its production default
 * (real-elapsed-time [com.dweekly.cyrinx.chat.VirtualTimeSource], a
 * ViewModel-owned [kotlinx.coroutines.CoroutineScope] -- see
 * [ChatViewModel]'s class doc comment for why those are constructor
 * parameters rather than baked in). Tests construct [ChatViewModel] directly,
 * never through this factory -- see ChatViewModelTest.
 */
class ChatViewModelFactory(private val config: ChatLaunchConfig) : ViewModelProvider.Factory {
    override fun <T : ViewModel> create(modelClass: Class<T>, extras: CreationExtras): T {
        @Suppress("UNCHECKED_CAST")
        return ChatViewModel(config) as T
    }
}

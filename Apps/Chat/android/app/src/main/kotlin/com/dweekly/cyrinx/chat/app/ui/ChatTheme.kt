package com.dweekly.cyrinx.chat.app.ui

import android.os.Build
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.dynamicDarkColorScheme
import androidx.compose.material3.dynamicLightColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.platform.LocalContext

/**
 * Standard Material3 theme wrapper -- dynamic color on API 31+, static
 * light/dark fallback below that. No custom design system (design brief: "the
 * sample must stay small," mirroring the Apple side's "system materials ...
 * no custom design system"); this is the entire theming surface.
 */
@Composable
fun ChatTheme(content: @Composable () -> Unit) {
    val useDarkTheme = isSystemInDarkTheme()
    val context = LocalContext.current
    val colorScheme =
        when {
            Build.VERSION.SDK_INT >= Build.VERSION_CODES.S ->
                if (useDarkTheme) dynamicDarkColorScheme(context) else dynamicLightColorScheme(context)
            useDarkTheme -> darkColorScheme()
            else -> lightColorScheme()
        }
    MaterialTheme(colorScheme = colorScheme, content = content)
}

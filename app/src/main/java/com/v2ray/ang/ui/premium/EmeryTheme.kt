package com.v2ray.ang.ui.premium

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.Immutable
import androidx.compose.ui.graphics.Color

@Immutable
object EmeryColors {
    val Success = Color(0xFF5A9E78)
    val SuccessContainer = Color(0xFF1A3328)
    val OnSuccess = Color(0xFFD8F0E4)
    val Warning = Color(0xFFC49A3C)
    val WarningContainer = Color(0xFF3D2E12)
    val OnWarning = Color(0xFFF5ECDA)
    val ConnectIdle = Color(0xFF1D3348)
    val ConnectActive = Color(0xFF265840)
    val ConnectingGlow = Color(0xFF4A7FB5)
    val ConnectedGlow = Color(0xFF5A9E78)
    val TextMuted = Color(0xFF566E82)
    val Brand = Color(0xFF5289B5)
}

internal val EmeryDarkScheme = darkColorScheme(
    primary = Color(0xFF4A7FB5),
    onPrimary = Color(0xFFEAF2FA),
    primaryContainer = Color(0xFF1A3550),
    onPrimaryContainer = Color(0xFFBDD5EA),
    secondary = Color(0xFF6B9DC8),
    onSecondary = Color(0xFFE8F0F8),
    secondaryContainer = Color(0xFF1E3A55),
    onSecondaryContainer = Color(0xFFB8CAD8),
    tertiary = Color(0xFF5A9E78),
    onTertiary = Color(0xFFE8F8EF),
    tertiaryContainer = Color(0xFF1A3328),
    onTertiaryContainer = Color(0xFFB8E0CA),
    error = Color(0xFFC05656),
    onError = Color(0xFFFAEAEA),
    errorContainer = Color(0xFF3D1A16),
    onErrorContainer = Color(0xFFE8B8B8),
    background = Color(0xFF0B132B),
    onBackground = Color(0xFFD4DEE8),
    surface = Color(0xFF0B132B),
    onSurface = Color(0xFFD4DEE8),
    surfaceVariant = Color(0xFF0B132B),
    onSurfaceVariant = Color(0xFF8299AD),
    outline = Color(0xFF2A3D52),
    outlineVariant = Color(0xFF1E2F40),
    inverseSurface = Color(0xFFD4DEE8),
    inverseOnSurface = Color(0xFF111B27),
    inversePrimary = Color(0xFF2A5A85),
    scrim = Color(0xFF000000),
    surfaceTint = Color(0x00000000),
)

@Composable
fun EmeryTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = EmeryDarkScheme,
        content = content,
    )
}

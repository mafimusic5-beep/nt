package com.v2ray.ang.ui.premium

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.Immutable
import androidx.compose.ui.graphics.Color

@Immutable
object EmeryColors {
    val Success = Color(0xFF5A9E78)
    val SuccessContainer = Color(0xFFEAF6E3)
    val OnSuccess = Color(0xFF111319)
    val Warning = Color(0xFFC49A3C)
    val WarningContainer = Color(0xFFFFF4D8)
    val OnWarning = Color(0xFF111319)
    val ConnectIdle = Color(0xFFC8F08E)
    val ConnectActive = Color(0xFF49B530)
    val ConnectingGlow = Color(0xFF6BC652)
    val ConnectedGlow = Color(0xFF49B530)
    val TextMuted = Color(0xFF7D828D)
    val Brand = Color(0xFF111319)
}

internal val EmeryLightScheme = lightColorScheme(
    primary = Color(0xFF111319),
    onPrimary = Color(0xFFFFFFFF),
    primaryContainer = Color(0xFFEAF6E3),
    onPrimaryContainer = Color(0xFF111319),
    secondary = Color(0xFF6BC652),
    onSecondary = Color(0xFF111319),
    secondaryContainer = Color(0xFFEAF6E3),
    onSecondaryContainer = Color(0xFF111319),
    tertiary = Color(0xFF49B530),
    onTertiary = Color(0xFFFFFFFF),
    tertiaryContainer = Color(0xFFEAF6E3),
    onTertiaryContainer = Color(0xFF111319),
    error = Color(0xFFBA1A1A),
    onError = Color(0xFFFFFFFF),
    errorContainer = Color(0xFFFFDAD6),
    onErrorContainer = Color(0xFF410002),
    background = Color(0xFFF7F8F4),
    onBackground = Color(0xFF111319),
    surface = Color(0xFFFFFFFF),
    onSurface = Color(0xFF111319),
    surfaceVariant = Color(0xFFF7F8F4),
    onSurfaceVariant = Color(0xFF7D828D),
    outline = Color(0xFFE7ECE2),
    outlineVariant = Color(0xFFDCE4D5),
    inverseSurface = Color(0xFF111319),
    inverseOnSurface = Color(0xFFFFFFFF),
    inversePrimary = Color(0xFFC8F08E),
    scrim = Color(0xFF000000),
    surfaceTint = Color(0x00000000),
)

@Composable
fun EmeryTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = EmeryLightScheme,
        content = content,
    )
}

package com.v2ray.ang.ui.premium

import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.tween
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import kotlin.math.abs

private const val CHARACTER_IMPRINT_MS = 190
private const val CHARACTER_START_SCALE = 0.94f
private const val UNDERLINE_HIGHLIGHT_ALPHA = 0.42f

@Composable
internal fun rememberActivationCharacterState(char: String): ActivationCharacterState {
    var visibleChar by remember { mutableStateOf(char) }
    val progress = remember { Animatable(if (char.isBlank()) 0f else 1f) }

    LaunchedEffect(char) {
        if (char.isBlank()) {
            progress.snapTo(0f)
            visibleChar = ""
        } else {
            visibleChar = char
            progress.snapTo(0f)
            progress.animateTo(
                targetValue = 1f,
                animationSpec = tween(
                    durationMillis = CHARACTER_IMPRINT_MS,
                    easing = FastOutSlowInEasing,
                ),
            )
        }
    }

    val scale = CHARACTER_START_SCALE + (1f - CHARACTER_START_SCALE) * progress.value
    val underlineAlpha = UNDERLINE_HIGHLIGHT_ALPHA * (1f - abs((progress.value * 2f) - 1f))

    return ActivationCharacterState(
        visibleChar = visibleChar,
        alpha = progress.value,
        scale = scale,
        underlineAlpha = underlineAlpha,
    )
}

internal data class ActivationCharacterState(
    val visibleChar: String,
    val alpha: Float,
    val scale: Float,
    val underlineAlpha: Float,
)

package com.v2ray.ang.ui.premium

import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.tween
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue

private const val CHARACTER_FADE_IN_MS = 240

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
                animationSpec = tween(durationMillis = CHARACTER_FADE_IN_MS),
            )
        }
    }

    return ActivationCharacterState(
        visibleChar = visibleChar,
        alpha = progress.value,
        yOffsetProgress = 1f - progress.value,
    )
}

internal data class ActivationCharacterState(
    val visibleChar: String,
    val alpha: Float,
    val yOffsetProgress: Float,
)

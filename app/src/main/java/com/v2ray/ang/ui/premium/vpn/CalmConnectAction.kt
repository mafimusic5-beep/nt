package com.v2ray.ang.ui.premium.vpn

import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.delay

@Composable
internal fun CalmConnectAction(
    state: VpnConnectionState,
    enabled: Boolean,
    checkingConnection: Boolean,
    compact: Boolean,
    tight: Boolean,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val label = when {
        checkingConnection -> "Проверяем связь…"
        state == VpnConnectionState.Disconnected -> "Включить VPN"
        state == VpnConnectionState.Connecting -> "Включаем..."
        else -> "Отключить VPN"
    }

    val shape = RoundedCornerShape(if (compact) 22.dp else 26.dp)
    val buttonHeight = if (tight) 54.dp else if (compact) 60.dp else 66.dp
    val buttonEnabled = enabled && state != VpnConnectionState.Connecting && !checkingConnection
    val showRipples = enabled && state == VpnConnectionState.Disconnected && !checkingConnection
    val phase = remember { Animatable(0f) }
    val interactionSource = remember { MutableInteractionSource() }

    LaunchedEffect(showRipples) {
        if (!showRipples) {
            phase.snapTo(0f)
            return@LaunchedEffect
        }

        while (true) {
            phase.snapTo(0f)
            phase.animateTo(
                targetValue = 1f,
                animationSpec = tween(
                    durationMillis = 1050,
                    easing = FastOutSlowInEasing,
                ),
            )
            phase.snapTo(0f)
            delay(3800L)
        }
    }

    Box(
        modifier = modifier
            .fillMaxWidth()
            .height(buttonHeight + 10.dp),
        contentAlignment = Alignment.Center,
    ) {
        val progress = phase.value
        if (showRipples && progress > 0f) {
            Canvas(Modifier.fillMaxSize()) {
                val phases = listOf(
                    progress,
                    ((progress - 0.20f) / 0.80f).coerceIn(0f, 1f),
                )
                val baseInsetX = 7.dp.toPx()
                val baseInsetY = 5.dp.toPx()

                phases.forEachIndexed { index, wave ->
                    if (wave <= 0f) return@forEachIndexed
                    val fade = (1f - wave).coerceIn(0f, 1f)
                    val insetX = baseInsetX * (1f - wave)
                    val insetY = baseInsetY * (1f - wave)
                    val width = (size.width - insetX * 2f).coerceAtLeast(0f)
                    val height = (size.height - insetY * 2f).coerceAtLeast(0f)
                    val alpha = (if (index == 0) 0.17f else 0.10f) * fade

                    drawRoundRect(
                        color = Color.Black.copy(alpha = alpha),
                        topLeft = Offset(insetX, insetY),
                        size = Size(width, height),
                        cornerRadius = CornerRadius(height / 2f, height / 2f),
                        style = Stroke(width = (if (index == 0) 0.95.dp else 0.75.dp).toPx()),
                    )
                }
            }
        }

        Box(
            modifier = Modifier
                .fillMaxWidth(0.97f)
                .height(buttonHeight)
                .clip(shape)
                .clickable(
                    interactionSource = interactionSource,
                    indication = null,
                    enabled = buttonEnabled,
                    role = Role.Button,
                    onClick = onClick,
                ),
            contentAlignment = Alignment.Center,
        ) {
            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.Center,
            ) {
                if (checkingConnection) {
                    CircularProgressIndicator(
                        modifier = Modifier.size(if (compact) 18.dp else 20.dp),
                        strokeWidth = 2.dp,
                        color = Color(0xFF7D828D),
                    )
                    Spacer(Modifier.width(12.dp))
                }
                Text(
                    text = label,
                    style = if (compact) MaterialTheme.typography.titleMedium else MaterialTheme.typography.titleLarge,
                    color = if (buttonEnabled || state == VpnConnectionState.Connected) Color(0xFF111319) else Color(0x997D828D),
                    fontWeight = FontWeight.Medium,
                    maxLines = 1,
                )
            }
        }
    }
}

package com.v2ray.ang.ui.premium.vpn

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
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
    val label = when (state) {
        VpnConnectionState.Disconnected -> "Включить VPN"
        VpnConnectionState.Connecting -> "Включить VPN"
        VpnConnectionState.Connected -> "Отключить VPN"
    }

    val shape = RoundedCornerShape(999.dp)
    val buttonHeight = if (tight) 54.dp else if (compact) 60.dp else 66.dp
    val buttonEnabled = enabled && state != VpnConnectionState.Connecting && !checkingConnection
    val interactionSource = remember { MutableInteractionSource() }

    var previousState by remember { mutableStateOf(state) }
    var showConnectedOverlay by remember { mutableStateOf(false) }

    LaunchedEffect(state) {
        val oldState = previousState
        previousState = state

        if (state == VpnConnectionState.Connected && oldState == VpnConnectionState.Connecting) {
            showConnectedOverlay = true
            delay(900L)
            showConnectedOverlay = false
        } else if (state != VpnConnectionState.Connected) {
            showConnectedOverlay = false
        }
    }

    Box(
        modifier = modifier
            .fillMaxWidth()
            .height(buttonHeight)
            .clip(shape)
            .border(
                width = 1.dp,
                color = Color(0xFF9EA3AD),
                shape = shape,
            )
            .clickable(
                interactionSource = interactionSource,
                indication = null,
                enabled = buttonEnabled,
                role = Role.Button,
                onClick = onClick,
            ),
        contentAlignment = Alignment.Center,
    ) {
        Text(
            text = label,
            style = if (compact) MaterialTheme.typography.titleMedium else MaterialTheme.typography.titleLarge,
            color = if (buttonEnabled || state == VpnConnectionState.Connected) {
                Color(0xFF111319)
            } else {
                Color(0x997D828D)
            },
            fontWeight = FontWeight.Medium,
            maxLines = 1,
        )
    }

    if (state == VpnConnectionState.Connecting || showConnectedOverlay) {
        Dialog(
            onDismissRequest = {},
            properties = DialogProperties(
                dismissOnBackPress = false,
                dismissOnClickOutside = false,
                usePlatformDefaultWidth = false,
            ),
        ) {
            Box(
                modifier = Modifier
                    .fillMaxSize()
                    .background(Color.Black.copy(alpha = 0.42f)),
                contentAlignment = Alignment.Center,
            ) {
                Text(
                    text = if (state == VpnConnectionState.Connecting) "Подключение.." else "Подключено",
                    style = MaterialTheme.typography.headlineMedium,
                    color = Color.White,
                    fontWeight = FontWeight.SemiBold,
                )
            }
        }
    }
}

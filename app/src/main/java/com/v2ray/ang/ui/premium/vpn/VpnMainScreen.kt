package com.v2ray.ang.ui.premium.vpn

import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.EaseInOutSine
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.KeyboardArrowDown
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp

@Composable
fun VpnMainRoute(
    viewModel: VpnMainViewModel,
    modifier: Modifier = Modifier,
) {
    val uiState by viewModel.uiState.collectAsState()
    VpnMainScreen(
        uiState = uiState,
        locations = VpnDemoData.locations,
        onLocationSelected = viewModel::onLocationSelected,
        onConnectClick = viewModel::onConnectClick,
        onDisconnectClick = viewModel::onDisconnectClick,
        modifier = modifier,
    )
}

@Composable
fun VpnMainScreen(
    uiState: VpnMainUiState,
    locations: List<VpnLocationOption>,
    onLocationSelected: (String) -> Unit,
    onConnectClick: () -> Unit,
    onDisconnectClick: () -> Unit,
    modifier: Modifier = Modifier,
) {
    BoxWithConstraints(
        modifier = modifier
            .fillMaxSize()
            .background(VpnPremiumTokens.Colors.Background)
            .navigationBarsPadding()
            .imePadding(),
    ) {
        val compact = maxHeight < 830.dp
        val tight = maxHeight < 730.dp
        val horizontalPadding = if (tight) 18.dp else 24.dp
        val topPadding = if (tight) 4.dp else 8.dp
        val bottomPadding = if (tight) 8.dp else 12.dp
        val headerToBeacon = if (tight) 10.dp else if (compact) 14.dp else 20.dp
        val beaconToTitle = if (tight) 14.dp else if (compact) 18.dp else 24.dp
        val titleToSubtitle = if (tight) 4.dp else 8.dp
        val subtitleToRoute = if (tight) 8.dp else 14.dp
        val routeToCard = if (tight) 10.dp else 16.dp
        val cardToButton = if (tight) 10.dp else 14.dp

        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(
                    start = horizontalPadding,
                    top = topPadding,
                    end = horizontalPadding,
                    bottom = bottomPadding,
                ),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            HeaderBar(compact = compact)

            Spacer(modifier = Modifier.height(headerToBeacon))

            StatusBeacon(connectionState = uiState.connectionState)

            Spacer(modifier = Modifier.height(beaconToTitle))

            Text(
                text = screenTitle(uiState.connectionState),
                style = if (tight) MaterialTheme.typography.headlineMedium else MaterialTheme.typography.headlineLarge,
                fontWeight = FontWeight.SemiBold,
                color = VpnPremiumTokens.Colors.TextPrimary,
                textAlign = TextAlign.Center,
            )

            Spacer(modifier = Modifier.height(titleToSubtitle))

            Text(
                text = screenSubtitle(uiState.connectionState),
                style = if (tight) MaterialTheme.typography.bodyLarge else MaterialTheme.typography.titleMedium,
                color = VpnPremiumTokens.Colors.TextSecondary,
                textAlign = TextAlign.Center,
            )

            Spacer(modifier = Modifier.height(subtitleToRoute))

            RouteSelectorChip(
                selectedLocation = uiState.selectedLocation,
                locations = locations,
                onLocationSelected = onLocationSelected,
                compact = compact,
            )

            Spacer(modifier = Modifier.height(routeToCard))

            when (uiState.connectionState) {
                VpnConnectionState.Disconnected -> DisconnectedCard(compact = compact, tight = tight)
                VpnConnectionState.Connecting -> ConnectingCard(compact = compact, tight = tight)
                VpnConnectionState.Connected -> ConnectedCard(
                    duration = uiState.formattedDuration,
                    compact = compact,
                    tight = tight,
                )
            }

            Spacer(modifier = Modifier.height(cardToButton))

            PrimaryConnectButton(
                state = uiState.connectionState,
                enabled = uiState.connectButtonEnabled,
                compact = compact,
                tight = tight,
                onClick = {
                    if (uiState.connectionState == VpnConnectionState.Connected) {
                        onDisconnectClick()
                    } else {
                        onConnectClick()
                    }
                },
            )

            if (uiState.connectionState == VpnConnectionState.Disconnected && !uiState.connectButtonEnabled) {
                Spacer(modifier = Modifier.height(if (tight) 6.dp else 10.dp))
                Text(
                    text = "Ключ доступа не найден",
                    style = MaterialTheme.typography.bodyMedium,
                    color = VpnPremiumTokens.Colors.TextSecondary,
                    textAlign = TextAlign.Center,
                )
            }
        }
    }
}

private fun screenTitle(state: VpnConnectionState): String = when (state) {
    VpnConnectionState.Disconnected -> "Защита выключена"
    VpnConnectionState.Connecting -> "Включаем защиту"
    VpnConnectionState.Connected -> "Защита включена"
}

private fun screenSubtitle(state: VpnConnectionState): String = when (state) {
    VpnConnectionState.Disconnected -> "Автозащита следит за сетью\nи включится при риске"
    VpnConnectionState.Connecting -> "Проверяем сеть и создаём\nзащищённое подключение"
    VpnConnectionState.Connected -> "Трафик защищён и соединение активно"
}

@Composable
private fun HeaderBar(
    compact: Boolean,
) {
    Box(
        modifier = Modifier.fillMaxWidth(),
        contentAlignment = Alignment.Center,
    ) {
        Text(
            text = "skryon",
            style = if (compact) MaterialTheme.typography.headlineSmall else MaterialTheme.typography.headlineMedium,
            fontWeight = FontWeight.SemiBold,
            color = VpnPremiumTokens.Colors.TextPrimary,
        )
    }
}

@Composable
private fun StatusBeacon(
    connectionState: VpnConnectionState,
) {
    val pulse = if (connectionState == VpnConnectionState.Connecting) {
        rememberInfiniteTransition(label = "status-beacon").animateFloat(
            initialValue = 0.99f,
            targetValue = 1.01f,
            animationSpec = infiniteRepeatable(
                animation = tween(durationMillis = 900, easing = EaseInOutSine),
                repeatMode = RepeatMode.Reverse,
            ),
            label = "beacon-pulse",
        ).value
    } else {
        1f
    }

    val coreColor by animateColorAsState(
        targetValue = when (connectionState) {
            VpnConnectionState.Disconnected -> VpnPremiumTokens.Colors.Positive
            VpnConnectionState.Connecting -> VpnPremiumTokens.Colors.Positive
            VpnConnectionState.Connected -> VpnPremiumTokens.Colors.PositiveStrong
        },
        label = "beacon-core",
    )

    val middleAlpha = when (connectionState) {
        VpnConnectionState.Disconnected -> 0.09f
        VpnConnectionState.Connecting -> 0.11f
        VpnConnectionState.Connected -> 0.10f
    }
    val outerAlpha = when (connectionState) {
        VpnConnectionState.Disconnected -> 0.035f
        VpnConnectionState.Connecting -> 0.05f
        VpnConnectionState.Connected -> 0.04f
    }

    Box(
        modifier = Modifier.size(60.dp),
        contentAlignment = Alignment.Center,
    ) {
        Box(
            modifier = Modifier
                .size(60.dp)
                .clip(CircleShape)
                .background(coreColor.copy(alpha = outerAlpha))
        )
        Box(
            modifier = Modifier
                .size((42 * pulse).dp)
                .clip(CircleShape)
                .background(coreColor.copy(alpha = middleAlpha))
        )
        Box(
            modifier = Modifier
                .size(30.dp)
                .clip(CircleShape)
                .background(coreColor)
        )
    }
}

@Composable
private fun RouteSelectorChip(
    selectedLocation: VpnLocationOption,
    locations: List<VpnLocationOption>,
    onLocationSelected: (String) -> Unit,
    compact: Boolean,
    modifier: Modifier = Modifier,
) {
    var expanded by remember { mutableStateOf(false) }

    Box {
        Row(
            modifier = modifier
                .clip(RoundedCornerShape(18.dp))
                .background(VpnPremiumTokens.Colors.Surface)
                .border(1.dp, VpnPremiumTokens.Colors.BorderSubtle, RoundedCornerShape(18.dp))
                .clickable { expanded = true }
                .padding(
                    horizontal = if (compact) 14.dp else 16.dp,
                    vertical = if (compact) 8.dp else 10.dp,
                ),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                text = selectedLocation.title,
                style = if (compact) MaterialTheme.typography.bodyMedium else MaterialTheme.typography.bodyLarge,
                color = VpnPremiumTokens.Colors.TextPrimary,
            )
            Spacer(modifier = Modifier.width(8.dp))
            Icon(
                imageVector = Icons.Rounded.KeyboardArrowDown,
                contentDescription = null,
                tint = VpnPremiumTokens.Colors.TextSecondary,
            )
        }

        DropdownMenu(
            expanded = expanded,
            onDismissRequest = { expanded = false },
            modifier = Modifier.background(Color.White),
        ) {
            locations.forEach { location ->
                DropdownMenuItem(
                    text = {
                        Text(
                            text = location.title,
                            color = VpnPremiumTokens.Colors.TextPrimary,
                        )
                    },
                    onClick = {
                        expanded = false
                        onLocationSelected(location.title)
                    },
                )
            }
        }
    }
}

@Composable
private fun DisconnectedCard(
    compact: Boolean,
    tight: Boolean,
) {
    SurfaceCard(compact = compact, tight = tight) {
        InfoRow(
            title = "Состояние",
            value = "VPN сейчас не подключён",
            note = "Ваша активность не защищена",
            compact = compact,
            tight = tight,
        )
        RowDivider(compact = compact, tight = tight)
        InfoRow(
            title = "Автозащита",
            value = "Следит за сетью",
            note = "Включится автоматически при риске",
            compact = compact,
            tight = tight,
        )
        RowDivider(compact = compact, tight = tight)
        InfoRow(
            title = "Утечек не обнаружено",
            value = "Проверено только что",
            compact = compact,
            tight = tight,
        )
    }
}

@Composable
private fun ConnectedCard(
    duration: String,
    compact: Boolean,
    tight: Boolean,
) {
    SurfaceCard(compact = compact, tight = tight) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.Top,
            horizontalArrangement = Arrangement.SpaceBetween,
        ) {
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text = "Подключение",
                    style = MaterialTheme.typography.bodyMedium,
                    color = VpnPremiumTokens.Colors.TextSecondary,
                )

                Spacer(modifier = Modifier.height(if (tight) 4.dp else 6.dp))

                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(
                        text = "В сети",
                        style = if (compact) MaterialTheme.typography.titleLarge else MaterialTheme.typography.headlineSmall,
                        color = VpnPremiumTokens.Colors.TextPrimary,
                        fontWeight = FontWeight.SemiBold,
                    )
                    Spacer(modifier = Modifier.width(8.dp))
                    Box(
                        modifier = Modifier
                            .size(9.dp)
                            .clip(CircleShape)
                            .background(VpnPremiumTokens.Colors.PositiveStrong),
                    )
                }

                Spacer(modifier = Modifier.height(if (tight) 4.dp else 6.dp))

                Text(
                    text = duration,
                    style = MaterialTheme.typography.bodyLarge,
                    color = VpnPremiumTokens.Colors.PositiveStrong,
                    fontWeight = FontWeight.Medium,
                )
            }

            Badge(text = "VPN активен", compact = compact)
        }

        RowDivider(compact = compact, tight = tight)

        InfoRow(
            title = "Риск в сети",
            value = "Низкий",
            accent = VpnPremiumTokens.Colors.PositiveStrong,
            compact = compact,
            tight = tight,
        )
        RowDivider(compact = compact, tight = tight)
        InfoRow(
            title = "Утечек не обнаружено",
            value = "Проверено только что",
            compact = compact,
            tight = tight,
        )
        RowDivider(compact = compact, tight = tight)
        InfoRow(
            title = "Автозащита",
            value = "Активна в фоне",
            note = "Остаётся включённой при риске",
            compact = compact,
            tight = tight,
        )
    }
}

@Composable
private fun ConnectingCard(
    compact: Boolean,
    tight: Boolean,
) {
    var activeStep by remember { mutableIntStateOf(0) }

    LaunchedEffect(Unit) {
        while (true) {
            kotlinx.coroutines.delay(550)
            activeStep = (activeStep + 1) % 4
        }
    }

    val steps = listOf(
        "Проверяем сеть" to "Оцениваем безопасность подключения",
        "Создаём защищённый маршрут" to "Устанавливаем безопасное соединение",
        "Проверяем утечки" to "Проверяем DNS и IP-утечки",
        "Включаем защиту" to "Защищаем ваш трафик",
    )

    SurfaceCard(compact = compact, tight = tight) {
        steps.forEachIndexed { index, (title, note) ->
            ProgressRow(
                title = title,
                note = note,
                state = when {
                    index < activeStep -> ProgressState.Completed
                    index == activeStep -> ProgressState.Active
                    else -> ProgressState.Pending
                },
                showLine = index != steps.lastIndex,
                compact = compact,
                tight = tight,
            )
        }
    }
}

private enum class ProgressState {
    Pending,
    Active,
    Completed,
}

@Composable
private fun ProgressRow(
    title: String,
    note: String,
    state: ProgressState,
    showLine: Boolean,
    compact: Boolean,
    tight: Boolean,
) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        verticalAlignment = Alignment.Top,
    ) {
        Column(
            modifier = Modifier.padding(top = 5.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            val dotColor = when (state) {
                ProgressState.Pending -> VpnPremiumTokens.Colors.Track
                ProgressState.Active -> VpnPremiumTokens.Colors.PositiveStrong
                ProgressState.Completed -> VpnPremiumTokens.Colors.PositiveStrong
            }

            Box(
                modifier = Modifier
                    .size(if (state == ProgressState.Active) 11.dp else 9.dp)
                    .clip(CircleShape)
                    .background(dotColor)
            )

            if (showLine) {
                Box(
                    modifier = Modifier
                        .padding(top = 5.dp)
                        .width(2.dp)
                        .height(if (tight) 24.dp else if (compact) 30.dp else 36.dp)
                        .background(
                            when (state) {
                                ProgressState.Pending -> VpnPremiumTokens.Colors.Track
                                ProgressState.Active -> VpnPremiumTokens.Colors.Positive.copy(alpha = 0.45f)
                                ProgressState.Completed -> VpnPremiumTokens.Colors.Positive.copy(alpha = 0.55f)
                            }
                        )
                )
            }
        }

        Spacer(modifier = Modifier.width(14.dp))

        Column(
            modifier = Modifier
                .weight(1f)
                .padding(bottom = if (showLine) if (tight) 8.dp else 10.dp else 0.dp),
        ) {
            Text(
                text = title,
                style = if (compact) MaterialTheme.typography.bodyLarge else MaterialTheme.typography.titleMedium,
                color = VpnPremiumTokens.Colors.TextPrimary,
                fontWeight = FontWeight.Medium,
            )
            Spacer(modifier = Modifier.height(3.dp))
            Text(
                text = note,
                style = MaterialTheme.typography.bodySmall,
                color = VpnPremiumTokens.Colors.TextSecondary,
            )
        }
    }
}

@Composable
fun HumanSilhouetteBlock(
    uiState: VpnMainUiState,
    modifier: Modifier = Modifier,
) {
    Box(
        modifier = modifier,
        contentAlignment = Alignment.Center,
    ) {
        StatusBeacon(connectionState = uiState.connectionState)
    }
}

@Composable
fun HologramManBlock(
    uiState: VpnMainUiState,
    modifier: Modifier = Modifier,
) {
    HumanSilhouetteBlock(uiState = uiState, modifier = modifier)
}

@Composable
fun LocationSelector(
    selectedLocation: VpnLocationOption,
    locations: List<VpnLocationOption>,
    onLocationSelected: (String) -> Unit,
    modifier: Modifier = Modifier,
) {
    RouteSelectorChip(
        selectedLocation = selectedLocation,
        locations = locations,
        onLocationSelected = onLocationSelected,
        compact = false,
        modifier = modifier,
    )
}

@Composable
fun ConnectionTimer(time: String) {
    Text(
        text = time,
        style = MaterialTheme.typography.headlineMedium,
        color = VpnPremiumTokens.Colors.TextPrimary,
        fontWeight = FontWeight.Medium,
    )
}

@Composable
fun ConnectionStatusOverlay(
    uiState: VpnMainUiState,
    modifier: Modifier = Modifier,
) {
    Box(modifier = modifier)
}

@Composable
fun ActivationKeyField(
    value: String,
    onValueChange: (String) -> Unit,
    enabled: Boolean,
    modifier: Modifier = Modifier,
) {
    Box(modifier = modifier)
}

@Composable
private fun SurfaceCard(
    compact: Boolean,
    tight: Boolean,
    content: @Composable ColumnScope.() -> Unit,
) {
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(if (compact) 24.dp else 30.dp))
            .background(VpnPremiumTokens.Colors.Surface)
            .border(
                1.dp,
                VpnPremiumTokens.Colors.BorderSubtle,
                RoundedCornerShape(if (compact) 24.dp else 30.dp),
            )
            .padding(
                horizontal = if (tight) 16.dp else if (compact) 18.dp else 22.dp,
                vertical = if (tight) 14.dp else if (compact) 16.dp else 22.dp,
            ),
        content = content,
    )
}

@Composable
private fun InfoRow(
    title: String,
    value: String,
    note: String? = null,
    accent: Color = VpnPremiumTokens.Colors.TextPrimary,
    compact: Boolean,
    tight: Boolean,
) {
    Column(modifier = Modifier.fillMaxWidth()) {
        Text(
            text = title,
            style = if (tight) MaterialTheme.typography.bodyMedium else MaterialTheme.typography.bodyLarge,
            color = VpnPremiumTokens.Colors.TextSecondary,
        )
        Spacer(modifier = Modifier.height(if (tight) 4.dp else 6.dp))
        Text(
            text = value,
            style = if (compact) MaterialTheme.typography.titleMedium else MaterialTheme.typography.titleLarge,
            color = accent,
            fontWeight = FontWeight.Medium,
        )
        if (!note.isNullOrBlank()) {
            Spacer(modifier = Modifier.height(if (tight) 3.dp else 5.dp))
            Text(
                text = note,
                style = if (tight) MaterialTheme.typography.bodySmall else MaterialTheme.typography.bodyMedium,
                color = VpnPremiumTokens.Colors.TextSecondary,
            )
        }
    }
}

@Composable
private fun RowDivider(
    compact: Boolean,
    tight: Boolean,
) {
    Box(
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = if (tight) 8.dp else if (compact) 10.dp else 14.dp)
            .height(1.dp)
            .background(VpnPremiumTokens.Colors.BorderSubtle)
    )
}

@Composable
private fun Badge(
    text: String,
    compact: Boolean,
) {
    Box(
        modifier = Modifier
            .clip(RoundedCornerShape(999.dp))
            .background(VpnPremiumTokens.Colors.PositiveSoft)
            .padding(
                horizontal = if (compact) 10.dp else 14.dp,
                vertical = if (compact) 6.dp else 8.dp,
            ),
        contentAlignment = Alignment.Center,
    ) {
        Text(
            text = text,
            style = if (compact) MaterialTheme.typography.bodySmall else MaterialTheme.typography.bodyMedium,
            color = VpnPremiumTokens.Colors.PositiveStrong,
            fontWeight = FontWeight.Medium,
        )
    }
}

@Composable
fun PrimaryConnectButton(
    state: VpnConnectionState,
    enabled: Boolean,
    onClick: () -> Unit,
    compact: Boolean = false,
    tight: Boolean = false,
    modifier: Modifier = Modifier,
) {
    val containerColor by animateColorAsState(
        targetValue = when (state) {
            VpnConnectionState.Disconnected -> VpnPremiumTokens.Colors.PrimaryButtonIdle
            VpnConnectionState.Connecting -> VpnPremiumTokens.Colors.PrimaryButtonIdle
            VpnConnectionState.Connected -> VpnPremiumTokens.Colors.PrimaryButtonConnected
        },
        label = "primary-button-color",
    )

    val label = when (state) {
        VpnConnectionState.Disconnected -> "Включить защиту"
        VpnConnectionState.Connecting -> "Включаем..."
        VpnConnectionState.Connected -> "Отключить защиту"
    }

    Button(
        onClick = onClick,
        enabled = enabled && state != VpnConnectionState.Connecting,
        modifier = modifier
            .fillMaxWidth()
            .height(if (tight) 52.dp else if (compact) 58.dp else 64.dp),
        shape = RoundedCornerShape(if (compact) 20.dp else 24.dp),
        colors = ButtonDefaults.buttonColors(
            containerColor = containerColor,
            contentColor = VpnPremiumTokens.Colors.ButtonText,
            disabledContainerColor = containerColor.copy(alpha = 0.65f),
            disabledContentColor = VpnPremiumTokens.Colors.ButtonText.copy(alpha = 0.7f),
        ),
    ) {
        Text(
            text = label,
            style = if (compact) MaterialTheme.typography.titleMedium else MaterialTheme.typography.titleLarge,
            fontWeight = FontWeight.Medium,
        )
    }
}

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
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Settings
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
import com.v2ray.ang.network.EmeryBackendClient
import kotlinx.coroutines.delay

@Composable
fun VpnMainRoute(
    viewModel: VpnMainViewModel,
    onSettingsClick: () -> Unit = {},
    modifier: Modifier = Modifier,
) {
    val uiState by viewModel.uiState.collectAsState()
    var locations by remember { mutableStateOf(VpnDemoData.locations) }

    LaunchedEffect(Unit) {
        val fetched = EmeryBackendClient.fetchVpnServers().getOrNull().orEmpty()
        val availableNames = fetched
            .filter { it.isAvailable }
            .mapNotNull { backendServer ->
                backendServer.city
                    .trim()
                    .takeIf { it.isNotEmpty() }
                    ?.lowercase()
            }
            .toSet()

        if (availableNames.isNotEmpty()) {
            val filtered = VpnDemoData.locations.filter { location ->
                location.title.trim().lowercase() in availableNames ||
                    location.id.trim().lowercase() in availableNames
            }

            if (filtered.isNotEmpty()) {
                locations = filtered
                if (filtered.none { it.id == uiState.selectedLocation.id }) {
                    viewModel.onLocationSelected(filtered.first().title)
                }
            }
        }
    }

    VpnMainScreen(
        uiState = uiState,
        locations = locations,
        onLocationSelected = viewModel::onLocationSelected,
        onConnectClick = viewModel::onConnectClick,
        onDisconnectClick = viewModel::onDisconnectClick,
        onSettingsClick = onSettingsClick,
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
    onSettingsClick: () -> Unit = {},
    modifier: Modifier = Modifier,
) {
    Column(
        modifier = modifier
            .fillMaxSize()
            .background(VpnPremiumTokens.Colors.Background)
            .statusBarsPadding()
            .navigationBarsPadding()
            .imePadding()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 24.dp, vertical = 16.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        HeaderBar(onSettingsClick = onSettingsClick)

        Spacer(modifier = Modifier.height(24.dp))

        StatusBeacon(connectionState = uiState.connectionState)

        Spacer(modifier = Modifier.height(28.dp))

        Text(
            text = screenTitle(uiState.connectionState),
            style = MaterialTheme.typography.headlineLarge,
            fontWeight = FontWeight.SemiBold,
            color = VpnPremiumTokens.Colors.TextPrimary,
            textAlign = TextAlign.Center,
        )

        Spacer(modifier = Modifier.height(10.dp))

        Text(
            text = screenSubtitle(uiState.connectionState),
            style = MaterialTheme.typography.titleMedium,
            color = VpnPremiumTokens.Colors.TextSecondary,
            textAlign = TextAlign.Center,
        )

        Spacer(modifier = Modifier.height(18.dp))

        RouteSelectorChip(
            selectedLocation = uiState.selectedLocation,
            locations = locations,
            onLocationSelected = onLocationSelected,
        )

        Spacer(modifier = Modifier.height(24.dp))

        when (uiState.connectionState) {
            VpnConnectionState.Disconnected -> DisconnectedCard()
            VpnConnectionState.Connecting -> ConnectingCard()
            VpnConnectionState.Connected -> ConnectedCard(duration = uiState.formattedDuration)
        }

        Spacer(modifier = Modifier.height(20.dp))

        PrimaryConnectButton(
            state = uiState.connectionState,
            enabled = uiState.connectionState != VpnConnectionState.Connecting,
            onClick = {
                if (uiState.connectionState == VpnConnectionState.Connected) {
                    onDisconnectClick()
                } else {
                    onConnectClick()
                }
            },
        )

        Spacer(modifier = Modifier.height(20.dp))
    }
}

private fun screenTitle(state: VpnConnectionState): String = when (state) {
    VpnConnectionState.Disconnected -> "Защита выключена"
    VpnConnectionState.Connecting -> "Включаем защиту"
    VpnConnectionState.Connected -> "Защита включена"
}

private fun screenSubtitle(state: VpnConnectionState): String = when (state) {
    VpnConnectionState.Disconnected -> "Защита работает в фоне"
    VpnConnectionState.Connecting -> "Проверяем сеть и создаём\nзащищённое подключение"
    VpnConnectionState.Connected -> "Трафик защищён и соединение активно"
}

@Composable
private fun HeaderBar(
    onSettingsClick: () -> Unit,
) {
    Box(
        modifier = Modifier.fillMaxWidth(),
        contentAlignment = Alignment.Center,
    ) {
        Text(
            text = "skryon",
            style = MaterialTheme.typography.headlineMedium,
            fontWeight = FontWeight.SemiBold,
            color = VpnPremiumTokens.Colors.TextPrimary,
        )

        SettingsCircleButton(
            onClick = onSettingsClick,
            modifier = Modifier.align(Alignment.CenterEnd),
        )
    }
}

@Composable
private fun StatusBeacon(
    connectionState: VpnConnectionState,
) {
    val pulse = if (connectionState == VpnConnectionState.Connecting) {
        rememberInfiniteTransition(label = "status-beacon").animateFloat(
            initialValue = 0.94f,
            targetValue = 1.04f,
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
        VpnConnectionState.Disconnected -> 0.10f
        VpnConnectionState.Connecting -> 0.14f
        VpnConnectionState.Connected -> 0.12f
    }
    val outerAlpha = when (connectionState) {
        VpnConnectionState.Disconnected -> 0.05f
        VpnConnectionState.Connecting -> 0.08f
        VpnConnectionState.Connected -> 0.06f
    }

    Box(
        modifier = Modifier.size(92.dp),
        contentAlignment = Alignment.Center,
    ) {
        Box(
            modifier = Modifier
                .size(92.dp)
                .clip(CircleShape)
                .background(coreColor.copy(alpha = outerAlpha))
        )
        Box(
            modifier = Modifier
                .size((58 * pulse).dp)
                .clip(CircleShape)
                .background(coreColor.copy(alpha = middleAlpha))
        )
        Box(
            modifier = Modifier
                .size(38.dp)
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
                .padding(horizontal = 16.dp, vertical = 10.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                text = selectedLocation.title,
                style = MaterialTheme.typography.bodyLarge,
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
private fun DisconnectedCard() {
    SurfaceCard {
        InfoRow(
            title = "Состояние",
            value = "VPN сейчас не подключён",
            note = "Включите защиту вручную или дождитесь риска",
        )
        RowDivider()
        InfoRow(
            title = "Автозащита",
            value = "Следит за сетью",
            note = "Включится автоматически при риске",
        )
        RowDivider()
        InfoRow(
            title = "Утечек не обнаружено",
            value = "Проверено только что",
        )
    }
}

@Composable
private fun ConnectedCard(
    duration: String,
) {
    SurfaceCard {
        Row(
            modifier = Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.Top,
            horizontalArrangement = Arrangement.SpaceBetween,
        ) {
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text = "Подключение",
                    style = MaterialTheme.typography.bodyLarge,
                    color = VpnPremiumTokens.Colors.TextSecondary,
                )

                Spacer(modifier = Modifier.height(8.dp))

                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(
                        text = "В сети",
                        style = MaterialTheme.typography.headlineSmall,
                        color = VpnPremiumTokens.Colors.TextPrimary,
                        fontWeight = FontWeight.SemiBold,
                    )
                    Spacer(modifier = Modifier.width(10.dp))
                    Box(
                        modifier = Modifier
                            .size(10.dp)
                            .clip(CircleShape)
                            .background(VpnPremiumTokens.Colors.PositiveStrong),
                    )
                }

                Spacer(modifier = Modifier.height(8.dp))

                Text(
                    text = duration,
                    style = MaterialTheme.typography.titleMedium,
                    color = VpnPremiumTokens.Colors.PositiveStrong,
                    fontWeight = FontWeight.Medium,
                )
            }

            Badge(text = "VPN активен")
        }

        RowDivider()

        InfoRow(
            title = "Риск в сети",
            value = "Низкий",
            accent = VpnPremiumTokens.Colors.PositiveStrong,
        )
        RowDivider()
        InfoRow(
            title = "Утечек не обнаружено",
            value = "Проверено только что",
        )
        RowDivider()
        InfoRow(
            title = "Автозащита",
            value = "Активна в фоне",
            note = "Остаётся включённой при риске",
        )
    }
}

@Composable
private fun ConnectingCard() {
    var activeStep by remember { mutableIntStateOf(0) }

    LaunchedEffect(Unit) {
        while (true) {
            delay(550)
            activeStep = (activeStep + 1) % 4
        }
    }

    val steps = listOf(
        "Проверяем сеть" to "Оцениваем безопасность подключения",
        "Создаём защищённый маршрут" to "Устанавливаем безопасное соединение",
        "Проверяем утечки" to "Проверяем DNS и IP-утечки",
        "Включаем защиту" to "Защищаем ваш трафик",
    )

    SurfaceCard {
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
) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        verticalAlignment = Alignment.Top,
    ) {
        Column(
            modifier = Modifier.padding(top = 6.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            val dotColor = when (state) {
                ProgressState.Pending -> VpnPremiumTokens.Colors.Track
                ProgressState.Active -> VpnPremiumTokens.Colors.PositiveStrong
                ProgressState.Completed -> VpnPremiumTokens.Colors.PositiveStrong
            }

            Box(
                modifier = Modifier
                    .size(if (state == ProgressState.Active) 12.dp else 10.dp)
                    .clip(CircleShape)
                    .background(dotColor)
            )

            if (showLine) {
                Box(
                    modifier = Modifier
                        .padding(top = 6.dp)
                        .width(2.dp)
                        .height(42.dp)
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

        Spacer(modifier = Modifier.width(16.dp))

        Column(
            modifier = Modifier
                .weight(1f)
                .padding(bottom = if (showLine) 12.dp else 0.dp),
        ) {
            Text(
                text = title,
                style = MaterialTheme.typography.titleMedium,
                color = VpnPremiumTokens.Colors.TextPrimary,
                fontWeight = FontWeight.Medium,
            )
            Spacer(modifier = Modifier.height(4.dp))
            Text(
                text = note,
                style = MaterialTheme.typography.bodyMedium,
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
fun TopBarArea(
    selectedLocation: VpnLocationOption,
    locations: List<VpnLocationOption>,
    onLocationSelected: (String) -> Unit,
    onSettingsClick: () -> Unit,
    modifier: Modifier = Modifier,
) {
    Row(
        modifier = modifier.fillMaxWidth(),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        LocationSelector(
            selectedLocation = selectedLocation,
            locations = locations,
            onLocationSelected = onLocationSelected,
        )
        Spacer(modifier = Modifier.weight(1f))
        SettingsCircleButton(onClick = onSettingsClick)
    }
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
        modifier = modifier,
    )
}

@Composable
fun SettingsCircleButton(
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
) {
    Box(
        modifier = modifier
            .size(46.dp)
            .clip(RoundedCornerShape(16.dp))
            .background(Color.White.copy(alpha = 0.92f))
            .border(1.dp, VpnPremiumTokens.Colors.BorderSubtle, RoundedCornerShape(16.dp))
            .clickable(onClick = onClick),
        contentAlignment = Alignment.Center,
    ) {
        Icon(
            imageVector = Icons.Outlined.Settings,
            contentDescription = "Settings",
            tint = VpnPremiumTokens.Colors.TextPrimary,
        )
    }
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
private fun SurfaceCard(
    content: @Composable ColumnScope.() -> Unit,
) {
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(30.dp))
            .background(VpnPremiumTokens.Colors.Surface)
            .border(1.dp, VpnPremiumTokens.Colors.BorderSubtle, RoundedCornerShape(30.dp))
            .padding(horizontal = 22.dp, vertical = 22.dp),
        content = content,
    )
}

@Composable
private fun InfoRow(
    title: String,
    value: String,
    note: String? = null,
    accent: Color = VpnPremiumTokens.Colors.TextPrimary,
) {
    Column(modifier = Modifier.fillMaxWidth()) {
        Text(
            text = title,
            style = MaterialTheme.typography.bodyLarge,
            color = VpnPremiumTokens.Colors.TextSecondary,
        )
        Spacer(modifier = Modifier.height(8.dp))
        Text(
            text = value,
            style = MaterialTheme.typography.titleLarge,
            color = accent,
            fontWeight = FontWeight.Medium,
        )
        if (!note.isNullOrBlank()) {
            Spacer(modifier = Modifier.height(6.dp))
            Text(
                text = note,
                style = MaterialTheme.typography.bodyMedium,
                color = VpnPremiumTokens.Colors.TextSecondary,
            )
        }
    }
}

@Composable
private fun RowDivider() {
    Box(
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 18.dp)
            .height(1.dp)
            .background(VpnPremiumTokens.Colors.BorderSubtle)
    )
}

@Composable
private fun Badge(text: String) {
    Box(
        modifier = Modifier
            .clip(RoundedCornerShape(999.dp))
            .background(VpnPremiumTokens.Colors.PositiveSoft)
            .padding(horizontal = 14.dp, vertical = 8.dp),
        contentAlignment = Alignment.Center,
    ) {
        Text(
            text = text,
            style = MaterialTheme.typography.bodyMedium,
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
            .height(72.dp),
        shape = RoundedCornerShape(24.dp),
        colors = ButtonDefaults.buttonColors(
            containerColor = containerColor,
            contentColor = VpnPremiumTokens.Colors.ButtonText,
            disabledContainerColor = containerColor.copy(alpha = 0.65f),
            disabledContentColor = VpnPremiumTokens.Colors.ButtonText.copy(alpha = 0.7f),
        ),
    ) {
        Text(
            text = label,
            style = MaterialTheme.typography.titleLarge,
            fontWeight = FontWeight.Medium,
        )
    }
}

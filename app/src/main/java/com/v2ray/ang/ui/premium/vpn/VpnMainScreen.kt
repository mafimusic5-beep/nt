package com.v2ray.ang.ui.premium.vpn

import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.EaseInOutSine
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
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
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
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
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp

@Composable
fun VpnMainRoute(
    viewModel: VpnMainViewModel,
    requestVpnPermission: ((onGranted: () -> Unit) -> Unit),
    startVpnService: (String) -> Boolean,
    stopVpnService: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val uiState by viewModel.uiState.collectAsState()
    VpnMainScreen(
        uiState = uiState,
        locations = uiState.locations,
        onLocationSelected = viewModel::onLocationSelected,
        onConnectClick = {
            requestVpnPermission {
                viewModel.onConnectClick(startVpnService)
            }
        },
        onDisconnectClick = { viewModel.onDisconnectClick(stopVpnService) },
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
            .background(Color.White)
            .statusBarsPadding()
            .navigationBarsPadding()
            .imePadding(),
    ) {
        val compact = maxHeight < 830.dp
        val tight = maxHeight < 730.dp
        val horizontalPadding = if (tight) 18.dp else 24.dp

        val illustrationTop = when {
            tight -> 250.dp
            compact -> 282.dp
            else -> 308.dp
        }
        val illustrationHeight = when {
            tight -> 320.dp
            compact -> 390.dp
            else -> 430.dp
        }

        ParisIllustration(
            modifier = Modifier
                .align(Alignment.TopCenter)
                .padding(top = illustrationTop)
                .fillMaxWidth()
                .height(illustrationHeight),
        )

        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(
                    start = horizontalPadding,
                    top = if (tight) 8.dp else 14.dp,
                    end = horizontalPadding,
                    bottom = if (tight) 8.dp else 12.dp,
                ),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            HeaderBar(
                selectedLocation = uiState.selectedLocation,
                locations = locations,
                onLocationSelected = onLocationSelected,
                compact = compact,
                isLoading = uiState.locationsLoading,
            )

            Spacer(Modifier.height(if (tight) 16.dp else if (compact) 26.dp else 36.dp))

            StatusBeacon(
                connectionState = uiState.connectionState,
                compact = compact,
                tight = tight,
            )

            Spacer(Modifier.height(if (tight) 8.dp else 14.dp))

            Text(
                text = screenTitle(uiState.connectionState),
                style = if (tight) MaterialTheme.typography.headlineSmall else MaterialTheme.typography.headlineMedium,
                fontWeight = FontWeight.SemiBold,
                color = VpnPremiumTokens.Colors.TextPrimary,
                textAlign = TextAlign.Center,
                maxLines = 1,
            )

            Spacer(Modifier.height(if (tight) 4.dp else 8.dp))

            Text(
                text = screenSubtitle(uiState.connectionState),
                style = if (tight) MaterialTheme.typography.bodyMedium else MaterialTheme.typography.titleMedium,
                color = VpnPremiumTokens.Colors.TextSecondary,
                textAlign = TextAlign.Center,
                maxLines = 1,
            )

            Spacer(Modifier.height(if (tight) 10.dp else 16.dp))

            ProtectionPill(uiState.connectionState, compact = compact)

            if (uiState.locationsError.isNotBlank()) {
                Spacer(Modifier.height(if (tight) 4.dp else 6.dp))
                Text(
                    text = uiState.locationsError,
                    style = MaterialTheme.typography.bodySmall,
                    color = VpnPremiumTokens.Colors.TextSecondary,
                    textAlign = TextAlign.Center,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
            }

            Spacer(Modifier.height(if (tight) 34.dp else if (compact) 62.dp else 84.dp))

            ConnectionInfoCard(
                uiState = uiState,
                compact = compact,
                tight = tight,
            )

            Spacer(Modifier.height(if (tight) 16.dp else 22.dp))

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
                Spacer(Modifier.height(if (tight) 6.dp else 10.dp))
                Text(
                    text = when {
                        uiState.activationKey.isBlank() -> "Ключ доступа не найден"
                        uiState.locationsLoading -> "Список регионов загружается"
                        uiState.locationsError.isNotBlank() -> uiState.locationsError
                        else -> "Регион недоступен"
                    },
                    style = MaterialTheme.typography.bodyMedium,
                    color = VpnPremiumTokens.Colors.TextSecondary,
                    textAlign = TextAlign.Center,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
            }

            Spacer(Modifier.weight(1f))
            BottomNavigationBar(compact = compact)
        }
    }
}

private fun screenTitle(state: VpnConnectionState): String = when (state) {
    VpnConnectionState.Disconnected -> "Защита выключена"
    VpnConnectionState.Connecting -> "Включаем защиту"
    VpnConnectionState.Connected -> "Защита включена"
}

private fun screenSubtitle(state: VpnConnectionState): String = when (state) {
    VpnConnectionState.Disconnected -> "Ваше соединение не защищено"
    VpnConnectionState.Connecting -> "Создаём защищённое подключение"
    VpnConnectionState.Connected -> "Ваше соединение безопасно"
}

@Composable
private fun HeaderBar(
    selectedLocation: VpnLocationOption,
    locations: List<VpnLocationOption>,
    onLocationSelected: (String) -> Unit,
    compact: Boolean,
    isLoading: Boolean,
) {
    val meta = selectedLocation.toLocationMeta()
    val locationText = if (isLoading) "Серверы" else meta.city
    val sideWidth = if (compact) 118.dp else 132.dp

    Row(
        modifier = Modifier
            .fillMaxWidth()
            .height(if (compact) 48.dp else 54.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Row(
            modifier = Modifier.width(sideWidth),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                text = "Skryon",
                style = if (compact) MaterialTheme.typography.headlineSmall else MaterialTheme.typography.headlineMedium,
                fontWeight = FontWeight.SemiBold,
                color = VpnPremiumTokens.Colors.TextPrimary,
                maxLines = 1,
            )
            Spacer(Modifier.width(7.dp))
            Text(
                text = "VPN",
                style = MaterialTheme.typography.titleSmall,
                color = VpnPremiumTokens.Colors.TextSecondary,
                maxLines = 1,
            )
        }

        Box(
            modifier = Modifier.weight(1f),
            contentAlignment = Alignment.Center,
        ) {
            HeaderLocationSelector(
                displayText = locationText,
                locations = locations,
                onLocationSelected = onLocationSelected,
                compact = compact,
            )
        }

        Box(
            modifier = Modifier.width(if (compact) 50.dp else 56.dp),
            contentAlignment = Alignment.CenterEnd,
        ) {
            MenuCircleButton(compact = compact)
        }
    }
}

@Composable
private fun HeaderLocationSelector(
    displayText: String,
    locations: List<VpnLocationOption>,
    onLocationSelected: (String) -> Unit,
    compact: Boolean,
    modifier: Modifier = Modifier,
) {
    var expanded by remember { mutableStateOf(false) }

    Box(modifier = modifier) {
        Row(
            modifier = Modifier
                .widthIn(max = if (compact) 104.dp else 128.dp)
                .clip(RoundedCornerShape(999.dp))
                .clickable { expanded = locations.isNotEmpty() }
                .padding(horizontal = 8.dp, vertical = 6.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.Center,
        ) {
            Text(
                text = displayText,
                style = if (compact) MaterialTheme.typography.titleMedium else MaterialTheme.typography.titleLarge,
                color = VpnPremiumTokens.Colors.TextPrimary,
                fontWeight = FontWeight.Medium,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
                textAlign = TextAlign.Center,
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
                            text = location.toLocationMeta().city,
                            color = VpnPremiumTokens.Colors.TextPrimary,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
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
private fun MenuCircleButton(
    modifier: Modifier = Modifier,
    compact: Boolean,
) {
    Box(
        modifier = modifier
            .size(if (compact) 42.dp else 48.dp)
            .clip(RoundedCornerShape(16.dp))
            .background(Color.White.copy(alpha = 0.92f))
            .border(1.dp, VpnPremiumTokens.Colors.BorderSubtle, RoundedCornerShape(16.dp)),
        contentAlignment = Alignment.Center,
    ) {
        Canvas(Modifier.size(18.dp)) {
            val stroke = 2.2.dp.toPx()
            drawLine(VpnPremiumTokens.Colors.TextPrimary, Offset(size.width * 0.12f, size.height * 0.25f), Offset(size.width * 0.88f, size.height * 0.25f), strokeWidth = stroke, cap = StrokeCap.Round)
            drawLine(VpnPremiumTokens.Colors.TextPrimary, Offset(size.width * 0.12f, size.height * 0.50f), Offset(size.width * 0.88f, size.height * 0.50f), strokeWidth = stroke, cap = StrokeCap.Round)
            drawLine(VpnPremiumTokens.Colors.TextPrimary, Offset(size.width * 0.12f, size.height * 0.75f), Offset(size.width * 0.88f, size.height * 0.75f), strokeWidth = stroke, cap = StrokeCap.Round)
        }
    }
}

@Composable
private fun StatusBeacon(
    connectionState: VpnConnectionState,
    compact: Boolean = false,
    tight: Boolean = false,
) {
    val pulse = if (connectionState == VpnConnectionState.Connecting) {
        rememberInfiniteTransition(label = "status-beacon").animateFloat(
            initialValue = 0.96f,
            targetValue = 1.04f,
            animationSpec = infiniteRepeatable(tween(900, easing = EaseInOutSine), RepeatMode.Reverse),
            label = "beacon-pulse",
        ).value
    } else {
        1f
    }
    val coreColor by animateColorAsState(
        targetValue = if (connectionState == VpnConnectionState.Connected) VpnPremiumTokens.Colors.PositiveStrong else VpnPremiumTokens.Colors.Positive,
        label = "beacon-core",
    )
    val beaconSize = when {
        tight -> 66.dp
        compact -> 82.dp
        else -> 96.dp
    }
    val midSize = when {
        tight -> 48.dp
        compact -> 58.dp
        else -> 66.dp
    }
    val coreSize = when {
        tight -> 24.dp
        compact -> 28.dp
        else -> 32.dp
    }
    Box(Modifier.size(beaconSize), contentAlignment = Alignment.Center) {
        Box(Modifier.size(beaconSize).clip(CircleShape).background(coreColor.copy(alpha = 0.035f)))
        Box(Modifier.size((midSize.value * pulse).dp).clip(CircleShape).background(coreColor.copy(alpha = 0.09f)))
        Box(Modifier.size(coreSize).clip(CircleShape).background(coreColor))
    }
}

@Composable
private fun ProtectionPill(state: VpnConnectionState, compact: Boolean) {
    val text = when (state) {
        VpnConnectionState.Disconnected -> "VPN выключен"
        VpnConnectionState.Connecting -> "VPN включается"
        VpnConnectionState.Connected -> "VPN активен"
    }
    Box(
        modifier = Modifier
            .clip(RoundedCornerShape(999.dp))
            .background(Color.White.copy(alpha = 0.96f))
            .border(1.dp, VpnPremiumTokens.Colors.BorderSubtle, RoundedCornerShape(999.dp))
            .padding(horizontal = if (compact) 18.dp else 22.dp, vertical = if (compact) 9.dp else 11.dp),
        contentAlignment = Alignment.Center,
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            MiniIcon(type = MiniIconType.Lock, tint = VpnPremiumTokens.Colors.PositiveStrong, size = 19.dp)
            Spacer(Modifier.width(12.dp))
            Text(
                text = text,
                style = if (compact) MaterialTheme.typography.bodyLarge else MaterialTheme.typography.titleMedium,
                color = VpnPremiumTokens.Colors.PositiveStrong,
                fontWeight = FontWeight.Medium,
                maxLines = 1,
            )
        }
    }
}

@Composable
private fun ConnectionInfoCard(
    uiState: VpnMainUiState,
    compact: Boolean,
    tight: Boolean,
) {
    val meta = uiState.selectedLocation.toLocationMeta()
    val duration = if (uiState.connectionState == VpnConnectionState.Connected) uiState.formattedDuration else "00:00:00"

    SurfaceCard(compact, tight) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.Top,
            horizontalArrangement = Arrangement.SpaceBetween,
        ) {
            Column(Modifier.weight(1f)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Box(Modifier.size(5.dp).clip(CircleShape).background(VpnPremiumTokens.Colors.TextSecondary.copy(alpha = 0.70f)))
                    Spacer(Modifier.width(10.dp))
                    Text(
                        text = "ПОДКЛЮЧЕНИЕ",
                        style = MaterialTheme.typography.bodySmall,
                        color = VpnPremiumTokens.Colors.TextSecondary,
                        fontWeight = FontWeight.Medium,
                        maxLines = 1,
                    )
                }
                Spacer(Modifier.height(if (tight) 12.dp else 16.dp))
                Text(
                    text = meta.city,
                    style = if (tight) MaterialTheme.typography.titleLarge else MaterialTheme.typography.headlineSmall,
                    color = VpnPremiumTokens.Colors.TextPrimary,
                    fontWeight = FontWeight.Medium,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                Spacer(Modifier.height(if (tight) 3.dp else 5.dp))
                Text(
                    text = meta.country.ifBlank { "Основной сервер" },
                    style = if (compact) MaterialTheme.typography.bodyMedium else MaterialTheme.typography.bodyLarge,
                    color = VpnPremiumTokens.Colors.TextSecondary,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                Spacer(Modifier.height(if (tight) 14.dp else 20.dp))
                FlagIcon(style = meta.flagStyle, compact = compact)
            }

            Row(
                modifier = Modifier.padding(top = if (tight) 28.dp else 34.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                SignalBars(compact = compact)
                Spacer(Modifier.width(10.dp))
                Text(
                    text = "Стабильное",
                    style = if (compact) MaterialTheme.typography.bodyMedium else MaterialTheme.typography.bodyLarge,
                    color = VpnPremiumTokens.Colors.TextSecondary,
                    maxLines = 1,
                )
            }
        }

        RowDivider(compact, tight)

        Row(
            modifier = Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            CardMetric(
                iconType = MiniIconType.Globe,
                title = "IP адрес",
                value = "Скрыт",
                compact = compact,
                modifier = Modifier.weight(1f),
            )
            VerticalDivider(compact = compact)
            CardMetric(
                iconType = MiniIconType.Shield,
                title = "Протокол",
                value = "WireGuard",
                compact = compact,
                modifier = Modifier.weight(1f),
                valueColor = VpnPremiumTokens.Colors.TextPrimary,
            )
            VerticalDivider(compact = compact)
            CardMetric(
                iconType = MiniIconType.Clock,
                title = "Время",
                value = duration,
                compact = compact,
                modifier = Modifier.weight(1f),
            )
        }
    }
}

@Composable
private fun CardMetric(
    iconType: MiniIconType,
    title: String,
    value: String,
    compact: Boolean,
    modifier: Modifier = Modifier,
    valueColor: Color = VpnPremiumTokens.Colors.PositiveStrong,
) {
    Row(
        modifier = modifier,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        MiniIcon(type = iconType, tint = VpnPremiumTokens.Colors.TextPrimary.copy(alpha = 0.82f), size = if (compact) 23.dp else 27.dp)
        Spacer(Modifier.width(if (compact) 8.dp else 10.dp))
        Column {
            Text(
                text = title,
                style = MaterialTheme.typography.bodySmall,
                color = VpnPremiumTokens.Colors.TextSecondary,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            Spacer(Modifier.height(3.dp))
            Text(
                text = value,
                style = if (compact) MaterialTheme.typography.bodyMedium else MaterialTheme.typography.bodyLarge,
                color = valueColor,
                fontWeight = FontWeight.Medium,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
        }
    }
}

@Composable
private fun BottomNavigationBar(compact: Boolean) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .height(if (compact) 62.dp else 70.dp)
            .clip(RoundedCornerShape(if (compact) 22.dp else 26.dp))
            .background(Color.White.copy(alpha = 0.94f))
            .border(1.dp, VpnPremiumTokens.Colors.BorderSubtle, RoundedCornerShape(if (compact) 22.dp else 26.dp))
            .padding(horizontal = if (compact) 10.dp else 14.dp, vertical = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.SpaceBetween,
    ) {
        BottomNavItem("Главная", MiniIconType.Home, selected = true, compact = compact, modifier = Modifier.weight(1f))
        BottomNavItem("Сервера", MiniIconType.Globe, selected = false, compact = compact, modifier = Modifier.weight(1f))
        BottomNavItem("Профиль", MiniIconType.Profile, selected = false, compact = compact, modifier = Modifier.weight(1f))
    }
}

@Composable
private fun BottomNavItem(
    label: String,
    iconType: MiniIconType,
    selected: Boolean,
    compact: Boolean,
    modifier: Modifier = Modifier,
) {
    Row(
        modifier = modifier
            .fillMaxHeight()
            .clip(RoundedCornerShape(18.dp))
            .background(if (selected) Color.White else Color.Transparent)
            .padding(horizontal = if (compact) 6.dp else 10.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.Center,
    ) {
        MiniIcon(
            type = iconType,
            tint = if (selected) VpnPremiumTokens.Colors.TextPrimary else VpnPremiumTokens.Colors.TextSecondary,
            size = if (compact) 19.dp else 22.dp,
        )
        Spacer(Modifier.width(if (compact) 7.dp else 9.dp))
        Text(
            text = label,
            style = if (compact) MaterialTheme.typography.bodyMedium else MaterialTheme.typography.bodyLarge,
            color = if (selected) VpnPremiumTokens.Colors.TextPrimary else VpnPremiumTokens.Colors.TextSecondary,
            fontWeight = if (selected) FontWeight.Medium else FontWeight.Normal,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
        )
    }
}

@Composable
private fun ParisIllustration(modifier: Modifier = Modifier) {
    Canvas(modifier = modifier) {
        val w = size.width
        val h = size.height
        val cloud = Color(0xFFEFF3F6)
        val mountain = Color(0xFFE8EEF1)
        val city = Color(0xFFD0D9DE)
        val line = Color(0xFFAAB7BF)
        val river = Color(0xFFE4F2F8)
        val green = Color(0xFFBED8B4)

        drawRect(
            brush = Brush.verticalGradient(
                0f to Color.White.copy(alpha = 0f),
                0.24f to Color(0xFFF6FAFC).copy(alpha = 0.60f),
                0.70f to Color(0xFFF3FAFD).copy(alpha = 0.46f),
                1f to Color.White.copy(alpha = 0.12f),
            )
        )

        drawCircle(cloud.copy(alpha = 0.66f), radius = w * 0.20f, center = Offset(w * 0.16f, h * 0.20f))
        drawCircle(cloud.copy(alpha = 0.54f), radius = w * 0.18f, center = Offset(w * 0.82f, h * 0.17f))
        drawCircle(cloud.copy(alpha = 0.46f), radius = w * 0.13f, center = Offset(w * 0.50f, h * 0.24f))

        val leftMountain = Path().apply {
            moveTo(0f, h * 0.42f)
            lineTo(w * 0.16f, h * 0.10f)
            lineTo(w * 0.34f, h * 0.44f)
            lineTo(w * 0.48f, h * 0.22f)
            lineTo(w * 0.62f, h * 0.44f)
            lineTo(0f, h * 0.44f)
            close()
        }
        drawPath(leftMountain, mountain.copy(alpha = 0.58f))

        val rightMountain = Path().apply {
            moveTo(w * 0.42f, h * 0.44f)
            lineTo(w * 0.64f, h * 0.18f)
            lineTo(w * 0.78f, h * 0.42f)
            lineTo(w * 0.95f, h * 0.10f)
            lineTo(w, h * 0.40f)
            lineTo(w, h * 0.46f)
            close()
        }
        drawPath(rightMountain, mountain.copy(alpha = 0.50f))

        val horizonY = h * 0.54f
        drawRoundRect(city.copy(alpha = 0.44f), topLeft = Offset(w * 0.11f, horizonY - h * 0.08f), size = Size(w * 0.05f, h * 0.08f), cornerRadius = CornerRadius(3.dp.toPx()))
        drawRoundRect(city.copy(alpha = 0.48f), topLeft = Offset(w * 0.19f, horizonY - h * 0.13f), size = Size(w * 0.055f, h * 0.13f), cornerRadius = CornerRadius(3.dp.toPx()))
        drawRoundRect(city.copy(alpha = 0.54f), topLeft = Offset(w * 0.29f, horizonY - h * 0.20f), size = Size(w * 0.06f, h * 0.20f), cornerRadius = CornerRadius(3.dp.toPx()))
        drawRoundRect(city.copy(alpha = 0.40f), topLeft = Offset(w * 0.38f, horizonY - h * 0.10f), size = Size(w * 0.045f, h * 0.10f), cornerRadius = CornerRadius(3.dp.toPx()))
        drawRoundRect(city.copy(alpha = 0.40f), topLeft = Offset(w * 0.73f, horizonY - h * 0.09f), size = Size(w * 0.07f, h * 0.09f), cornerRadius = CornerRadius(3.dp.toPx()))
        drawRoundRect(city.copy(alpha = 0.36f), topLeft = Offset(w * 0.84f, horizonY - h * 0.12f), size = Size(w * 0.09f, h * 0.12f), cornerRadius = CornerRadius(3.dp.toPx()))

        drawParisTower(
            base = Offset(w * 0.64f, horizonY + h * 0.01f),
            height = h * 0.45f,
            color = line.copy(alpha = 0.70f),
            strokeWidth = 1.35.dp.toPx(),
        )

        drawLine(
            color = line.copy(alpha = 0.46f),
            start = Offset(w * 0.04f, horizonY),
            end = Offset(w * 0.96f, horizonY),
            strokeWidth = 1.dp.toPx(),
        )

        drawBridge(
            start = Offset(w * 0.07f, horizonY + h * 0.04f),
            end = Offset(w * 0.93f, horizonY + h * 0.04f),
            archTop = horizonY - h * 0.06f,
            color = line.copy(alpha = 0.42f),
            strokeWidth = 1.6.dp.toPx(),
        )

        repeat(12) { index ->
            val x = w * 0.05f + index * (w * 0.075f)
            drawCircle(green.copy(alpha = 0.50f), radius = h * 0.022f, center = Offset(x, horizonY + h * 0.02f))
        }

        val riverPath = Path().apply {
            moveTo(0f, horizonY + h * 0.02f)
            cubicTo(w * 0.24f, horizonY + h * 0.08f, w * 0.72f, horizonY + h * 0.01f, w, horizonY + h * 0.07f)
            lineTo(w, h)
            lineTo(0f, h)
            close()
        }
        drawPath(riverPath, river.copy(alpha = 0.58f))
        drawLine(
            color = Color.White.copy(alpha = 0.42f),
            start = Offset(0f, horizonY + h * 0.13f),
            end = Offset(w, horizonY + h * 0.09f),
            strokeWidth = 2.dp.toPx(),
        )
        drawRect(
            brush = Brush.verticalGradient(
                0f to Color.White.copy(alpha = 0f),
                1f to Color.White.copy(alpha = 0.64f),
            ),
            topLeft = Offset(0f, h * 0.68f),
            size = Size(w, h * 0.32f),
        )
    }
}

private fun DrawScope.drawParisTower(
    base: Offset,
    height: Float,
    color: Color,
    strokeWidth: Float,
) {
    val top = Offset(base.x, base.y - height)
    val leftBase = Offset(base.x - height * 0.15f, base.y)
    val rightBase = Offset(base.x + height * 0.15f, base.y)
    val midLeft = Offset(base.x - height * 0.06f, base.y - height * 0.45f)
    val midRight = Offset(base.x + height * 0.06f, base.y - height * 0.45f)

    drawLine(color, top, leftBase, strokeWidth, cap = StrokeCap.Round)
    drawLine(color, top, rightBase, strokeWidth, cap = StrokeCap.Round)
    drawLine(color, midLeft, midRight, strokeWidth, cap = StrokeCap.Round)
    drawLine(color, Offset(base.x - height * 0.10f, base.y - height * 0.20f), Offset(base.x + height * 0.10f, base.y - height * 0.20f), strokeWidth, cap = StrokeCap.Round)
    drawLine(color.copy(alpha = 0.72f), Offset(base.x, base.y - height * 0.94f), top.copy(y = top.y - height * 0.10f), strokeWidth, cap = StrokeCap.Round)

    repeat(4) { index ->
        val y = base.y - height * (0.12f + index * 0.14f)
        val dx = height * (0.13f - index * 0.022f)
        drawLine(color.copy(alpha = 0.35f), Offset(base.x - dx, y), Offset(base.x + dx, y - height * 0.07f), strokeWidth * 0.72f)
        drawLine(color.copy(alpha = 0.35f), Offset(base.x + dx, y), Offset(base.x - dx, y - height * 0.07f), strokeWidth * 0.72f)
    }
}

private fun DrawScope.drawBridge(
    start: Offset,
    end: Offset,
    archTop: Float,
    color: Color,
    strokeWidth: Float,
) {
    drawLine(color, start, end, strokeWidth, cap = StrokeCap.Round)
    val arch = Path().apply {
        moveTo(start.x + (end.x - start.x) * 0.15f, start.y)
        cubicTo(
            start.x + (end.x - start.x) * 0.32f,
            archTop,
            start.x + (end.x - start.x) * 0.68f,
            archTop,
            end.x - (end.x - start.x) * 0.15f,
            end.y,
        )
    }
    drawPath(arch, color.copy(alpha = 0.62f), style = Stroke(width = strokeWidth, cap = StrokeCap.Round))
    repeat(7) { index ->
        val x = start.x + (end.x - start.x) * (0.20f + index * 0.10f)
        drawLine(color.copy(alpha = 0.30f), Offset(x, start.y), Offset(x, start.y + 15.dp.toPx()), strokeWidth * 0.7f)
    }
}

@Composable
fun HumanSilhouetteBlock(uiState: VpnMainUiState, modifier: Modifier = Modifier) {
    Box(modifier = modifier, contentAlignment = Alignment.Center) { StatusBeacon(uiState.connectionState) }
}

@Composable
fun HologramManBlock(uiState: VpnMainUiState, modifier: Modifier = Modifier) {
    HumanSilhouetteBlock(uiState = uiState, modifier = modifier)
}

@Composable
fun LocationSelector(
    selectedLocation: VpnLocationOption,
    locations: List<VpnLocationOption>,
    onLocationSelected: (String) -> Unit,
    modifier: Modifier = Modifier,
) {
    RouteSelectorChip(selectedLocation, locations, onLocationSelected, compact = false, modifier = modifier)
}

@Composable
fun ConnectionTimer(time: String) {
    Text(time, style = MaterialTheme.typography.headlineMedium, color = VpnPremiumTokens.Colors.TextPrimary, fontWeight = FontWeight.Medium)
}

@Composable
fun ConnectionStatusOverlay(uiState: VpnMainUiState, modifier: Modifier = Modifier) {
    Box(modifier = modifier)
}

@Composable
fun ActivationKeyField(value: String, onValueChange: (String) -> Unit, enabled: Boolean, modifier: Modifier = Modifier) {
    Box(modifier = modifier)
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
    val meta = selectedLocation.toLocationMeta()
    Box {
        Row(
            modifier = modifier
                .clip(RoundedCornerShape(18.dp))
                .background(VpnPremiumTokens.Colors.Surface)
                .border(1.dp, VpnPremiumTokens.Colors.BorderSubtle, RoundedCornerShape(18.dp))
                .clickable { expanded = locations.isNotEmpty() }
                .padding(horizontal = if (compact) 14.dp else 16.dp, vertical = if (compact) 8.dp else 10.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                text = meta.city,
                style = if (compact) MaterialTheme.typography.bodyMedium else MaterialTheme.typography.bodyLarge,
                color = VpnPremiumTokens.Colors.TextPrimary,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            Spacer(Modifier.width(8.dp))
            Icon(Icons.Rounded.KeyboardArrowDown, contentDescription = null, tint = VpnPremiumTokens.Colors.TextSecondary)
        }
        DropdownMenu(expanded = expanded, onDismissRequest = { expanded = false }, modifier = Modifier.background(Color.White)) {
            locations.forEach { location ->
                DropdownMenuItem(
                    text = { Text(location.toLocationMeta().city, color = VpnPremiumTokens.Colors.TextPrimary) },
                    onClick = { expanded = false; onLocationSelected(location.title) },
                )
            }
        }
    }
}

@Composable
private fun SurfaceCard(compact: Boolean, tight: Boolean, content: @Composable ColumnScope.() -> Unit) {
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(if (compact) 24.dp else 30.dp))
            .background(Color.White.copy(alpha = 0.72f))
            .border(
                1.dp,
                VpnPremiumTokens.Colors.BorderSubtle.copy(alpha = 0.82f),
                RoundedCornerShape(if (compact) 24.dp else 30.dp),
            )
            .padding(
                horizontal = if (tight) 16.dp else if (compact) 18.dp else 22.dp,
                vertical = if (tight) 14.dp else if (compact) 16.dp else 20.dp,
            ),
        content = content,
    )
}

@Composable
private fun RowDivider(compact: Boolean, tight: Boolean) {
    Box(Modifier.fillMaxWidth().padding(vertical = if (tight) 12.dp else if (compact) 14.dp else 18.dp).height(1.dp).background(VpnPremiumTokens.Colors.BorderSubtle.copy(alpha = 0.70f)))
}

@Composable
private fun VerticalDivider(compact: Boolean) {
    Box(
        Modifier
            .padding(horizontal = if (compact) 10.dp else 14.dp)
            .width(1.dp)
            .height(if (compact) 44.dp else 52.dp)
            .background(VpnPremiumTokens.Colors.BorderSubtle.copy(alpha = 0.70f))
    )
}

@Composable
private fun FlagIcon(style: FlagStyle, compact: Boolean) {
    val width = if (compact) 28.dp else 34.dp
    val height = if (compact) 20.dp else 24.dp
    Row(
        modifier = Modifier
            .size(width = width, height = height)
            .clip(RoundedCornerShape(3.dp))
            .border(0.5.dp, VpnPremiumTokens.Colors.BorderSubtle, RoundedCornerShape(3.dp)),
    ) {
        val colors = when (style) {
            FlagStyle.France -> listOf(Color(0xFF1B4CB8), Color.White, Color(0xFFE43D42))
            FlagStyle.Germany -> listOf(Color(0xFF161616), Color(0xFFDD2028), Color(0xFFFFCE33))
            FlagStyle.Neutral -> listOf(Color(0xFFE8ECEF), Color.White, Color(0xFFDDE4E8))
        }
        if (style == FlagStyle.Germany) {
            Column(Modifier.fillMaxSize()) {
                colors.forEach { color -> Box(Modifier.weight(1f).fillMaxWidth().background(color)) }
            }
        } else {
            colors.forEach { color -> Box(Modifier.weight(1f).fillMaxSize().background(color)) }
        }
    }
}

@Composable
private fun SignalBars(compact: Boolean) {
    Row(
        modifier = Modifier.height(if (compact) 20.dp else 24.dp),
        verticalAlignment = Alignment.Bottom,
    ) {
        listOf(0.45f, 0.70f, 1f).forEachIndexed { index, fraction ->
            Box(
                Modifier
                    .width(if (compact) 3.dp else 4.dp)
                    .height(((if (compact) 18f else 22f) * fraction).dp)
                    .clip(RoundedCornerShape(999.dp))
                    .background(VpnPremiumTokens.Colors.PositiveStrong)
            )
            if (index != 2) Spacer(Modifier.width(4.dp))
        }
    }
}

private enum class MiniIconType { Lock, Shield, Globe, Clock, Home, Profile }

@Composable
private fun MiniIcon(type: MiniIconType, tint: Color, size: Dp) {
    Canvas(Modifier.size(size)) {
        val sw = this.size.width
        val sh = this.size.height
        val minSide = this.size.minDimension
        val stroke = (minSide * 0.08f).coerceAtLeast(1.5f)
        when (type) {
            MiniIconType.Lock -> {
                drawRoundRect(
                    color = tint,
                    topLeft = Offset(sw * 0.24f, sh * 0.44f),
                    size = Size(sw * 0.52f, sh * 0.40f),
                    cornerRadius = CornerRadius(sw * 0.06f),
                    style = Stroke(width = stroke),
                )
                drawArc(
                    color = tint,
                    startAngle = 200f,
                    sweepAngle = 140f,
                    useCenter = false,
                    topLeft = Offset(sw * 0.32f, sh * 0.16f),
                    size = Size(sw * 0.36f, sh * 0.45f),
                    style = Stroke(width = stroke, cap = StrokeCap.Round),
                )
                drawCircle(tint, radius = stroke * 0.65f, center = Offset(sw * 0.50f, sh * 0.62f))
            }
            MiniIconType.Shield -> {
                val p = Path().apply {
                    moveTo(sw * 0.50f, sh * 0.12f)
                    lineTo(sw * 0.78f, sh * 0.24f)
                    lineTo(sw * 0.73f, sh * 0.60f)
                    cubicTo(sw * 0.68f, sh * 0.76f, sw * 0.56f, sh * 0.86f, sw * 0.50f, sh * 0.90f)
                    cubicTo(sw * 0.44f, sh * 0.86f, sw * 0.32f, sh * 0.76f, sw * 0.27f, sh * 0.60f)
                    lineTo(sw * 0.22f, sh * 0.24f)
                    close()
                }
                drawPath(p, tint, style = Stroke(width = stroke, cap = StrokeCap.Round))
            }
            MiniIconType.Globe -> {
                drawCircle(tint, radius = minSide * 0.36f, center = Offset(sw / 2f, sh / 2f), style = Stroke(width = stroke))
                drawLine(tint, Offset(sw * 0.18f, sh * 0.50f), Offset(sw * 0.82f, sh * 0.50f), strokeWidth = stroke, cap = StrokeCap.Round)
                drawArc(tint, 90f, 180f, false, Offset(sw * 0.34f, sh * 0.14f), Size(sw * 0.32f, sh * 0.72f), style = Stroke(width = stroke))
                drawArc(tint, -90f, 180f, false, Offset(sw * 0.34f, sh * 0.14f), Size(sw * 0.32f, sh * 0.72f), style = Stroke(width = stroke))
            }
            MiniIconType.Clock -> {
                drawCircle(tint, radius = minSide * 0.36f, center = Offset(sw / 2f, sh / 2f), style = Stroke(width = stroke))
                drawLine(tint, Offset(sw * 0.50f, sh * 0.30f), Offset(sw * 0.50f, sh * 0.52f), strokeWidth = stroke, cap = StrokeCap.Round)
                drawLine(tint, Offset(sw * 0.50f, sh * 0.52f), Offset(sw * 0.64f, sh * 0.62f), strokeWidth = stroke, cap = StrokeCap.Round)
            }
            MiniIconType.Home -> {
                val p = Path().apply {
                    moveTo(sw * 0.18f, sh * 0.48f)
                    lineTo(sw * 0.50f, sh * 0.20f)
                    lineTo(sw * 0.82f, sh * 0.48f)
                    lineTo(sw * 0.76f, sh * 0.82f)
                    lineTo(sw * 0.28f, sh * 0.82f)
                    close()
                }
                drawPath(p, tint)
            }
            MiniIconType.Profile -> {
                drawCircle(tint, radius = minSide * 0.16f, center = Offset(sw / 2f, sh * 0.34f), style = Stroke(width = stroke))
                drawArc(
                    color = tint,
                    startAngle = 200f,
                    sweepAngle = 140f,
                    useCenter = false,
                    topLeft = Offset(sw * 0.20f, sh * 0.48f),
                    size = Size(sw * 0.60f, sh * 0.50f),
                    style = Stroke(width = stroke, cap = StrokeCap.Round),
                )
            }
        }
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
        targetValue = if (enabled) Color(0xFF101319) else Color(0xFFB9BEC6),
        label = "primary-button-color",
    )
    val label = when (state) {
        VpnConnectionState.Disconnected -> "Включить VPN"
        VpnConnectionState.Connecting -> "Включаем..."
        VpnConnectionState.Connected -> "Отключить VPN"
    }
    Button(
        onClick = onClick,
        enabled = enabled && state != VpnConnectionState.Connecting,
        modifier = modifier.fillMaxWidth().height(if (tight) 54.dp else if (compact) 60.dp else 66.dp),
        shape = RoundedCornerShape(if (compact) 22.dp else 26.dp),
        colors = ButtonDefaults.buttonColors(
            containerColor = containerColor,
            contentColor = Color.White,
            disabledContainerColor = containerColor.copy(alpha = 0.80f),
            disabledContentColor = Color.White.copy(alpha = 0.72f),
        ),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.Center) {
            if (state == VpnConnectionState.Connected || state == VpnConnectionState.Connecting) {
                PauseGlyph(tint = Color.White.copy(alpha = 0.86f), compact = compact)
                Spacer(Modifier.width(18.dp))
            }
            Text(
                text = label,
                style = if (compact) MaterialTheme.typography.titleMedium else MaterialTheme.typography.titleLarge,
                fontWeight = FontWeight.Medium,
                maxLines = 1,
            )
        }
    }
}

@Composable
private fun PauseGlyph(tint: Color, compact: Boolean) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.width(if (compact) 3.dp else 4.dp).height(if (compact) 16.dp else 18.dp).clip(RoundedCornerShape(999.dp)).background(tint))
        Spacer(Modifier.width(6.dp))
        Box(Modifier.width(if (compact) 3.dp else 4.dp).height(if (compact) 16.dp else 18.dp).clip(RoundedCornerShape(999.dp)).background(tint))
    }
}

private data class LocationMeta(
    val city: String,
    val country: String,
    val flagStyle: FlagStyle,
)

private enum class FlagStyle { France, Germany, Neutral }

private fun VpnLocationOption.toLocationMeta(): LocationMeta {
    val normalized = title.trim()
    val lower = normalized.lowercase()
    return when {
        lower.contains("paris") || lower.contains("париж") || lower.contains("france") || lower.contains("франц") ->
            LocationMeta(city = "Париж", country = "Франция", flagStyle = FlagStyle.France)
        lower.contains("frankfurt") || lower.contains("франкфурт") || lower.contains("germany") || lower.contains("герман") || lower == "de" ->
            LocationMeta(city = "Франкфурт", country = "Германия", flagStyle = FlagStyle.Germany)
        normalized.isBlank() || lower.contains("загрузка") || lower.contains("loading") || lower.contains("server") || lower.contains("сервер") ->
            LocationMeta(city = "Париж", country = "Франция", flagStyle = FlagStyle.France)
        else ->
            LocationMeta(city = normalized, country = "Основной сервер", flagStyle = FlagStyle.Neutral)
    }
}

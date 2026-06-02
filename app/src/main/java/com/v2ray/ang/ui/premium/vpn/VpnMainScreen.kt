package com.v2ray.ang.ui.premium.vpn

import androidx.compose.animation.animateColorAsState
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
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
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeCap
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
            .background(AppUiColors.Background)
            .statusBarsPadding()
            .navigationBarsPadding()
            .imePadding(),
    ) {
        val compact = maxHeight < 830.dp
        val tight = maxHeight < 730.dp
        val horizontalPadding = if (tight) 18.dp else 24.dp
        val regionText = uiState.selectedLocation.regionLabel()

        BackgroundShape(
            modifier = Modifier
                .align(Alignment.TopCenter)
                .padding(top = if (tight) 210.dp else 250.dp)
                .fillMaxWidth()
                .height(if (tight) 360.dp else 430.dp),
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
                compact = compact,
            )

            Spacer(Modifier.height(if (tight) 32.dp else if (compact) 48.dp else 64.dp))

            StatusBeacon(
                connectionState = uiState.connectionState,
                compact = compact,
                tight = tight,
            )

            Spacer(Modifier.height(if (tight) 12.dp else 18.dp))

            Text(
                text = screenTitle(uiState.connectionState),
                style = if (tight) MaterialTheme.typography.headlineSmall else MaterialTheme.typography.headlineMedium,
                fontWeight = FontWeight.SemiBold,
                color = AppUiColors.TextPrimary,
                textAlign = TextAlign.Center,
                maxLines = 1,
            )

            Spacer(Modifier.height(if (tight) 5.dp else 8.dp))

            Text(
                text = screenSubtitle(uiState.connectionState),
                style = if (tight) MaterialTheme.typography.bodyMedium else MaterialTheme.typography.titleMedium,
                color = AppUiColors.TextSecondary,
                textAlign = TextAlign.Center,
                maxLines = 1,
            )

            Spacer(Modifier.height(if (tight) 16.dp else 22.dp))

            ProtectionPill(uiState.connectionState, compact = compact)

            if (uiState.locationsError.isNotBlank()) {
                Spacer(Modifier.height(if (tight) 6.dp else 8.dp))
                Text(
                    text = uiState.locationsError,
                    style = MaterialTheme.typography.bodySmall,
                    color = AppUiColors.TextSecondary,
                    textAlign = TextAlign.Center,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
            }

            Spacer(Modifier.weight(1f))

            RegionStatusSection(
                regionText = regionText,
                state = uiState.connectionState,
                compact = compact,
                tight = tight,
            )

            Spacer(Modifier.height(if (tight) 12.dp else 16.dp))

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
                        uiState.locationsLoading -> "Регион загружается"
                        uiState.locationsError.isNotBlank() -> uiState.locationsError
                        else -> "Регион недоступен"
                    },
                    style = MaterialTheme.typography.bodyMedium,
                    color = AppUiColors.TextSecondary,
                    textAlign = TextAlign.Center,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
            }

            Spacer(Modifier.height(if (tight) 14.dp else 20.dp))

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
    compact: Boolean,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .height(if (compact) 48.dp else 54.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Row(
            modifier = Modifier.weight(1f),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                text = "Skryon",
                style = if (compact) MaterialTheme.typography.headlineSmall else MaterialTheme.typography.headlineMedium,
                fontWeight = FontWeight.SemiBold,
                color = AppUiColors.TextPrimary,
                maxLines = 1,
            )
            Spacer(Modifier.width(7.dp))
            Text(
                text = "VPN",
                style = MaterialTheme.typography.titleSmall,
                color = AppUiColors.TextSecondary,
                maxLines = 1,
            )
        }

        Text(
            text = selectedLocation.regionLabel(),
            modifier = Modifier.weight(1f),
            style = if (compact) MaterialTheme.typography.titleMedium else MaterialTheme.typography.titleLarge,
            color = AppUiColors.TextPrimary,
            fontWeight = FontWeight.Medium,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
            textAlign = TextAlign.Center,
        )

        Spacer(modifier = Modifier.weight(1f))
    }
}

@Composable
private fun StatusBeacon(
    connectionState: VpnConnectionState,
    compact: Boolean = false,
    tight: Boolean = false,
) {
    val coreColor by animateColorAsState(
        targetValue = if (connectionState == VpnConnectionState.Connected) AppUiColors.PositiveStrong else AppUiColors.Positive,
        label = "beacon-core",
    )
    val beaconSize = when {
        tight -> 72.dp
        compact -> 88.dp
        else -> 104.dp
    }
    val midSize = when {
        tight -> 52.dp
        compact -> 64.dp
        else -> 74.dp
    }
    val coreSize = when {
        tight -> 25.dp
        compact -> 30.dp
        else -> 34.dp
    }
    Box(Modifier.size(beaconSize), contentAlignment = Alignment.Center) {
        Box(Modifier.size(beaconSize).clip(CircleShape).background(coreColor.copy(alpha = 0.05f)))
        Box(Modifier.size(midSize).clip(CircleShape).background(coreColor.copy(alpha = 0.11f)))
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
            .border(1.dp, AppUiColors.Border, RoundedCornerShape(999.dp))
            .padding(horizontal = if (compact) 18.dp else 22.dp, vertical = if (compact) 9.dp else 11.dp),
        contentAlignment = Alignment.Center,
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            MiniIcon(type = MiniIconType.Lock, tint = AppUiColors.PositiveStrong, size = 19.dp)
            Spacer(Modifier.width(12.dp))
            Text(
                text = text,
                style = if (compact) MaterialTheme.typography.bodyLarge else MaterialTheme.typography.titleMedium,
                color = AppUiColors.PositiveStrong,
                fontWeight = FontWeight.Medium,
                maxLines = 1,
            )
        }
    }
}

@Composable
private fun RegionStatusSection(
    regionText: String,
    state: VpnConnectionState,
    compact: Boolean,
    tight: Boolean,
) {
    val statusText = when (state) {
        VpnConnectionState.Disconnected -> "Ожидает подключения"
        VpnConnectionState.Connecting -> "Подключение..."
        VpnConnectionState.Connected -> "Подключено"
    }
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(if (compact) 22.dp else 28.dp))
            .background(Color.White.copy(alpha = 0.86f))
            .border(1.dp, AppUiColors.Border, RoundedCornerShape(if (compact) 22.dp else 28.dp))
            .padding(
                horizontal = if (tight) 18.dp else 22.dp,
                vertical = if (tight) 14.dp else 18.dp,
            ),
    ) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.SpaceBetween,
        ) {
            Column(Modifier.weight(1f)) {
                Text(
                    text = "ТЕКУЩИЙ РЕГИОН",
                    style = MaterialTheme.typography.bodySmall,
                    color = AppUiColors.TextSecondary,
                    fontWeight = FontWeight.Medium,
                    maxLines = 1,
                )
                Spacer(Modifier.height(5.dp))
                Text(
                    text = regionText,
                    style = if (tight) MaterialTheme.typography.titleLarge else MaterialTheme.typography.headlineSmall,
                    color = AppUiColors.TextPrimary,
                    fontWeight = FontWeight.SemiBold,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
            }
            StatusBadge(text = statusText, state = state, compact = compact)
        }
    }
}

@Composable
private fun StatusBadge(text: String, state: VpnConnectionState, compact: Boolean) {
    val color = when (state) {
        VpnConnectionState.Disconnected -> AppUiColors.TextSecondary
        VpnConnectionState.Connecting -> AppUiColors.Warning
        VpnConnectionState.Connected -> AppUiColors.PositiveStrong
    }
    Row(
        modifier = Modifier
            .clip(RoundedCornerShape(999.dp))
            .background(color.copy(alpha = 0.10f))
            .padding(horizontal = if (compact) 10.dp else 12.dp, vertical = if (compact) 7.dp else 8.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(Modifier.size(7.dp).clip(CircleShape).background(color))
        Spacer(Modifier.width(7.dp))
        Text(
            text = text,
            style = MaterialTheme.typography.bodySmall,
            color = color,
            fontWeight = FontWeight.Medium,
            maxLines = 1,
        )
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
            .border(1.dp, AppUiColors.Border, RoundedCornerShape(if (compact) 22.dp else 26.dp))
            .padding(horizontal = if (compact) 8.dp else 10.dp, vertical = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.SpaceBetween,
    ) {
        BottomNavItem("Главная", MiniIconType.Home, selected = true, compact = compact, modifier = Modifier.weight(1f))
        Spacer(Modifier.width(if (compact) 8.dp else 12.dp))
        BottomNavItem("Расширенные", MiniIconType.Settings, selected = false, compact = compact, modifier = Modifier.weight(1f))
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
            .clip(RoundedCornerShape(18.dp))
            .background(if (selected) AppUiColors.SelectedSurface else Color.Transparent)
            .padding(horizontal = if (compact) 8.dp else 12.dp, vertical = if (compact) 10.dp else 12.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.Center,
    ) {
        MiniIcon(
            type = iconType,
            tint = if (selected) AppUiColors.TextPrimary else AppUiColors.TextSecondary,
            size = if (compact) 18.dp else 21.dp,
        )
        Spacer(Modifier.width(if (compact) 7.dp else 9.dp))
        Text(
            text = label,
            style = if (compact) MaterialTheme.typography.bodyMedium else MaterialTheme.typography.bodyLarge,
            color = if (selected) AppUiColors.TextPrimary else AppUiColors.TextSecondary,
            fontWeight = if (selected) FontWeight.Medium else FontWeight.Normal,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
        )
    }
}

@Composable
private fun BackgroundShape(modifier: Modifier = Modifier) {
    Canvas(modifier = modifier) {
        val w = size.width
        val h = size.height
        drawCircle(Color(0xFFF1F5F7).copy(alpha = 0.78f), radius = w * 0.30f, center = Offset(w * 0.13f, h * 0.36f))
        drawCircle(Color(0xFFF1F5F7).copy(alpha = 0.72f), radius = w * 0.31f, center = Offset(w * 0.85f, h * 0.34f))
        val center = Path().apply {
            moveTo(w * 0.50f, h * 0.04f)
            lineTo(w * 0.92f, h * 0.74f)
            lineTo(w * 0.08f, h * 0.74f)
            close()
        }
        drawPath(center, Color.White.copy(alpha = 0.52f))
        drawRect(
            brush = Brush.verticalGradient(
                0f to Color.White.copy(alpha = 0f),
                1f to Color.White,
            ),
        )
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
    val region = selectedLocation.regionLabel()
    Row(
        modifier = modifier
            .clip(RoundedCornerShape(18.dp))
            .background(AppUiColors.Surface)
            .border(1.dp, AppUiColors.Border, RoundedCornerShape(18.dp))
            .clickable(enabled = locations.isNotEmpty()) { onLocationSelected(selectedLocation.title) }
            .padding(horizontal = 16.dp, vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            text = region,
            style = MaterialTheme.typography.bodyLarge,
            color = AppUiColors.TextPrimary,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
        )
    }
}

@Composable
fun ConnectionTimer(time: String) {
    Text(time, style = MaterialTheme.typography.headlineMedium, color = AppUiColors.TextPrimary, fontWeight = FontWeight.Medium)
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

private enum class MiniIconType { Lock, Shield, Globe, Clock, Home, Settings }

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
                    size = androidx.compose.ui.geometry.Size(sw * 0.52f, sh * 0.40f),
                    cornerRadius = androidx.compose.ui.geometry.CornerRadius(sw * 0.06f),
                    style = Stroke(width = stroke),
                )
                drawArc(
                    color = tint,
                    startAngle = 200f,
                    sweepAngle = 140f,
                    useCenter = false,
                    topLeft = Offset(sw * 0.32f, sh * 0.16f),
                    size = androidx.compose.ui.geometry.Size(sw * 0.36f, sh * 0.45f),
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
                drawArc(tint, 90f, 180f, false, Offset(sw * 0.34f, sh * 0.14f), androidx.compose.ui.geometry.Size(sw * 0.32f, sh * 0.72f), style = Stroke(width = stroke))
                drawArc(tint, -90f, 180f, false, Offset(sw * 0.34f, sh * 0.14f), androidx.compose.ui.geometry.Size(sw * 0.32f, sh * 0.72f), style = Stroke(width = stroke))
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
            MiniIconType.Settings -> {
                drawCircle(tint, radius = minSide * 0.23f, center = Offset(sw / 2f, sh / 2f), style = Stroke(width = stroke))
                repeat(6) { index ->
                    val angle = Math.toRadians((index * 60.0) - 90.0)
                    val start = Offset(
                        x = sw / 2f + kotlin.math.cos(angle).toFloat() * minSide * 0.30f,
                        y = sh / 2f + kotlin.math.sin(angle).toFloat() * minSide * 0.30f,
                    )
                    val end = Offset(
                        x = sw / 2f + kotlin.math.cos(angle).toFloat() * minSide * 0.42f,
                        y = sh / 2f + kotlin.math.sin(angle).toFloat() * minSide * 0.42f,
                    )
                    drawLine(tint, start, end, strokeWidth = stroke, cap = StrokeCap.Round)
                }
                drawCircle(tint, radius = minSide * 0.06f, center = Offset(sw / 2f, sh / 2f))
            }
        }
    }
}

private fun VpnLocationOption.regionLabel(): String {
    val value = title.trim()
    if (value.isBlank()) return "Регион"
    val lower = value.lowercase()
        .replace('_', '-')
        .replace('.', '-')
        .replace(' ', '-')
    val code = when {
        value.startsWith("Регион ", ignoreCase = true) -> value.removePrefix("Регион").trim().uppercase()
        hasRegionToken(lower, "de") || lower.contains("germany") || lower.contains("frankfurt") || lower.contains("герман") || lower.contains("франкфурт") -> "DE"
        hasRegionToken(lower, "nl") || lower.contains("netherlands") || lower.contains("amsterdam") || lower.contains("нидер") || lower.contains("амстердам") -> "NL"
        hasRegionToken(lower, "fr") || lower.contains("france") || lower.contains("paris") || lower.contains("франц") || lower.contains("париж") -> "FR"
        hasRegionToken(lower, "ru") || lower.contains("russia") || lower.contains("moscow") || lower.contains("росси") || lower.contains("москва") -> "RU"
        hasRegionToken(lower, "eu") || lower.contains("europe") || lower.contains("европ") -> "EU"
        hasRegionToken(lower, "pl") || lower.contains("poland") || lower.contains("warsaw") || lower.contains("польш") -> "PL"
        hasRegionToken(lower, "uk") || hasRegionToken(lower, "gb") || lower.contains("london") || lower.contains("лондон") -> "UK"
        hasRegionToken(lower, "us") || hasRegionToken(lower, "usa") || lower.contains("america") || lower.contains("new-york") -> "US"
        hasRegionToken(lower, "se") || lower.contains("sweden") || lower.contains("stockholm") -> "SE"
        hasRegionToken(lower, "fi") || lower.contains("finland") || lower.contains("helsinki") -> "FI"
        hasRegionToken(lower, "es") || lower.contains("spain") || lower.contains("madrid") -> "ES"
        hasRegionToken(lower, "it") || lower.contains("italy") || lower.contains("milan") || lower.contains("rome") -> "IT"
        hasRegionToken(lower, "tr") || lower.contains("turkey") || lower.contains("istanbul") -> "TR"
        hasRegionToken(lower, "sg") || lower.contains("singapore") -> "SG"
        else -> ""
    }
    return if (code.isBlank()) value else "Регион $code"
}

private fun hasRegionToken(value: String, token: String): Boolean {
    return Regex("(^|[^a-z0-9])${Regex.escape(token.lowercase())}([^a-z0-9]|$)").containsMatchIn(value)
}

private object AppUiColors {
    val Background = Color.White
    val Surface = Color(0xFFF9FAFB)
    val SelectedSurface = Color(0xFFF4F6F8)
    val Border = Color(0xFFE6E9EE)
    val TextPrimary = Color(0xFF111319)
    val TextSecondary = Color(0xFF7D828D)
    val Positive = Color(0xFF74C84F)
    val PositiveStrong = Color(0xFF36A852)
    val Warning = Color(0xFFE1A100)
}

$screen = Join-Path $PSScriptRoot '..\app\src\main\java\com\v2ray\ang\ui\premium\vpn\VpnMainScreen.kt'
$screen = [System.IO.Path]::GetFullPath($screen)

@'
package com.v2ray.ang.ui.premium.vpn

import android.content.pm.PackageManager
import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.EaseInOutSine
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.focusable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
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
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.ColorFilter
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.v2ray.ang.R

@Composable
fun VpnMainRoute(
    viewModel: VpnMainViewModel,
    onSettingsClick: () -> Unit = {},
    modifier: Modifier = Modifier,
) {
    val uiState by viewModel.uiState.collectAsState()
    val locations by viewModel.availableRegions.collectAsState()
    val selectedLocation by viewModel.selectedRegion.collectAsState()
    VpnMainScreen(
        uiState = uiState,
        selectedLocation = selectedLocation,
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
    selectedLocation: VpnServerRegionUi?,
    locations: List<VpnServerRegionUi>,
    onLocationSelected: (Long) -> Unit,
    onConnectClick: () -> Unit,
    onDisconnectClick: () -> Unit,
    onSettingsClick: () -> Unit = {},
    modifier: Modifier = Modifier,
) {
    val isTv = rememberIsTelevisionDevice()
    if (isTv) {
        TvVpnMainScreen(
            uiState = uiState,
            selectedLocation = selectedLocation,
            locations = locations,
            onLocationSelected = onLocationSelected,
            onConnectClick = onConnectClick,
            onDisconnectClick = onDisconnectClick,
            onSettingsClick = onSettingsClick,
            modifier = modifier,
        )
    } else {
        MobileVpnMainScreen(
            uiState = uiState,
            selectedLocation = selectedLocation,
            locations = locations,
            onLocationSelected = onLocationSelected,
            onConnectClick = onConnectClick,
            onDisconnectClick = onDisconnectClick,
            onSettingsClick = onSettingsClick,
            modifier = modifier,
        )
    }
}

@Composable
private fun rememberIsTelevisionDevice(): Boolean {
    val context = LocalContext.current
    return remember(context) {
        context.packageManager.hasSystemFeature(PackageManager.FEATURE_LEANBACK)
    }
}

@Composable
private fun MobileVpnMainScreen(
    uiState: VpnMainUiState,
    selectedLocation: VpnServerRegionUi?,
    locations: List<VpnServerRegionUi>,
    onLocationSelected: (Long) -> Unit,
    onConnectClick: () -> Unit,
    onDisconnectClick: () -> Unit,
    onSettingsClick: () -> Unit = {},
    modifier: Modifier = Modifier,
) {
    BoxWithConstraints(
        modifier = modifier
            .fillMaxSize()
            .background(VpnPremiumTokens.Colors.Background),
    ) {
        val compactHeight = maxHeight < 760.dp
        val heroHeight = (maxHeight * if (compactHeight) 0.62f else 0.68f).coerceIn(380.dp, 620.dp)

        LaunchedEffect(uiState.connectionState, uiState.elapsedSeconds, selectedLocation?.serverId) {
            VpnNdjsonDebugLogger.log(
                location = "VpnMainScreen.kt:MobileVpnMainScreen",
                message = "compose_screen_state",
                hypothesisId = "H1_state_drives_ui",
                runId = "real-regions",
                data = mapOf(
                    "connectionState" to uiState.connectionState.name,
                    "elapsedSeconds" to uiState.elapsedSeconds,
                    "serverId" to (selectedLocation?.serverId ?: -1L),
                ),
            )
        }

        Column(
            modifier = Modifier
                .fillMaxSize()
                .statusBarsPadding()
                .navigationBarsPadding()
                .imePadding()
                .verticalScroll(rememberScrollState())
                .padding(horizontal = VpnPremiumTokens.Spacing.ScreenHorizontal, vertical = VpnPremiumTokens.Spacing.TopPadding),
        ) {
            TopBarArea(
                selectedLocation = selectedLocation,
                locations = locations,
                onLocationSelected = onLocationSelected,
                onSettingsClick = onSettingsClick,
            )

            Spacer(modifier = Modifier.height(VpnPremiumTokens.Spacing.BetweenTopAndHero))

            HologramManBlock(
                uiState = uiState,
                modifier = Modifier
                    .fillMaxWidth()
                    .height(heroHeight),
            )

            Spacer(modifier = Modifier.height(VpnPremiumTokens.Spacing.HeroToBottom))

            Column {
                if (locations.isEmpty()) {
                    Text(
                        text = "Нет доступных регионов",
                        color = VpnPremiumTokens.Colors.TextSecondary,
                        style = MaterialTheme.typography.bodyMedium,
                        modifier = Modifier.fillMaxWidth(),
                        textAlign = TextAlign.Center,
                    )
                    Spacer(modifier = Modifier.height(VpnPremiumTokens.Spacing.BottomBlockGap))
                }
                PrimaryConnectButton(
                    state = uiState.connectionState,
                    enabled = uiState.connectButtonEnabled && selectedLocation != null,
                    onClick = {
                        if (uiState.connectionState == VpnConnectionState.Connected) onDisconnectClick() else onConnectClick()
                    },
                )
                Spacer(modifier = Modifier.height(VpnPremiumTokens.Spacing.BottomSafeExtra))
            }
        }
    }
}

@Composable
private fun TvVpnMainScreen(
    uiState: VpnMainUiState,
    selectedLocation: VpnServerRegionUi?,
    locations: List<VpnServerRegionUi>,
    onLocationSelected: (Long) -> Unit,
    onConnectClick: () -> Unit,
    onDisconnectClick: () -> Unit,
    onSettingsClick: () -> Unit = {},
    modifier: Modifier = Modifier,
) {
    BoxWithConstraints(
        modifier = modifier
            .fillMaxSize()
            .background(VpnPremiumTokens.Colors.Background)
            .statusBarsPadding()
            .navigationBarsPadding()
            .padding(horizontal = 40.dp, vertical = 28.dp),
    ) {
        val wide = maxWidth >= 1100.dp
        if (wide) {
            Row(
                modifier = Modifier.fillMaxSize(),
                horizontalArrangement = Arrangement.spacedBy(32.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Box(
                    modifier = Modifier
                        .weight(1.15f)
                        .fillMaxHeight(),
                    contentAlignment = Alignment.Center,
                ) {
                    HologramManBlock(
                        uiState = uiState,
                        modifier = Modifier.fillMaxSize(),
                    )
                }
                TvControlPanel(
                    uiState = uiState,
                    selectedLocation = selectedLocation,
                    locations = locations,
                    onLocationSelected = onLocationSelected,
                    onConnectClick = onConnectClick,
                    onDisconnectClick = onDisconnectClick,
                    onSettingsClick = onSettingsClick,
                    modifier = Modifier
                        .weight(0.85f)
                        .fillMaxHeight(),
                )
            }
        } else {
            Column(
                modifier = Modifier
                    .fillMaxSize()
                    .verticalScroll(rememberScrollState()),
                verticalArrangement = Arrangement.spacedBy(24.dp),
            ) {
                Box(
                    modifier = Modifier
                        .fillMaxWidth()
                        .heightIn(min = 360.dp, max = 520.dp),
                    contentAlignment = Alignment.Center,
                ) {
                    HologramManBlock(
                        uiState = uiState,
                        modifier = Modifier.fillMaxSize(),
                    )
                }
                TvControlPanel(
                    uiState = uiState,
                    selectedLocation = selectedLocation,
                    locations = locations,
                    onLocationSelected = onLocationSelected,
                    onConnectClick = onConnectClick,
                    onDisconnectClick = onDisconnectClick,
                    onSettingsClick = onSettingsClick,
                    modifier = Modifier.fillMaxWidth(),
                )
            }
        }
    }
}

@Composable
private fun TvControlPanel(
    uiState: VpnMainUiState,
    selectedLocation: VpnServerRegionUi?,
    locations: List<VpnServerRegionUi>,
    onLocationSelected: (Long) -> Unit,
    onConnectClick: () -> Unit,
    onDisconnectClick: () -> Unit,
    onSettingsClick: () -> Unit,
    modifier: Modifier = Modifier,
) {
    Column(
        modifier = modifier,
        verticalArrangement = Arrangement.spacedBy(18.dp),
    ) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text = "EMERY VPN",
                    color = VpnPremiumTokens.Colors.TextPrimary,
                    style = MaterialTheme.typography.headlineSmall,
                    fontWeight = FontWeight.Bold,
                )
                Spacer(modifier = Modifier.height(6.dp))
                Text(
                    text = if (uiState.connectionState == VpnConnectionState.Connected) "Подключение активно" else "Управление подключением",
                    color = VpnPremiumTokens.Colors.TextSecondary,
                    style = MaterialTheme.typography.titleMedium,
                )
            }
            TvIconActionButton(onClick = onSettingsClick)
        }

        TvInfoCard {
            Text(
                text = "Регион",
                color = VpnPremiumTokens.Colors.TextSecondary,
                style = MaterialTheme.typography.labelLarge,
            )
            Spacer(modifier = Modifier.height(8.dp))
            Text(
                text = selectedLocation?.title ?: "Нет доступного региона",
                color = VpnPremiumTokens.Colors.TextPrimary,
                style = MaterialTheme.typography.headlineSmall,
                fontWeight = FontWeight.SemiBold,
            )
            if (locations.isNotEmpty()) {
                Spacer(modifier = Modifier.height(16.dp))
                TvRegionPicker(
                    selectedLocation = selectedLocation,
                    locations = locations,
                    onLocationSelected = onLocationSelected,
                )
            }
        }

        TvInfoCard {
            Text(
                text = if (uiState.connectionState == VpnConnectionState.Connected) uiState.formattedDuration else uiState.protectionLabel,
                color = VpnPremiumTokens.Colors.TextPrimary,
                style = MaterialTheme.typography.displaySmall,
                fontWeight = FontWeight.SemiBold,
            )
            Spacer(modifier = Modifier.height(8.dp))
            Text(
                text = when (uiState.connectionState) {
                    VpnConnectionState.Disconnected -> "Нажми, чтобы подключить VPN"
                    VpnConnectionState.Connecting -> "Идёт подключение"
                    VpnConnectionState.Connected -> "Защищённое подключение активно"
                },
                color = VpnPremiumTokens.Colors.TextSecondary,
                style = MaterialTheme.typography.titleMedium,
            )
            uiState.errorMessage?.let { error ->
                Spacer(modifier = Modifier.height(10.dp))
                Text(
                    text = error,
                    color = Color(0xFFE59CA6),
                    style = MaterialTheme.typography.bodyLarge,
                )
            }
        }

        TvPrimaryActionButton(
            state = uiState.connectionState,
            enabled = uiState.connectButtonEnabled && selectedLocation != null,
            onClick = {
                if (uiState.connectionState == VpnConnectionState.Connected) onDisconnectClick() else onConnectClick()
            },
        )
    }
}

@Composable
private fun TvInfoCard(
    modifier: Modifier = Modifier,
    content: @Composable Column.() -> Unit,
) {
    Column(
        modifier = modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(28.dp))
            .background(VpnPremiumTokens.Colors.Surface.copy(alpha = 0.96f))
            .border(1.dp, VpnPremiumTokens.Colors.BorderStrong, RoundedCornerShape(28.dp))
            .padding(24.dp),
        content = content,
    )
}

@Composable
private fun TvRegionPicker(
    selectedLocation: VpnServerRegionUi?,
    locations: List<VpnServerRegionUi>,
    onLocationSelected: (Long) -> Unit,
) {
    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
        locations.forEach { location ->
            TvRegionRow(
                title = location.title,
                selected = selectedLocation?.serverId == location.serverId,
                onClick = { onLocationSelected(location.serverId) },
            )
        }
    }
}

@Composable
private fun TvRegionRow(
    title: String,
    selected: Boolean,
    onClick: () -> Unit,
) {
    var focused by remember { mutableStateOf(false) }
    val borderColor by animateColorAsState(
        targetValue = when {
            focused -> Color(0xFFDBEAFE)
            selected -> Color(0xFF9FC0E1)
            else -> VpnPremiumTokens.Colors.BorderSubtle
        },
        label = "tvRegionBorder",
    )
    val backgroundColor by animateColorAsState(
        targetValue = when {
            focused -> Color(0xFF162646)
            selected -> Color(0xFF13233F)
            else -> VpnPremiumTokens.Colors.Surface.copy(alpha = 0.72f)
        },
        label = "tvRegionBackground",
    )

    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(22.dp))
            .background(backgroundColor)
            .border(2.dp, borderColor, RoundedCornerShape(22.dp))
            .onFocusChanged { focused = it.isFocused }
            .focusable()
            .clickable(onClick = onClick)
            .padding(horizontal = 18.dp, vertical = 16.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        LocationFlagEmoji(location = VpnServerRegionUi(0L, title, "ok"))
        Spacer(modifier = Modifier.width(12.dp))
        Text(
            text = title,
            color = VpnPremiumTokens.Colors.TextPrimary,
            style = MaterialTheme.typography.titleMedium,
            fontWeight = if (selected) FontWeight.SemiBold else FontWeight.Medium,
        )
    }
}

@Composable
private fun TvPrimaryActionButton(
    state: VpnConnectionState,
    enabled: Boolean,
    onClick: () -> Unit,
) {
    var focused by remember { mutableStateOf(false) }
    val color by animateColorAsState(
        targetValue = if (state == VpnConnectionState.Connected) VpnPremiumTokens.Colors.PrimaryButtonConnected else VpnPremiumTokens.Colors.PrimaryButtonIdle,
        label = "tvConnectButtonColor",
    )
    val borderColor by animateColorAsState(
        targetValue = if (focused) Color.White.copy(alpha = 0.92f) else color.copy(alpha = 0.9f),
        label = "tvConnectButtonBorder",
    )
    val label = when (state) {
        VpnConnectionState.Disconnected -> "Подключиться"
        VpnConnectionState.Connecting -> "Connecting..."
        VpnConnectionState.Connected -> "Отключить"
    }

    Button(
        onClick = onClick,
        enabled = enabled,
        modifier = Modifier
            .fillMaxWidth()
            .height(86.dp)
            .onFocusChanged { focused = it.isFocused }
            .focusable()
            .border(3.dp, borderColor, RoundedCornerShape(26.dp)),
        shape = RoundedCornerShape(26.dp),
        colors = ButtonDefaults.buttonColors(containerColor = color),
    ) {
        Text(
            text = label,
            color = Color.White,
            style = MaterialTheme.typography.headlineSmall,
            fontWeight = FontWeight.Bold,
        )
    }
}

@Composable
private fun TvIconActionButton(onClick: () -> Unit) {
    var focused by remember { mutableStateOf(false) }
    Box(
        modifier = Modifier
            .size(64.dp)
            .clip(CircleShape)
            .background(if (focused) Color.White else VpnPremiumTokens.Colors.SettingsCircleFill)
            .border(2.dp, if (focused) Color(0xFF9FC0E1) else Color.Transparent, CircleShape)
            .onFocusChanged { focused = it.isFocused }
            .focusable()
            .clickable(onClick = onClick),
        contentAlignment = Alignment.Center,
    ) {
        Icon(
            imageVector = Icons.Outlined.Settings,
            contentDescription = "Settings",
            tint = if (focused) VpnPremiumTokens.Colors.SettingsIcon else VpnPremiumTokens.Colors.SettingsIcon,
            modifier = Modifier.size(28.dp),
        )
    }
}

@Composable
fun HumanSilhouetteBlock(
    uiState: VpnMainUiState,
    modifier: Modifier = Modifier,
) {
    HologramManBlock(uiState = uiState, modifier = modifier)
}

@Composable
fun HologramManBlock(
    uiState: VpnMainUiState,
    modifier: Modifier = Modifier,
) {
    val infiniteTransition = rememberInfiniteTransition(label = "hologram")
    val pulse by infiniteTransition.animateFloat(
        initialValue = 0f,
        targetValue = 1f,
        animationSpec = infiniteRepeatable(
            animation = tween(
                durationMillis = if (uiState.connectionState == VpnConnectionState.Connecting) 1800 else 3200,
                easing = EaseInOutSine,
            ),
            repeatMode = RepeatMode.Reverse,
        ),
        label = "pulse",
    )

    val state = uiState.connectionState

    val baseLineColor by animateColorAsState(
        targetValue = when (state) {
            VpnConnectionState.Disconnected -> VpnPremiumTokens.Colors.SilhouetteDisconnected
            VpnConnectionState.Connecting -> VpnPremiumTokens.Colors.SilhouetteConnecting
            VpnConnectionState.Connected -> VpnPremiumTokens.Colors.SilhouetteConnected
        },
        animationSpec = tween(700),
        label = "lineColor",
    )

    Box(
        modifier = modifier,
        contentAlignment = Alignment.Center,
    ) {
        Box(
            modifier = Modifier
                .fillMaxSize()
                .padding(horizontal = 10.dp, vertical = 6.dp),
            contentAlignment = Alignment.Center,
        ) {
            Image(
                painter = painterResource(id = R.drawable.hologram_man),
                contentDescription = null,
                modifier = Modifier
                    .fillMaxSize()
                    .graphicsLayer(
                        alpha = when (state) {
                            VpnConnectionState.Disconnected -> 0.12f
                            VpnConnectionState.Connecting -> 0.16f + pulse * 0.05f
                            VpnConnectionState.Connected -> 0.18f
                        },
                        scaleX = 1.02f,
                        scaleY = 1.02f,
                    ),
                contentScale = ContentScale.Fit,
                colorFilter = ColorFilter.tint(baseLineColor),
            )
            Image(
                painter = painterResource(id = R.drawable.hologram_man),
                contentDescription = null,
                modifier = Modifier
                    .fillMaxSize()
                    .graphicsLayer(
                        alpha = when (state) {
                            VpnConnectionState.Disconnected -> 0.62f
                            VpnConnectionState.Connecting -> 0.74f + pulse * 0.06f
                            VpnConnectionState.Connected -> 0.86f
                        },
                    ),
                contentScale = ContentScale.Fit,
                colorFilter = ColorFilter.tint(baseLineColor.copy(alpha = 0.92f)),
            )
        }

        ConnectionStatusOverlay(
            uiState = uiState,
            modifier = Modifier.fillMaxSize(),
        )
    }
}

@Composable
fun TopBarArea(
    selectedLocation: VpnServerRegionUi?,
    locations: List<VpnServerRegionUi>,
    onLocationSelected: (Long) -> Unit,
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
            modifier = Modifier
                .weight(1f, fill = false)
                .widthIn(max = 260.dp),
        )
        Spacer(modifier = Modifier.weight(1f, fill = true))
        SettingsCircleButton(onClick = onSettingsClick)
    }
}

@Composable
fun LocationSelector(
    selectedLocation: VpnServerRegionUi?,
    locations: List<VpnServerRegionUi>,
    onLocationSelected: (Long) -> Unit,
    modifier: Modifier = Modifier,
) {
    var expanded by remember { mutableStateOf(false) }
    val hasLocations = locations.isNotEmpty()
    val currentLocation = selectedLocation ?: locations.firstOrNull()
    Box(modifier = modifier) {
        Row(
            modifier = Modifier
                .height(VpnPremiumTokens.Sizes.SelectorHeight)
                .clip(RoundedCornerShape(VpnPremiumTokens.Sizes.SelectorCorner))
                .background(VpnPremiumTokens.Colors.Surface)
                .border(1.dp, VpnPremiumTokens.Colors.BorderStrong, RoundedCornerShape(VpnPremiumTokens.Sizes.SelectorCorner))
                .clickable(enabled = hasLocations) { expanded = true }
                .padding(horizontal = 14.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            LocationFlagEmoji(location = currentLocation)
            Spacer(modifier = Modifier.width(10.dp))
            Text(
                text = currentLocation?.title ?: "Нет региона",
                style = MaterialTheme.typography.titleMedium,
                color = VpnPremiumTokens.Colors.TextPrimary,
                modifier = Modifier.weight(1f),
                fontWeight = FontWeight.SemiBold,
                maxLines = 1,
            )
            Icon(
                imageVector = Icons.Rounded.KeyboardArrowDown,
                contentDescription = null,
                tint = VpnPremiumTokens.Colors.TextSecondary,
            )
        }
        DropdownMenu(
            expanded = expanded && hasLocations,
            onDismissRequest = { expanded = false },
            modifier = Modifier.background(VpnPremiumTokens.Colors.Background),
        ) {
            locations.forEach { location ->
                DropdownMenuItem(
                    text = {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            LocationFlagEmoji(location = location)
                            Spacer(modifier = Modifier.width(10.dp))
                            Text(location.title, color = VpnPremiumTokens.Colors.TextPrimary)
                        }
                    },
                    onClick = {
                        expanded = false
                        onLocationSelected(location.serverId)
                    },
                )
            }
        }
    }
}

@Composable
fun SettingsCircleButton(
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
) {
    Box(
        modifier = modifier
            .size(VpnPremiumTokens.Sizes.SettingsButton)
            .clip(CircleShape)
            .background(VpnPremiumTokens.Colors.SettingsCircleFill)
            .border(1.dp, Color.Black.copy(alpha = 0.06f), CircleShape)
            .clickable(onClick = onClick),
        contentAlignment = Alignment.Center,
    ) {
        Icon(
            imageVector = Icons.Outlined.Settings,
            contentDescription = "Settings",
            tint = VpnPremiumTokens.Colors.SettingsIcon,
            modifier = Modifier.size(18.dp),
        )
    }
}

@Composable
fun ConnectionTimer(time: String) {
    Text(
        text = time,
        style = MaterialTheme.typography.headlineLarge.copy(
            fontWeight = FontWeight.SemiBold,
            fontFamily = FontFamily.Monospace,
            letterSpacing = VpnPremiumTokens.Typography.TimerLetterSpacing,
        ),
        color = Color.White.copy(alpha = 0.96f),
        textAlign = TextAlign.Center,
    )
}

@Composable
fun ConnectionStatusOverlay(
    uiState: VpnMainUiState,
    modifier: Modifier = Modifier,
) {
    val state = uiState.connectionState
    val chestYOffset = 0.06f

    LaunchedEffect(state, uiState.timerVisible) {
        VpnNdjsonDebugLogger.log(
            location = "VpnMainScreen.kt:ConnectionStatusOverlay",
            message = "compose_overlay",
            hypothesisId = "H2_overlay_branching",
            runId = "real-regions",
            data = mapOf(
                "connectionState" to state.name,
                "timerVisible" to uiState.timerVisible,
            ),
        )
    }

    BoxWithConstraints(modifier = modifier) {
        Column(
            modifier = Modifier
                .align(Alignment.Center)
                .padding(top = (maxHeight * chestYOffset)),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            if (state == VpnConnectionState.Connected) {
                ConnectionTimer(time = uiState.formattedDuration)
                Text(
                    text = "Protected",
                    color = VpnPremiumTokens.Colors.TextSecondary.copy(alpha = 0.88f),
                    style = MaterialTheme.typography.bodyMedium,
                    textAlign = TextAlign.Center,
                    modifier = Modifier.padding(top = 6.dp),
                )
            } else {
                Text(
                    text = "Not protected",
                    color = VpnPremiumTokens.Colors.TextSecondary.copy(alpha = 0.92f),
                    style = MaterialTheme.typography.titleMedium.copy(fontWeight = FontWeight.Medium),
                    textAlign = TextAlign.Center,
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
    modifier: Modifier = Modifier,
) {
    val color by animateColorAsState(
        targetValue = if (state == VpnConnectionState.Connected) VpnPremiumTokens.Colors.PrimaryButtonConnected else VpnPremiumTokens.Colors.PrimaryButtonIdle,
        label = "connectButtonColor",
    )

    val label = when (state) {
        VpnConnectionState.Disconnected -> "Подключиться"
        VpnConnectionState.Connecting -> "Connecting..."
        VpnConnectionState.Connected -> "Отключить"
    }

    Button(
        onClick = onClick,
        enabled = enabled,
        modifier = modifier
            .fillMaxWidth()
            .height(VpnPremiumTokens.Sizes.PrimaryButtonHeight),
        shape = RoundedCornerShape(VpnPremiumTokens.Sizes.PrimaryButtonCorner),
        colors = ButtonDefaults.buttonColors(containerColor = color),
    ) {
        Text(text = label, color = Color.White, fontWeight = FontWeight.SemiBold)
    }
}

@Composable
private fun LocationFlagEmoji(location: VpnServerRegionUi?, modifier: Modifier = Modifier) {
    val raw = remember(location?.title) { location?.title?.lowercase().orEmpty() }
    val flag = when {
        raw.contains("moscow") || raw.contains("моск") || raw.contains("russia") || raw.contains("рос") -> "\uD83C\uDDF7\uD83C\uDDFA"
        raw.contains("switzerland") || raw.contains("швейцар") -> "\uD83C\uDDE8\uD83C\uDDED"
        raw.contains("netherlands") || raw.contains("нидерл") || raw.contains("голланд") -> "\uD83C\uDDF3\uD83C\uDDF1"
        raw.contains("germany") || raw.contains("герман") -> "\uD83C\uDDE9\uD83C\uDDEA"
        raw.contains("france") || raw.contains("франц") -> "\uD83C\uDDEB\uD83C\uDDF7"
        raw.contains("poland") || raw.contains("поль") -> "\uD83C\uDDF5\uD83C\uDDF1"
        else -> "\uD83C\uDFF3\uFE0F"
    }
    Box(
        modifier = modifier
            .size(26.dp)
            .clip(CircleShape)
            .background(Color.White.copy(alpha = 0.06f)),
        contentAlignment = Alignment.Center,
    ) {
        Text(text = flag, fontSize = 16.sp)
    }
}
'@ | Set-Content $screen -Encoding UTF8

Write-Host 'patched VpnMainScreen.kt with TV adaptation only'

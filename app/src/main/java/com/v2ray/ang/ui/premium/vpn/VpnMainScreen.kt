package com.v2ray.ang.ui.premium.vpn

import androidx.compose.animation.AnimatedVisibility
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
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.shape.CircleShape
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
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
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
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.ColorFilter
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.layout.ContentScale
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
    VpnMainScreen(
        uiState = uiState,
        locations = VpnDemoData.locations,
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
    BoxWithConstraints(
        modifier = modifier
            .fillMaxSize()
            .background(VpnPremiumTokens.Colors.Background),
    ) {
        val compactHeight = maxHeight < 760.dp
        val heroHeight = (maxHeight * if (compactHeight) 0.62f else 0.68f).coerceIn(380.dp, 620.dp)

        LaunchedEffect(uiState.connectionState, uiState.elapsedSeconds, uiState.selectedLocation.id) {
            VpnNdjsonDebugLogger.log(
                location = "VpnMainScreen.kt:VpnMainScreen",
                message = "compose_screen_state",
                hypothesisId = "H1_state_drives_ui",
                runId = "pre-fix",
                data = mapOf(
                    "connectionState" to uiState.connectionState.name,
                    "elapsedSeconds" to uiState.elapsedSeconds,
                    "locationId" to uiState.selectedLocation.id,
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
                selectedLocation = uiState.selectedLocation,
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
                PrimaryConnectButton(
                    state = uiState.connectionState,
                    enabled = uiState.connectButtonEnabled,
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
            modifier = Modifier
                .weight(1f, fill = false)
                .widthIn(max = VpnPremiumTokens.Sizes.TopSelectorMaxWidth),
        )
        Spacer(modifier = Modifier.weight(1f, fill = true))
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
    var expanded by remember { mutableStateOf(false) }
    Box(modifier = modifier) {
        Row(
            modifier = Modifier
                .height(VpnPremiumTokens.Sizes.SelectorHeight)
                .clip(RoundedCornerShape(VpnPremiumTokens.Sizes.SelectorCorner))
                .background(VpnPremiumTokens.Colors.Surface)
                .border(1.dp, VpnPremiumTokens.Colors.BorderStrong, RoundedCornerShape(VpnPremiumTokens.Sizes.SelectorCorner))
                .clickable { expanded = true }
                .padding(horizontal = 14.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            LocationFlagEmoji(locationId = selectedLocation.id)
            Spacer(modifier = Modifier.width(10.dp))
            Text(
                text = selectedLocation.title,
                style = MaterialTheme.typography.titleMedium,
                color = VpnPremiumTokens.Colors.TextPrimary,
                modifier = Modifier.weight(1f),
                fontWeight = FontWeight.SemiBold,
            )
            Icon(
                imageVector = Icons.Rounded.KeyboardArrowDown,
                contentDescription = null,
                tint = VpnPremiumTokens.Colors.TextSecondary,
            )
        }
        DropdownMenu(
            expanded = expanded,
            onDismissRequest = { expanded = false },
            modifier = Modifier.background(VpnPremiumTokens.Colors.Background),
        ) {
            locations.forEach { location ->
                DropdownMenuItem(
                    text = {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            LocationFlagEmoji(locationId = location.id)
                            Spacer(modifier = Modifier.width(10.dp))
                            Text(location.title, color = VpnPremiumTokens.Colors.TextPrimary)
                        }
                    },
                    onClick = { expanded = false; onLocationSelected(location.title) },
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
            runId = "pre-fix",
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
fun ActivationKeyField(
    value: String,
    onValueChange: (String) -> Unit,
    enabled: Boolean,
    modifier: Modifier = Modifier,
) {
    OutlinedTextField(
        value = value,
        onValueChange = onValueChange,
        modifier = modifier.fillMaxWidth(),
        enabled = enabled,
        singleLine = true,
        shape = RoundedCornerShape(VpnPremiumTokens.Sizes.FieldCorner),
        label = { Text("Activation key", color = VpnPremiumTokens.Colors.TextSecondary) },
        colors = OutlinedTextFieldDefaults.colors(
            unfocusedContainerColor = Color.Transparent,
            focusedContainerColor = Color.Transparent,
            focusedTextColor = Color.White,
            unfocusedTextColor = Color.White,
            disabledTextColor = VpnPremiumTokens.Colors.TextSecondary.copy(alpha = 0.9f),
            unfocusedBorderColor = VpnPremiumTokens.Colors.BorderStrong,
            focusedBorderColor = VpnPremiumTokens.Colors.BorderStrong.copy(alpha = 0.9f),
            disabledBorderColor = VpnPremiumTokens.Colors.BorderSubtle,
        ),
    )
}

@Composable
private fun LocationFlagEmoji(locationId: String, modifier: Modifier = Modifier) {
    val flag = remember(locationId) {
        when (locationId.lowercase()) {
            "switzerland" -> "\uD83C\uDDE8\uD83C\uDDED"
            "netherlands" -> "\uD83C\uDDF3\uD83C\uDDF1"
            "germany" -> "\uD83C\uDDE9\uD83C\uDDEA"
            "france" -> "\uD83C\uDDEB\uD83C\uDDF7"
            "poland" -> "\uD83C\uDDF5\uD83C\uDDF1"
            else -> "\uD83C\uDFF3\uFE0F"
        }
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

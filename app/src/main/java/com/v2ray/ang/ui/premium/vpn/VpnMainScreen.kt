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
import com.v2ray.ang.network.EmeryBackendClient

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

        if (availableNames.isEmpty()) {
            return@LaunchedEffect
        }

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
    BoxWithConstraints(
        modifier = modifier
            .fillMaxSize()
            .background(VpnPremiumTokens.Colors.Background),
    ) {
        val compactHeight = maxHeight < 760.dp
        val heroHeight = (maxHeight * if (compactHeight) 0.62f else 0.68f).coerceIn(380.dp, 620.dp)

        // #region agent log (runtime evidence)
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
        // #endregion

        Column(
            modifier = Modifier
                .fillMaxSize()
                .statusBarsPadding()
                .navigationBarsPadding()
                .imePadding()
                .verticalScroll(rememberScrollState())
                .padding(horizontal = Vpn@remiumTokens.Spacing.ScreenHorizontal, vertical = Vpn@remiumTokens.Spacing.TopPadding),
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
                    enabled = uiState.connectionState != VpnConnectionState.Connecting,
                    onClick = {
                        if (uiState.connectionState == VpnConnectionState.Connected) onDisconnectClick() else onConnectClick()
                    },
                )
                Spacer(modifier = Modifier.height(Vpn@remiumTokens.Spacing.BottomSafeExtra))
            }
        }
    }
}

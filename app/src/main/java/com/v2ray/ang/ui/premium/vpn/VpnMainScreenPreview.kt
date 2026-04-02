package com.v2ray.ang.ui.premium.vpn

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp

private val VpnPreviewColorScheme = darkColorScheme(
    primary = Color(0xFF9FC0E1),
    onPrimary = Color(0xFFF4F7FB),
    background = Color(0xFF0B132B),
    onBackground = Color(0xFFF4F7FB),
    surface = Color(0xFF0D1628),
    onSurface = Color(0xFFF4F7FB),
    surfaceVariant = Color(0xFF142238),
    onSurfaceVariant = Color(0xFFA4B2C6),
    outline = Color(0x33EEF4FF),
)

@Composable
private fun VpnPreviewTheme(
    content: @Composable () -> Unit,
) {
    MaterialTheme(
        colorScheme = VpnPreviewColorScheme,
        content = content,
    )
}

@Preview(
    name = "VPN Connected",
    showBackground = true,
    backgroundColor = 0xFF0B132B,
    widthDp = 390,
    heightDp = 844,
)
@Composable
private fun VpnMainScreenConnectedPreview() {
    VpnPreviewTheme {
        VpnMainScreen(
            uiState = VpnDemoData.connectedState(),
            locations = VpnDemoData.locations,
            onLocationSelected = {},
            onConnectClick = {},
            onDisconnectClick = {},
            onSettingsClick = {},
        )
    }
}

@Preview(
    name = "VPN Disconnected",
    showBackground = true,
    backgroundColor = 0xFF0B132B,
    widthDp = 390,
    heightDp = 844,
)
@Composable
private fun VpnMainScreenDisconnectedPreview() {
    VpnPreviewTheme {
        VpnMainScreen(
            uiState = VpnDemoData.disconnectedState(),
            locations = VpnDemoData.locations,
            onLocationSelected = {},
            onConnectClick = {},
            onDisconnectClick = {},
            onSettingsClick = {},
        )
    }
}

@Preview(
    name = "Hero Connected",
    showBackground = true,
    backgroundColor = 0xFF0B132B,
    widthDp = 390,
    heightDp = 700,
)
@Composable
private fun HeroConnectedPreview() {
    VpnPreviewTheme {
        Box(
            modifier = Modifier
                .fillMaxSize()
                .background(Color(0xFF0B132B))
                .padding(24.dp),
        ) {
            HumanSilhouetteBlock(
                uiState = VpnDemoData.connectedState(),
                modifier = Modifier.fillMaxSize(),
            )
        }
    }
}

@Preview(
    name = "Hero Disconnected",
    showBackground = true,
    backgroundColor = 0xFF0B132B,
    widthDp = 390,
    heightDp = 700,
)
@Composable
private fun HeroDisconnectedPreview() {
    VpnPreviewTheme {
        Box(
            modifier = Modifier
                .fillMaxSize()
                .background(Color(0xFF0B132B))
                .padding(24.dp),
        ) {
            HumanSilhouetteBlock(
                uiState = VpnDemoData.disconnectedState(),
                modifier = Modifier.fillMaxSize(),
            )
        }
    }
}

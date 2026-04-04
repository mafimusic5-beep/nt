package com.v2ray.ang.ui.premium.vpn

import java.util.Locale

enum class VpnConnectionState {
    Disconnected,
    Connecting,
    Connected,
}

data class VpnMainUiState(
    val connectionState: VpnConnectionState = VpnConnectionState.Disconnected,
    val elapsedSeconds: Long = 0L,
    val errorMessage: String? = null,
) {
    val formattedDuration: String
        get() {
            val hours = elapsedSeconds / 3600
            val minutes = (elapsedSeconds % 3600) / 60
            val seconds = elapsedSeconds % 60
            return String.format(Locale.US, "%02d:%02d:%02d", hours, minutes, seconds)
        }

    val protectionLabel: String
        get() = when (connectionState) {
            VpnConnectionState.Connected -> "Protected"
            VpnConnectionState.Connecting -> "Securing tunnel"
            VpnConnectionState.Disconnected -> "Not protected"
        }

    val connectButtonLabel: String
        get() = when (connectionState) {
            VpnConnectionState.Disconnected -> "Connect"
            VpnConnectionState.Connecting -> "Connecting..."
            VpnConnectionState.Connected -> "Disconnect"
        }

    val connectButtonEnabled: Boolean
        get() = when (connectionState) {
            VpnConnectionState.Disconnected -> true
            VpnConnectionState.Connecting -> false
            VpnConnectionState.Connected -> true
        }

    val timerVisible: Boolean
        get() = connectionState == VpnConnectionState.Connected
}

object VpnDemoData {
    val previewRegions: List<VpnServerRegionUi> = listOf(
        VpnServerRegionUi(serverId = 1L, title = "Switzerland", healthStatus = "ok"),
        VpnServerRegionUi(serverId = 2L, title = "Netherlands", healthStatus = "ok"),
        VpnServerRegionUi(serverId = 3L, title = "Germany", healthStatus = "ok"),
    )

    fun disconnectedState(): VpnMainUiState = VpnMainUiState(
        connectionState = VpnConnectionState.Disconnected,
        elapsedSeconds = 0L,
    )

    fun connectedState(): VpnMainUiState = VpnMainUiState(
        connectionState = VpnConnectionState.Connected,
        elapsedSeconds = 763L,
    )
}

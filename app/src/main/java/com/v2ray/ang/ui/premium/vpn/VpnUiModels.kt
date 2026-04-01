package com.v2ray.ang.ui.premium.vpn

import java.util.Locale

enum class VpnConnectionState {
    Disconnected,
    Connecting,
    Connected,
}

data class VpnLocationOption(
    val id: String,
    val title: String,
)

data class VpnMainUiState(
    val activationKey: String = "",
    val selectedLocation: VpnLocationOption = VpnDemoData.locations.first(),
    val connectionState: VpnConnectionState = VpnConnectionState.Disconnected,
    val elapsedSeconds: Long = 0L,
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
            VpnConnectionState.Disconnected -> activationKey.isNotBlank()
            VpnConnectionState.Connecting -> false
            VpnConnectionState.Connected -> true
        }

    val timerVisible: Boolean
        get() = connectionState == VpnConnectionState.Connected
}

object VpnDemoData {
    val locations: List<VpnLocationOption> = listOf(
        VpnLocationOption(id = "switzerland", title = "Switzerland"),
        VpnLocationOption(id = "netherlands", title = "Netherlands"),
        VpnLocationOption(id = "germany", title = "Germany"),
        VpnLocationOption(id = "france", title = "France"),
        VpnLocationOption(id = "poland", title = "Poland"),
    )

    fun disconnectedState(): VpnMainUiState = VpnMainUiState(
        activationKey = "EVPN-24H9-X2Q7",
        selectedLocation = locations.first(),
        connectionState = VpnConnectionState.Disconnected,
        elapsedSeconds = 0L,
    )

    fun connectedState(): VpnMainUiState = VpnMainUiState(
        activationKey = "EVPN-24H9-X2Q7",
        selectedLocation = locations.first(),
        connectionState = VpnConnectionState.Connected,
        elapsedSeconds = 763L,
    )
}

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
    val locations: List<VpnLocationOption> = VpnDemoData.loadingLocations,
    val selectedLocation: VpnLocationOption = VpnDemoData.loadingLocations.first(),
    val connectionState: VpnConnectionState = VpnConnectionState.Disconnected,
    val elapsedSeconds: Long = 0L,
    val locationsLoading: Boolean = false,
    val locationsError: String = "",
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
            VpnConnectionState.Disconnected -> activationKey.isNotBlank() && selectedLocation.id.toLongOrNull() != null
            VpnConnectionState.Connecting -> false
            VpnConnectionState.Connected -> true
        }

    val timerVisible: Boolean
        get() = connectionState == VpnConnectionState.Connected
}

object VpnDemoData {
    val loadingLocations: List<VpnLocationOption> = listOf(
        VpnLocationOption(id = "loading", title = "Загрузка серверов"),
    )

    val unavailableLocations: List<VpnLocationOption> = listOf(
        VpnLocationOption(id = "unavailable", title = "Серверы недоступны"),
    )

    val locations: List<VpnLocationOption> = listOf(
        VpnLocationOption(id = "1", title = "Singapore"),
        VpnLocationOption(id = "2", title = "Amsterdam"),
        VpnLocationOption(id = "3", title = "Frankfurt"),
    )

    fun disconnectedState(): VpnMainUiState = VpnMainUiState(
        activationKey = "EVPN-24H9-X2Q7",
        locations = locations,
        selectedLocation = locations.first(),
        connectionState = VpnConnectionState.Disconnected,
        elapsedSeconds = 0L,
    )

    fun connectedState(): VpnMainUiState = VpnMainUiState(
        activationKey = "EVPN-24H9-X2Q7",
        locations = locations,
        selectedLocation = locations.first(),
        connectionState = VpnConnectionState.Connected,
        elapsedSeconds = 763L,
    )
}

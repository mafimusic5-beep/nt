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
    val importText: String = "",
) {
    fun cityLabel(): String {
        val value = title.trim()
        if (value.startsWith("In ", ignoreCase = true)) return value

        val lower = value.lowercase().replace('_', '-').replace('.', '-').replace(' ', '-')
        return when {
            value.isBlank() -> "Париж"
            lower.contains("paris") || lower.contains("париж") || lower.contains("france") || hasLocationToken(lower, "fr") -> "Париж"
            lower.contains("frankfurt") || lower.contains("germany") || hasLocationToken(lower, "de") -> "Франкфурт"
            lower.contains("amsterdam") || lower.contains("netherlands") || hasLocationToken(lower, "nl") -> "Амстердам"
            lower.contains("moscow") || lower.contains("москва") || hasLocationToken(lower, "ru") -> "Москва"
            lower.contains("warsaw") || hasLocationToken(lower, "pl") -> "Варшава"
            lower.contains("london") || hasLocationToken(lower, "uk") || hasLocationToken(lower, "gb") -> "Лондон"
            lower.contains("new-york") || lower.contains("newyork") || hasLocationToken(lower, "us") || hasLocationToken(lower, "usa") -> "Нью-Йорк"
            lower.contains("stockholm") || hasLocationToken(lower, "se") -> "Стокгольм"
            lower.contains("helsinki") || hasLocationToken(lower, "fi") -> "Хельсинки"
            lower.contains("madrid") || hasLocationToken(lower, "es") -> "Мадрид"
            lower.contains("milan") || hasLocationToken(lower, "it") -> "Милан"
            lower.contains("istanbul") || hasLocationToken(lower, "tr") -> "Стамбул"
            lower.contains("singapore") || hasLocationToken(lower, "sg") -> "Сингапур"
            lower.contains("skryon") -> "Париж"
            lower.contains("europe") || hasLocationToken(lower, "eu") -> "Европа"
            else -> value
        }
    }

    fun countryCodeLabel(): String {
        val value = title.trim().lowercase().replace('_', '-').replace('.', '-').replace(' ', '-')
        return when {
            value.contains("germany") || value.contains("deutschland") -> "DE"
            value.contains("france") -> "FR"
            value.contains("netherlands") || value.contains("nederland") -> "NL"
            value.contains("russia") -> "RU"
            value.contains("poland") -> "PL"
            value.contains("united-kingdom") -> "UK"
            value.contains("united-states") || value.contains("america") -> "US"
            value.contains("sweden") -> "SE"
            value.contains("finland") -> "FI"
            value.contains("spain") -> "ES"
            value.contains("italy") -> "IT"
            value.contains("turkey") -> "TR"
            value.contains("singapore") -> "SG"
            value.contains("hong-kong") -> "HK"
            value.contains("japan") -> "JP"
            value.contains("kazakhstan") -> "KZ"
            value.contains("ukraine") -> "UA"
            cityLabel() == "Париж" -> "FR"
            cityLabel() == "Франкфурт" -> "DE"
            cityLabel() == "Амстердам" -> "NL"
            cityLabel() == "Москва" -> "RU"
            cityLabel() == "Варшава" -> "PL"
            cityLabel() == "Лондон" -> "UK"
            cityLabel() == "Нью-Йорк" -> "US"
            cityLabel() == "Стокгольм" -> "SE"
            cityLabel() == "Хельсинки" -> "FI"
            cityLabel() == "Мадрид" -> "ES"
            cityLabel() == "Милан" -> "IT"
            cityLabel() == "Стамбул" -> "TR"
            cityLabel() == "Сингапур" -> "SG"
            cityLabel() == "Европа" -> "EU"
            hasLocationToken(value, "fr") -> "FR"
            hasLocationToken(value, "de") -> "DE"
            hasLocationToken(value, "nl") -> "NL"
            hasLocationToken(value, "ru") -> "RU"
            hasLocationToken(value, "pl") -> "PL"
            hasLocationToken(value, "uk") || hasLocationToken(value, "gb") -> "UK"
            hasLocationToken(value, "us") || hasLocationToken(value, "usa") -> "US"
            hasLocationToken(value, "se") -> "SE"
            hasLocationToken(value, "fi") -> "FI"
            hasLocationToken(value, "es") -> "ES"
            hasLocationToken(value, "it") -> "IT"
            hasLocationToken(value, "tr") -> "TR"
            hasLocationToken(value, "sg") -> "SG"
            hasLocationToken(value, "eu") -> "EU"
            else -> "VPN"
        }
    }

    fun regionLabel(): String {
        val city = cityLabel()
        if (city.startsWith("In ", ignoreCase = true)) return city
        return when (city) {
            "Париж" -> "Регион FR"
            "Франкфурт" -> "Регион DE"
            "Амстердам" -> "Регион NL"
            "Москва" -> "Регион RU"
            "Варшава" -> "Регион PL"
            "Лондон" -> "Регион UK"
            "Нью-Йорк" -> "Регион US"
            "Европа" -> "Регион EU"
            else -> city
        }
    }
}

private fun hasLocationToken(value: String, token: String): Boolean {
    return Regex("(^|[^a-z0-9])${Regex.escape(token.lowercase())}([^a-z0-9]|$)").containsMatchIn(value)
}

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
            VpnConnectionState.Disconnected -> activationKey.isNotBlank() && (
                selectedLocation.id.toLongOrNull() != null || selectedLocation.importText.isNotBlank()
            )
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

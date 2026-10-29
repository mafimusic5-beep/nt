package com.v2ray.ang.handler

/**
 * Persistent USER intent, separate from Android's connection status.
 *
 * Turning on auto-connect is permission to retry an explicitly requested
 * connection, NOT permission to undo an explicit manual disconnect.
 */
internal fun shouldAutoReconnectVpn(
    enabled: Boolean,
    userRequestedConnection: Boolean,
    networkAvailable: Boolean,
    vpnAlreadyRunning: Boolean,
): Boolean = enabled && userRequestedConnection && networkAvailable && !vpnAlreadyRunning

internal object SkryonVpnConnectionIntent {
    private const val KEY_USER_REQUESTED_VPN = "SKRYON_USER_REQUESTED_VPN"

    fun isArmed(): Boolean = MmkvManager.decodeSettingsBool(KEY_USER_REQUESTED_VPN, false)

    fun markUserConnected() {
        MmkvManager.encodeSettings(KEY_USER_REQUESTED_VPN, true)
    }

    fun markUserDisconnected() {
        MmkvManager.encodeSettings(KEY_USER_REQUESTED_VPN, false)
    }

    fun shouldRetry(networkAvailable: Boolean, vpnAlreadyRunning: Boolean): Boolean =
        shouldAutoReconnectVpn(
            enabled = MmkvManager.decodeStartOnBoot(),
            userRequestedConnection = isArmed(),
            networkAvailable = networkAvailable,
            vpnAlreadyRunning = vpnAlreadyRunning,
        )
}

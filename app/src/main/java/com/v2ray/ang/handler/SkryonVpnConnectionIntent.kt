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

/**
 * Explicit start from the Connect button is distinct from an OS-triggered
 * restart. OS restart requires auto-connect to remain enabled.
 */
internal fun isPremiumVpnServiceStartAllowed(
    userRequestedConnection: Boolean,
    autoConnectEnabled: Boolean,
    explicitUserStart: Boolean,
): Boolean = userRequestedConnection && (explicitUserStart || autoConnectEnabled)

internal object SkryonVpnConnectionIntent {
    const val EXTRA_EXPLICIT_VPN_CONNECT = "com.v2ray.ang.extra.EXPLICIT_VPN_CONNECT"
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

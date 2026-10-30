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

/** Protect a live VPN daemon from a worker running in another Android process. */
internal fun isRecentSkryonServiceHeartbeat(lastSeenAt: Long, now: Long): Boolean =
    lastSeenAt > 0 && now - lastSeenAt in 0..20_000L

internal object SkryonVpnConnectionIntent {
    const val EXTRA_EXPLICIT_VPN_CONNECT = "com.v2ray.ang.extra.EXPLICIT_VPN_CONNECT"
    private const val KEY_SERVICE_ALIVE_AT = "SKRYON_VPN_SERVICE_ALIVE_AT"
    private const val KEY_USER_REQUESTED_VPN = "SKRYON_USER_REQUESTED_VPN"
    private const val KEY_CONNECT_IN_PROGRESS_SINCE = "SKRYON_CONNECT_IN_PROGRESS_SINCE"
    private const val MANUAL_CONNECT_GRACE_MS = 60_000L

    fun isArmed(): Boolean = MmkvManager.decodeSettingsBool(KEY_USER_REQUESTED_VPN, false)

    fun markVpnServiceAlive() {
        MmkvManager.encodeSettings(KEY_SERVICE_ALIVE_AT, System.currentTimeMillis())
    }

    fun clearVpnServiceHeartbeat() {
        MmkvManager.encodeSettings(KEY_SERVICE_ALIVE_AT, 0L)
    }

    fun isVpnServiceAlive(): Boolean = isRecentSkryonServiceHeartbeat(
        lastSeenAt = MmkvManager.decodeSettingsLong(KEY_SERVICE_ALIVE_AT, 0L),
        now = System.currentTimeMillis(),
    )

    fun markUserConnected() {
        MmkvManager.encodeSettings(KEY_USER_REQUESTED_VPN, true)
        MmkvManager.encodeSettings(KEY_CONNECT_IN_PROGRESS_SINCE, System.currentTimeMillis())
    }

    /**
     * Reserve the same cross-process startup grace period for a background worker.
     * Otherwise the periodic and one-off WorkManager jobs may race and both
     * replace the selected VPN profile during startup.
     */
    fun markAutoConnectionStarting(): Long? {
        if (!isArmed()) return null
        val startedAt = System.currentTimeMillis()
        MmkvManager.encodeSettings(KEY_CONNECT_IN_PROGRESS_SINCE, startedAt)
        return startedAt
    }

    fun finishConnectionAttemptIfStartedAt(startedAt: Long) {
        if (MmkvManager.decodeSettingsLong(KEY_CONNECT_IN_PROGRESS_SINCE, 0L) == startedAt) {
            finishUserConnectionAttempt()
        }
    }

    fun finishUserConnectionAttempt() {
        MmkvManager.encodeSettings(KEY_CONNECT_IN_PROGRESS_SINCE, 0L)
    }

    fun isUserConnectionStarting(): Boolean {
        val started = MmkvManager.decodeSettingsLong(KEY_CONNECT_IN_PROGRESS_SINCE, 0L)
        val elapsed = System.currentTimeMillis() - started
        return started > 0L && elapsed in 0 until MANUAL_CONNECT_GRACE_MS
    }

    fun markUserDisconnected() {
        MmkvManager.encodeSettings(KEY_USER_REQUESTED_VPN, false)
        finishUserConnectionAttempt()
    }

    fun shouldRetry(networkAvailable: Boolean, vpnAlreadyRunning: Boolean): Boolean =
        shouldAutoReconnectVpn(
            enabled = MmkvManager.decodeStartOnBoot(),
            userRequestedConnection = isArmed(),
            networkAvailable = networkAvailable,
            vpnAlreadyRunning = vpnAlreadyRunning,
        )
}

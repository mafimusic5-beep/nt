package com.v2ray.ang.handler

/**
 * Persistent USER intent, separate from Android's connection status.
 *
 * Once Connect is pressed, the user's durable desired state is CONNECTED.
 * No background lifecycle event or loss of Wi-Fi may turn that into OFF.
 * Explicit Disconnect clears the request. Platform VPN permission
 * revocation is also terminal because Android forbids silent reauthorization.
 * Premium VPN resumes across reboot while its last explicit intent is ON.
 */
internal fun shouldAutoReconnectVpn(
    userRequestedConnection: Boolean,
    networkAvailable: Boolean,
    vpnAlreadyRunning: Boolean,
): Boolean = userRequestedConnection && networkAvailable && !vpnAlreadyRunning

/**
 * An OS restart of the VPN daemon must respect the same durable user intent.
 * The legacy/manual-profile boot toggle cannot suppress recovery from
 * an unexpected process death or a lost connection in a premium VPN.
 */
internal fun isPremiumVpnServiceStartAllowed(
    userRequestedConnection: Boolean,
): Boolean = userRequestedConnection

/**
 * Restore a frozen-but-still-running Xray tunnel only after repeated failures,
 * when the underlying network exists and the user did not request OFF.
 * Never restart while a new connection is still being established.
 */
internal fun shouldRestartUnhealthyVpnTunnel(
    userRequestedConnection: Boolean,
    physicalNetworkAvailable: Boolean,
    connectionStarting: Boolean,
    consecutiveFailedChecks: Int,
): Boolean = userRequestedConnection &&
    physicalNetworkAvailable &&
    !connectionStarting &&
    consecutiveFailedChecks >= 3

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
            userRequestedConnection = isArmed(),
            networkAvailable = networkAvailable,
            vpnAlreadyRunning = vpnAlreadyRunning,
        )
}

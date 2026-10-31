package com.v2ray.ang.handler

import android.content.Context
import android.provider.Settings

/**
 * Persistent USER intent, separate from Android's connection status.
 *
 * Boot autostart is separate from recovery of a user-enabled VPN.
 * A network failure is never the same as a manual disconnect.
 */
internal fun shouldAutoReconnectVpn(
    userRequestedConnection: Boolean,
    networkAvailable: Boolean,
    vpnAlreadyRunning: Boolean,
): Boolean = userRequestedConnection && networkAvailable && !vpnAlreadyRunning

/**
 * Android may try sticky or always-on restart before BOOT_COMPLETED is
 * dispatched. Do not restore an armed session from an earlier boot.
 */
internal fun isArmedForBoot(
    userRequestedConnection: Boolean,
    requestedBootCount: Int?,
    currentBootCount: Int?,
): Boolean = userRequestedConnection &&
    (requestedBootCount == null || currentBootCount == null || requestedBootCount == currentBootCount)

internal fun isPremiumVpnServiceStartAllowed(
    userRequestedConnection: Boolean,
): Boolean = userRequestedConnection

/** Protect a live VPN daemon from a worker running in another Android process. */
internal fun isRecentSkryonServiceHeartbeat(lastSeenAt: Long, now: Long): Boolean =
    lastSeenAt > 0 && now - lastSeenAt in 0..20_000L

internal object SkryonVpnConnectionIntent {
    const val EXTRA_EXPLICIT_VPN_CONNECT = "com.v2ray.ang.extra.EXPLICIT_VPN_CONNECT"
    private const val KEY_SERVICE_ALIVE_AT = "SKRYON_VPN_SERVICE_ALIVE_AT"
    private const val KEY_USER_REQUESTED_VPN = "SKRYON_USER_REQUESTED_VPN"
    private const val KEY_REQUESTED_BOOT = "SKRYON_VPN_REQUESTED_BOOT_COUNT"
    private const val KEY_CONNECT_IN_PROGRESS_SINCE = "SKRYON_CONNECT_IN_PROGRESS_SINCE"
    private const val MANUAL_CONNECT_GRACE_MS = 60_000L

    fun isArmed(): Boolean = MmkvManager.decodeSettingsBool(KEY_USER_REQUESTED_VPN, false)

    private fun bootCount(context: Context): Int? = runCatching {
        Settings.Global.getInt(context.contentResolver, Settings.Global.BOOT_COUNT)
    }.getOrNull()

    fun isArmedForCurrentBoot(context: Context): Boolean =
        isArmedForBoot(
            userRequestedConnection = isArmed(),
            requestedBootCount = MmkvManager.decodeSettingsString(KEY_REQUESTED_BOOT, "")?.toIntOrNull(),
            currentBootCount = bootCount(context),
        )

    private fun arm(context: Context) {
        MmkvManager.encodeSettings(KEY_USER_REQUESTED_VPN, true)
        bootCount(context)?.let {
            MmkvManager.encodeSettings(KEY_REQUESTED_BOOT, it.toString())
        }
    }

    /** Boot and an explicit auto-start toggle can arm a background session. */
    fun markBootAutoConnectRequested(context: Context) {
        val wasArmed = isArmedForCurrentBoot(context)
        arm(context)
        if (!wasArmed) finishUserConnectionAttempt()
    }

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

    fun markUserConnected(context: Context) {
        arm(context)
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
        MmkvManager.encodeSettings(KEY_REQUESTED_BOOT, "")
        finishUserConnectionAttempt()
    }

    fun shouldRetry(context: Context, networkAvailable: Boolean, vpnAlreadyRunning: Boolean): Boolean =
        shouldAutoReconnectVpn(
            userRequestedConnection = isArmedForCurrentBoot(context),
            networkAvailable = networkAvailable,
            vpnAlreadyRunning = vpnAlreadyRunning,
        )
}

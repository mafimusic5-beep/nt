package com.v2ray.ang.handler

import android.content.Context
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.net.VpnService
import android.util.Log
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import com.v2ray.ang.AppConfig
import com.v2ray.ang.network.EmeryBackendClient
import com.v2ray.ang.ui.premium.SKRYON_VPN_SESSION_ID_PREF
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.sync.Mutex

/**
 * Periodic fallback is intentionally coarse (Android's minimum is 15 min).
 * Connectivity callbacks in the foreground process and active VPN service
 * handle fast recovery. No private user data is transmitted by scheduling.
 */
/**
 * Only a definitely absent Android VPN network may make a persisted lease
 * stale. A live tunnel or another startup (manual or automatic) owns the
 * lease and must never have its profile replaced by WorkManager.
 */
internal fun shouldRecoverStaleVpnLease(
    hasSession: Boolean,
    vpnNetworkActive: Boolean,
    connectionStarting: Boolean,
    serviceAlive: Boolean,
): Boolean = hasSession && !vpnNetworkActive && !connectionStarting && !serviceAlive

internal fun isRetryableSkryonReconnectFailure(reason: String?): Boolean {
    val code = reason.orEmpty().trim().lowercase()
    return code in setOf(
        "network",
        "timeout",
        "server_capacity_unavailable",
        "pool_backend_unreachable",
        "credential_install_failed",
        "server_config_unavailable",
        "server_selection_refresh_required",
        "assignment_install_in_progress",
        "assignment_maintenance_in_progress",
        "assignment_state_changed_retry",
    ) || (code.startsWith("http_5") && code.length == 8 && code.substring(5).all(Char::isDigit))
}

internal object SkryonVpnAutoConnectScheduler {
    private const val ONE_TIME_WORK = "skryon_vpn_auto_connect_once"
    private const val PERIODIC_WORK = "skryon_vpn_auto_connect_periodic"

    fun ensureScheduled(context: Context) {
        if (!SkryonVpnConnectionIntent.isArmed() || !MmkvManager.decodeStartOnBoot()) return
        val constraints = Constraints.Builder()
            .setRequiredNetworkType(NetworkType.CONNECTED)
            .build()
        val periodic = PeriodicWorkRequestBuilder<SkryonVpnAutoConnectWorker>(15, TimeUnit.MINUTES)
            .setConstraints(constraints)
            .build()
        WorkManager.getInstance(context.applicationContext)
            .enqueueUniquePeriodicWork(PERIODIC_WORK, ExistingPeriodicWorkPolicy.KEEP, periodic)
        // Do not schedule an immediate second session while the user is
        // in the middle of the initial Connect flow. Network recovery is
        // handled immediately by the active service's callback.
    }

    fun requestOnce(context: Context) {
        if (!SkryonVpnConnectionIntent.isArmed() || !MmkvManager.decodeStartOnBoot()) return
        val request = OneTimeWorkRequestBuilder<SkryonVpnAutoConnectWorker>()
            .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
            .setInitialDelay(10, TimeUnit.SECONDS)
            .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 30, TimeUnit.SECONDS)
            .build()
        WorkManager.getInstance(context.applicationContext)
            .enqueueUniqueWork(ONE_TIME_WORK, ExistingWorkPolicy.KEEP, request)
    }

    fun cancel(context: Context) {
        WorkManager.getInstance(context.applicationContext).apply {
            cancelUniqueWork(ONE_TIME_WORK)
            cancelUniqueWork(PERIODIC_WORK)
        }
    }
}

internal class SkryonVpnAutoConnectWorker(
    context: Context,
    params: WorkerParameters,
) : CoroutineWorker(context, params) {
    private companion object {
        // Periodic and one-time workers run concurrently in the same app process.
        private val connectMutex = Mutex()
    }

    override suspend fun doWork(): Result {
        connectMutex.lock()
        return try {
            reconnectIfRequested()
        } finally {
            connectMutex.unlock()
        }
    }

    private fun vpnNetworkPresent(): Boolean? = runCatching {
        val manager = applicationContext.getSystemService(Context.CONNECTIVITY_SERVICE)
            as? ConnectivityManager ?: return null
        manager.allNetworks.any { network ->
            manager.getNetworkCapabilities(network)
                ?.hasTransport(NetworkCapabilities.TRANSPORT_VPN) == true
        }
    }.getOrNull()

    private suspend fun reconnectIfRequested(): Result {
        if (!SkryonVpnConnectionIntent.shouldRetry(
                networkAvailable = true,
                vpnAlreadyRunning = V2RayServiceManager.isRunning(),
            )
        ) return Result.success()

        // Manual Connect has priority. The normal worker runs in a different
        // process and cannot inspect a Compose ViewModel's in-flight state.
        if (SkryonVpnConnectionIntent.isUserConnectionStarting()) return Result.retry()

        // Never prompt for VPN consent from a background worker, and never
        // attempt to replace another running VPN app.
        if (VpnService.prepare(applicationContext) != null) return Result.success()

        // The service core lives in another process. Android may hide the
        // disallowed VPN app's own network from ConnectivityManager; its
        // short-lived daemon pulse prevents a false "stale session" cleanup.
        if (SkryonVpnConnectionIntent.isVpnServiceAlive()) return Result.success()
        val networkPresent = vpnNetworkPresent() ?: return Result.retry()
        if (networkPresent) {
            // The VPN service owns its current session even when its Xray
            // core runs in a different process from this WorkManager worker.
            return Result.success()
        }

        val profile = EmeryAccessManager.loadProfile() ?: return Result.success()
        val session = MmkvManager.decodeSettingsString(SKRYON_VPN_SESSION_ID_PREF, "")
            ?.trim().orEmpty()
        if (shouldRecoverStaleVpnLease(
                hasSession = session.isNotBlank(),
                vpnNetworkActive = networkPresent,
                connectionStarting = SkryonVpnConnectionIntent.isUserConnectionStarting(),
                serviceAlive = SkryonVpnConnectionIntent.isVpnServiceAlive(),
            )
        ) {
            // Process death might leave a lease in MMKV without a live Android
            // VPN network. Release only this installation's old signed lease.
            // An unconfirmed network error cannot be treated as a release.
            val released = EmeryBackendClient.releaseVpnSession(profile.accessKey, session)
            val reason = released.exceptionOrNull()?.message.orEmpty()
            if (released.isFailure && reason !in setOf("vpn_session_mismatch", "vpn_session_expired")) {
                return if (isRetryableSkryonReconnectFailure(reason)) Result.retry() else Result.success()
            }
            if (SkryonVpnConnectionIntent.isUserConnectionStarting()) return Result.retry()
            if (SkryonVpnConnectionIntent.isVpnServiceAlive() || vpnNetworkPresent() != false) {
                return Result.retry()
            }
            if (MmkvManager.decodeSettingsString(SKRYON_VPN_SESSION_ID_PREF, "") == session) {
                MmkvManager.encodeSettings(SKRYON_VPN_SESSION_ID_PREF, "")
            } else {
                // Another connection has acquired a newer lease; never clear it.
                return Result.retry()
            }
        }

        if (SkryonVpnConnectionIntent.isUserConnectionStarting()) return Result.retry()
        if (!SkryonVpnConnectionIntent.shouldRetry(true, V2RayServiceManager.isRunning())) {
            return Result.success()
        }

        val startedAt = SkryonVpnConnectionIntent.markAutoConnectionStarting()
            ?: return Result.success()
        var startRequested = false
        try {
        val confirmed = EmeryBackendClient.fetchProfile(
            accessKey = profile.accessKey,
            requireDeviceInventory = true,
        ).getOrElse { error ->
            Log.w(AppConfig.TAG, "Auto-connect device check failed: ${error.message}")
            return if (isRetryableSkryonReconnectFailure(error.message)) Result.retry() else Result.success()
        }
        if (!SkryonVpnConnectionIntent.shouldRetry(true, V2RayServiceManager.isRunning())) {
            return Result.success()
        }

        // Only a manual region choice is sticky. "Random region" draws a new
        // available pool on each NEW connection after a stopped service.
        // Underlay dropouts on a still-live tunnel keep its existing server.
        val pinnedId = SkryonRegionPreference.pinnedServerId()
        val availableIds = if (pinnedId != null) {
            emptyList()
        } else {
            EmeryBackendClient.fetchVpnServers()
                .getOrElse { return Result.retry() }
                .filter { it.isAvailable }
                .map { it.id }
        }
        val nextPoolId = chooseRegionPoolId(pinnedId, availableIds) ?: return Result.retry()
        val connection = EmeryVpnSync.connectToServer(confirmed.accessKey, nextPoolId)
            .getOrElse { error ->
                Log.w(AppConfig.TAG, "Auto-connect pool acquire failed: ${error.message}")
                return if (isRetryableSkryonReconnectFailure(error.message)) Result.retry() else Result.success()
            }

        if (!SkryonVpnConnectionIntent.shouldRetry(true, V2RayServiceManager.isRunning())) {
            EmeryBackendClient.releaseVpnSession(confirmed.accessKey, connection.sessionId)
            if (MmkvManager.decodeSettingsString(SKRYON_VPN_SESSION_ID_PREF, "") == connection.sessionId) {
                MmkvManager.encodeSettings(SKRYON_VPN_SESSION_ID_PREF, "")
            }
            return Result.success()
        }

        // This is a background auto-start, NOT a new manual user action.
        // Starting without a GUID keeps the explicit/automatic service gate
        // active; EmeryVpnSync has already selected the desired profile.
        val started = V2RayServiceManager.startVService(applicationContext)
        if (!started) {
            EmeryBackendClient.releaseVpnSession(confirmed.accessKey, connection.sessionId)
            if (MmkvManager.decodeSettingsString(SKRYON_VPN_SESSION_ID_PREF, "") == connection.sessionId) {
                MmkvManager.encodeSettings(SKRYON_VPN_SESSION_ID_PREF, "")
            }
            return Result.retry()
        }
        startRequested = true
        return Result.success()
        } finally {
            if (!startRequested) {
                SkryonVpnConnectionIntent.finishConnectionAttemptIfStartedAt(startedAt)
            }
        }
    }
}

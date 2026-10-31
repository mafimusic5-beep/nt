package com.v2ray.ang.handler

import android.content.Context
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.net.VpnService
import android.os.Build
import android.util.Log
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OutOfQuotaPolicy
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
    private const val BOOT_WORK = "skryon_vpn_boot_recovery"
    private const val POST_START_WORK = "skryon_vpn_post_start_verify"
    private const val ONE_TIME_WORK = "skryon_vpn_auto_connect_once"
    private const val PERIODIC_WORK = "skryon_vpn_auto_connect_periodic"

    fun ensureScheduled(context: Context) {
        if (!SkryonVpnConnectionIntent.isArmed()) return
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
        if (!SkryonVpnConnectionIntent.isArmed()) return
        val request = OneTimeWorkRequestBuilder<SkryonVpnAutoConnectWorker>()
            .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
            .setInitialDelay(10, TimeUnit.SECONDS)
            .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 30, TimeUnit.SECONDS)
            .build()
        WorkManager.getInstance(context.applicationContext)
            .enqueueUniqueWork(ONE_TIME_WORK, ExistingWorkPolicy.KEEP, request)
    }

    /**
     * A daemon may fail while the previous one-time worker is still finishing.
     * APPEND_OR_REPLACE preserves that worker's signed acquire/release flow and
     * guarantees a new recovery check afterwards. Network callbacks keep using
     * KEEP to avoid flooding the queue.
     */
    fun requestAfterUnexpectedStop(context: Context) {
        if (!SkryonVpnConnectionIntent.isArmed()) return
        val request = OneTimeWorkRequestBuilder<SkryonVpnAutoConnectWorker>()
            .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
            .setInitialDelay(10, TimeUnit.SECONDS)
            .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 30, TimeUnit.SECONDS)
            .build()
        WorkManager.getInstance(context.applicationContext)
            .enqueueUniqueWork(ONE_TIME_WORK, ExistingWorkPolicy.APPEND_OR_REPLACE, request)
    }

    /**
     * Recover as soon as the OS supplies Internet after boot, unlock, or a
     * package update. Android's scheduled work may otherwise delay the first
     * one-off retry until much later, especially on televisions.
     *
     * Expedited JobScheduler work on Android 12+ requests prompt execution,
     * with a safe fallback if expedited quota is exhausted. Older Android
     * versions use a normal immediate WorkManager job to avoid requiring
     * getForegroundInfo() for the pre-Android-12 expedited implementation.
     */
    fun requestAfterBoot(context: Context) {
        if (!SkryonVpnConnectionIntent.isArmed()) return
        val request = OneTimeWorkRequestBuilder<SkryonVpnAutoConnectWorker>()
            .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
            .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 30, TimeUnit.SECONDS)
            .apply {
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                    setExpedited(OutOfQuotaPolicy.RUN_AS_NON_EXPEDITED_WORK_REQUEST)
                }
            }.build()
        WorkManager.getInstance(context.applicationContext)
            .enqueueUniqueWork(BOOT_WORK, ExistingWorkPolicy.KEEP, request)
    }

    /**
     * startForegroundService() can return before Android actually launches the
     * VPN service. If Android denies the background start, there is no service
     * callback to request a retry. Recheck the daemon after the 60-second
     * startup grace, instead of waiting for the next 15-minute periodic job.
     */
    fun requestPostStartVerification(context: Context) {
        if (!SkryonVpnConnectionIntent.isArmed()) return
        val request = OneTimeWorkRequestBuilder<SkryonVpnAutoConnectWorker>()
            .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
            .setInitialDelay(75, TimeUnit.SECONDS)
            .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 30, TimeUnit.SECONDS)
            .build()
        WorkManager.getInstance(context.applicationContext)
            .enqueueUniqueWork(POST_START_WORK, ExistingWorkPolicy.REPLACE, request)
    }

    fun cancel(context: Context) {
        WorkManager.getInstance(context.applicationContext).apply {
            cancelUniqueWork(POST_START_WORK)
            cancelUniqueWork(BOOT_WORK)
            cancelUniqueWork(ONE_TIME_WORK)
            cancelUniqueWork(PERIODIC_WORK)
        }
    }
}

/**
 * Shared, serialized, signed VPN recovery. Boot-time foreground coordinator
 * and WorkManager use this SAME session acquisition logic, avoiding races.
 *
 * Returns true when the attempt is retryable. Authentication, entitlement and
 * Android VPN permission checks remain mandatory in either launch path.
 */
internal class SkryonVpnRecoveryRunner(
    private val applicationContext: Context,
) {
    private companion object {
        val connectMutex = Mutex()
    }

    suspend fun run(): Boolean {
        connectMutex.lock()
        return try {
            reconnectIfRequested()
        } finally {
            connectMutex.unlock()
        }
    }

    private fun vpnNetworkPresent(): Boolean? = runCatching {
        val manager = applicationContext.getSystemService(Context.CONNECTIVITY_SERVICE)
            as? ConnectivityManager ?: return@runCatching null
        manager.allNetworks.any { network ->
            manager.getNetworkCapabilities(network)
                ?.hasTransport(NetworkCapabilities.TRANSPORT_VPN) == true
        }
    }.getOrNull()

    private suspend fun reconnectIfRequested(): Boolean {
        if (!SkryonVpnConnectionIntent.shouldRetry(
                networkAvailable = true,
                vpnAlreadyRunning = V2RayServiceManager.isRunning(),
            )
        ) return false

        // Manual Connect has priority. The normal worker runs in a different
        // process and cannot inspect a Compose ViewModel's in-flight state.
        if (SkryonVpnConnectionIntent.isUserConnectionStarting()) return true

        // Never prompt for VPN consent from a background worker, and never
        // attempt to replace another running VPN app.
        if (VpnService.prepare(applicationContext) != null) {
            // An old ON preference is not a replacement for Android's VPN
            // consent. Do not repeatedly prompt from a background worker.
            Log.w(AppConfig.TAG, "Skryon recovery blocked: Android VPN permission is not granted")
            return false
        }

        // The service core lives in another process. Android may hide the
        // disallowed VPN app's own network from ConnectivityManager; its
        // short-lived daemon pulse prevents a false "stale session" cleanup.
        if (SkryonVpnConnectionIntent.isVpnServiceAlive()) return false
        val networkPresent = vpnNetworkPresent() ?: return true
        if (networkPresent) {
            Log.i(AppConfig.TAG, "Skryon recovery waiting: VPN interface still registered")
            // Android can leave an old VPN network registered briefly after
            // the service process died. Do not replace it, but do NOT treat
            // this as recovered either: retry when that network disappears.
            // This also avoids interfering with another VPN app's network.
            return true
        }

        val profile = EmeryAccessManager.loadProfile() ?: run {
            Log.w(AppConfig.TAG, "Skryon recovery skipped: no activated local profile")
            return false
        }
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
                return if (isRetryableSkryonReconnectFailure(reason)) true else false
            }
            if (SkryonVpnConnectionIntent.isUserConnectionStarting()) return true
            if (SkryonVpnConnectionIntent.isVpnServiceAlive() || vpnNetworkPresent() != false) {
                return true
            }
            if (MmkvManager.decodeSettingsString(SKRYON_VPN_SESSION_ID_PREF, "") == session) {
                MmkvManager.encodeSettings(SKRYON_VPN_SESSION_ID_PREF, "")
            } else {
                // Another connection has acquired a newer lease; never clear it.
                return true
            }
        }

        if (SkryonVpnConnectionIntent.isUserConnectionStarting()) return true
        if (!SkryonVpnConnectionIntent.shouldRetry(true, V2RayServiceManager.isRunning())) {
            return false
        }

        val startedAt = SkryonVpnConnectionIntent.markAutoConnectionStarting()
            ?: return false
        var startRequested = false
        try {
        val confirmed = EmeryBackendClient.fetchProfile(
            accessKey = profile.accessKey,
            requireDeviceInventory = true,
        ).getOrElse { error ->
            Log.w(AppConfig.TAG, "Auto-connect device check failed: ${error.message}")
            return if (isRetryableSkryonReconnectFailure(error.message)) true else false
        }
        if (!SkryonVpnConnectionIntent.shouldRetry(true, V2RayServiceManager.isRunning())) {
            return false
        }

        // Only a manual region choice is sticky. "Random region" draws a new
        // available pool on each NEW connection after a stopped service.
        // Underlay dropouts on a still-live tunnel keep its existing server.
        val pinnedId = SkryonRegionPreference.pinnedServerId()
        val availableIds = if (pinnedId != null) {
            emptyList()
        } else {
            EmeryBackendClient.fetchVpnServers()
                .getOrElse { return true }
                .filter { it.isAvailable }
                .map { it.id }
        }
        val nextPoolId = chooseRegionPoolId(pinnedId, availableIds) ?: return true
        val connection = EmeryVpnSync.connectToServer(confirmed.accessKey, nextPoolId)
            .getOrElse { error ->
                Log.w(AppConfig.TAG, "Auto-connect pool acquire failed: ${error.message}")
                return if (isRetryableSkryonReconnectFailure(error.message)) true else false
            }

        if (!SkryonVpnConnectionIntent.shouldRetry(true, V2RayServiceManager.isRunning())) {
            EmeryBackendClient.releaseVpnSession(confirmed.accessKey, connection.sessionId)
            if (MmkvManager.decodeSettingsString(SKRYON_VPN_SESSION_ID_PREF, "") == connection.sessionId) {
                MmkvManager.encodeSettings(SKRYON_VPN_SESSION_ID_PREF, "")
            }
            return false
        }

        // This is a background auto-start, NOT a new manual user action.
        // Starting without a GUID keeps the explicit/automatic service gate
        // active; EmeryVpnSync has already selected the desired profile.
        val started = V2RayServiceManager.startVService(applicationContext)
        if (!started) {
            Log.w(AppConfig.TAG, "Skryon recovery failed: foreground VPN service start rejected")
            EmeryBackendClient.releaseVpnSession(confirmed.accessKey, connection.sessionId)
            if (MmkvManager.decodeSettingsString(SKRYON_VPN_SESSION_ID_PREF, "") == connection.sessionId) {
                MmkvManager.encodeSettings(SKRYON_VPN_SESSION_ID_PREF, "")
            }
            return true
        }
        startRequested = true
        SkryonVpnAutoConnectScheduler.requestPostStartVerification(applicationContext)
        return false
        } finally {
            if (!startRequested) {
                SkryonVpnConnectionIntent.finishConnectionAttemptIfStartedAt(startedAt)
            }
        }
    }
}

/**
 * WorkManager is a fallback for network recovery and process death.
 * BOOT_COMPLETED also creates a short-lived foreground coordinator, because
 * Android 12+ can reject startForegroundService() from a background worker.
 */
internal class SkryonVpnAutoConnectWorker(
    context: Context,
    params: WorkerParameters,
) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result =
        if (SkryonVpnRecoveryRunner(applicationContext).run()) Result.retry()
        else Result.success()
}

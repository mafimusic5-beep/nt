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

/**
 * Periodic fallback is intentionally coarse (Android's minimum is 15 min).
 * Connectivity callbacks in the foreground process and active VPN service
 * handle fast recovery. No private user data is transmitted by scheduling.
 */
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
        requestOnce(context)
    }

    fun requestOnce(context: Context) {
        if (!SkryonVpnConnectionIntent.isArmed() || !MmkvManager.decodeStartOnBoot()) return
        val request = OneTimeWorkRequestBuilder<SkryonVpnAutoConnectWorker>()
            .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
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
    override suspend fun doWork(): Result {
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

        val session = MmkvManager.decodeSettingsString(SKRYON_VPN_SESSION_ID_PREF, "")
            ?.trim().orEmpty()
        if (session.isNotBlank()) {
            // A foreground connect may already be starting; avoid replacing
            // its in-flight profile. A stale session will be reconciled on a
            // subsequent retry/recovery path rather than by racing it here.
            return Result.retry()
        }

        val profile = EmeryAccessManager.loadProfile() ?: return Result.success()
        val confirmed = EmeryBackendClient.fetchProfile(
            accessKey = profile.accessKey,
            requireDeviceInventory = true,
        ).getOrElse { error ->
            Log.w(AppConfig.TAG, "Auto-connect device check failed: ${error.message}")
            return if (error.message == "network") Result.retry() else Result.success()
        }
        if (!SkryonVpnConnectionIntent.shouldRetry(true, V2RayServiceManager.isRunning())) {
            return Result.success()
        }

        val servers = EmeryBackendClient.fetchVpnServers().getOrElse { error ->
            Log.w(AppConfig.TAG, "Auto-connect server list unavailable: ${error.message}")
            return Result.retry()
        }
        val pool = chooseFreestVpnPool(servers) ?: return Result.retry()
        // Do not bind to the last-used server: choose the lowest-occupancy
        // available pool on each new connection.
        val connection = EmeryVpnSync.connectToServer(confirmed.accessKey, pool.id)
            .getOrElse { error ->
                Log.w(AppConfig.TAG, "Auto-connect pool acquire failed: ${error.message}")
                return if (error.message in setOf("network", "server_capacity_unavailable", "pool_backend_unreachable", "assignment_install_in_progress")) {
                    Result.retry()
                } else {
                    Result.success()
                }
            }

        if (!SkryonVpnConnectionIntent.shouldRetry(true, V2RayServiceManager.isRunning())) {
            EmeryBackendClient.releaseVpnSession(confirmed.accessKey, connection.sessionId)
            if (MmkvManager.decodeSettingsString(SKRYON_VPN_SESSION_ID_PREF, "") == connection.sessionId) {
                MmkvManager.encodeSettings(SKRYON_VPN_SESSION_ID_PREF, "")
            }
            return Result.success()
        }

        val started = V2RayServiceManager.startVService(applicationContext, connection.selectedGuid)
        if (!started) {
            EmeryBackendClient.releaseVpnSession(confirmed.accessKey, connection.sessionId)
            if (MmkvManager.decodeSettingsString(SKRYON_VPN_SESSION_ID_PREF, "") == connection.sessionId) {
                MmkvManager.encodeSettings(SKRYON_VPN_SESSION_ID_PREF, "")
            }
            return Result.retry()
        }
        return Result.success()
    }
}

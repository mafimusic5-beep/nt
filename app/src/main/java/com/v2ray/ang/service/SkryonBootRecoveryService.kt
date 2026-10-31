package com.v2ray.ang.service

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.net.VpnService
import android.os.Build
import android.os.IBinder
import android.os.SystemClock
import android.util.Log
import androidx.core.app.NotificationCompat
import com.v2ray.ang.AppConfig
import com.v2ray.ang.R
import com.v2ray.ang.handler.EmeryAccessManager
import com.v2ray.ang.handler.MmkvManager
import com.v2ray.ang.handler.SkryonVpnConnectionIntent
import com.v2ray.ang.handler.SkryonVpnRecoveryRunner
import com.v2ray.ang.ui.premium.PremiumActivity
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

/**
 * BOOT_COMPLETED permits a short foreground-service launch on Android 12+.
 * The old WorkManager-only boot path tried to start a VPN foreground service
 * long AFTER this exemption, when the OS could silently reject its launch.
 *
 * This process-level coordinator starts during the boot broadcast, immediately
 * promotes itself to foreground, then waits for the physical network and runs
 * the SAME signed, device-bound, serialized recovery as WorkManager. It NEVER
 * opens an Activity and never prompts for VPN authorization in background.
 */
class SkryonBootRecoveryService : Service() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private var recoveryJob: Job? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        val notifications = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            notifications.createNotificationChannel(
                NotificationChannel(CHANNEL_ID, "Skryon VPN startup", NotificationManager.IMPORTANCE_LOW)
            )
        }
        val openApp = Intent(this, PremiumActivity::class.java)
        val openIntent = PendingIntent.getActivity(
            this, 0, openApp,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val notification = NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_stat_name)
            .setContentTitle("Skryon VPN")
            .setContentText("Восстановление VPN после запуска устройства")
            .setContentIntent(openIntent)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .build()
        // The manifest declares specialUse and FOREGROUND_SERVICE_SPECIAL_USE.
        // Unlike dataSync, specialUse is not among Android 15's boot bans.
        startForeground(NOTIFICATION_ID, notification)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (!authorizedForBootRecovery()) {
            stopSelf(startId)
            return START_NOT_STICKY
        }
        if (recoveryJob?.isActive == true) return START_NOT_STICKY

        recoveryJob = scope.launch {
            try {
                val until = SystemClock.elapsedRealtime() + MAX_BOOT_RECOVERY_MS
                var attempts = 0
                while (isActive && attempts < MAX_ATTEMPTS && SystemClock.elapsedRealtime() < until) {
                    if (!authorizedForBootRecovery()) break
                    if (VpnService.prepare(applicationContext) != null) {
                        Log.w(AppConfig.TAG, "Skryon boot recovery requires user-granted VPN permission")
                        break
                    }
                    if (SkryonVpnConnectionIntent.isVpnServiceAlive()) {
                        Log.i(AppConfig.TAG, "Skryon boot recovery: VPN daemon already alive")
                        break
                    }
                    if (!physicalInternetAvailable()) {
                        delay(NETWORK_WAIT_MS)
                        continue
                    }

                    attempts += 1
                    val retry = try {
                        SkryonVpnRecoveryRunner(applicationContext).run()
                    } catch (error: Exception) {
                        Log.w(AppConfig.TAG, "Skryon boot recovery attempt failed", error)
                        true
                    }
                    if (!retry) {
                        // startForegroundService may return before the VPN
                        // daemon actually becomes active. Give it startup time.
                        delay(START_CONFIRMATION_MS)
                        if (SkryonVpnConnectionIntent.isVpnServiceAlive() ||
                            !authorizedForBootRecovery()
                        ) break
                    } else {
                        delay(RETRY_INTERVAL_MS)
                    }
                }
            } finally {
                stopSelf(startId)
            }
        }
        return START_NOT_STICKY
    }

    private fun authorizedForBootRecovery(): Boolean =
        MmkvManager.decodeStartOnBoot() &&
            SkryonVpnConnectionIntent.isArmed() &&
            EmeryAccessManager.loadProfile() != null

    private fun physicalInternetAvailable(): Boolean = runCatching {
        val connectivity = getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
        connectivity.allNetworks.any { network ->
            connectivity.getNetworkCapabilities(network)?.let { caps ->
                !caps.hasTransport(NetworkCapabilities.TRANSPORT_VPN) &&
                    caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
            } == true
        }
    }.getOrDefault(false)

    override fun onDestroy() {
        recoveryJob?.cancel()
        scope.cancel()
        super.onDestroy()
    }

    private companion object {
        const val CHANNEL_ID = "skryon_vpn_boot_recovery"
        const val NOTIFICATION_ID = 1188
        const val MAX_BOOT_RECOVERY_MS = 180_000L
        const val NETWORK_WAIT_MS = 5_000L
        const val START_CONFIRMATION_MS = 10_000L
        const val RETRY_INTERVAL_MS = 15_000L
        const val MAX_ATTEMPTS = 9
    }
}

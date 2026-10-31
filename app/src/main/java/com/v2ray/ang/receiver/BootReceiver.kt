package com.v2ray.ang.receiver

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log
import androidx.core.content.ContextCompat
import com.v2ray.ang.service.V2RayVpnService
import com.v2ray.ang.AppConfig
import com.v2ray.ang.handler.EmeryAccessManager
import com.v2ray.ang.handler.MmkvManager
import com.v2ray.ang.handler.SkryonVpnConnectionIntent
import com.v2ray.ang.handler.SkryonVpnAutoConnectScheduler
import com.v2ray.ang.handler.V2RayServiceManager

/** Android may deliver BOOT_COMPLETED before a usable Wi-Fi is available. */
internal fun shouldScheduleSkryonRecoveryOnBootEvent(action: String?): Boolean =
    action == Intent.ACTION_BOOT_COMPLETED ||
        action == Intent.ACTION_USER_UNLOCKED ||
        action == Intent.ACTION_MY_PACKAGE_REPLACED

/** Events exempt from Android 12+ background FGS start restrictions. */
internal fun shouldStartForegroundRecoveryFromBoot(action: String?): Boolean =
    action == Intent.ACTION_BOOT_COMPLETED ||
        action == Intent.ACTION_MY_PACKAGE_REPLACED

internal fun shouldResumePremiumVpnAtBoot(
    startOnBootEnabled: Boolean,
    userRequestedConnection: Boolean,
): Boolean = startOnBootEnabled && userRequestedConnection

class BootReceiver : BroadcastReceiver() {
    /**
     * Starts a saved manual profile immediately. Premium profiles are fail-closed:
     * the backend must confirm the signed current device and complete inventory
     * before the VPN service is allowed to start after boot.
     */
    override fun onReceive(context: Context?, intent: Intent?) {
        val action = intent?.action
        Log.i(AppConfig.TAG, "BootReceiver received: $action")

        if (context == null || !shouldScheduleSkryonRecoveryOnBootEvent(action)) {
            Log.w(AppConfig.TAG, "BootReceiver: Invalid context or action")
            return
        }

        val applicationContext = context.applicationContext
        val premiumProfile = EmeryAccessManager.loadProfile()
        // Boot and unlock must honor the SAVED toggle; removing the UI switch
        // previously made this user preference impossible to control.
        val startOnBootEnabled = MmkvManager.decodeStartOnBoot()
        if (premiumProfile != null) {
            if (action == Intent.ACTION_BOOT_COMPLETED ||
                action == Intent.ACTION_USER_UNLOCKED
            ) {
                if (!startOnBootEnabled) {
                    // Disarm only a PREVIOUS-boot request. If the user has
                    // already pressed Connect during this boot, a delayed
                    // BOOT_COMPLETED broadcast must not undo their action.
                    if (SkryonVpnConnectionIntent.disarmPreviousBootRequestIfDisabled()) {
                        SkryonVpnAutoConnectScheduler.cancel(applicationContext)
                    }
                    Log.i(AppConfig.TAG, "BootReceiver: Premium start-on-boot disabled by user")
                    return
                }
            }
            if (!shouldResumePremiumVpnAtBoot(
                    startOnBootEnabled = startOnBootEnabled || action == Intent.ACTION_MY_PACKAGE_REPLACED,
                    userRequestedConnection = SkryonVpnConnectionIntent.isArmed(),
                )
            ) {
                Log.i(AppConfig.TAG, "BootReceiver: Premium VPN is OFF or autostart disabled")
                return
            }
        }
        if (premiumProfile == null) {
            if (action != Intent.ACTION_BOOT_COMPLETED &&
                action != Intent.ACTION_MY_PACKAGE_REPLACED
            ) return
            if (!startOnBootEnabled) {
                Log.i(AppConfig.TAG, "BootReceiver: Legacy start on boot disabled")
                return
            }
            if (MmkvManager.getSelectServer().isNullOrEmpty()) {
                Log.w(AppConfig.TAG, "BootReceiver: No server selected")
                return
            }
            Log.i(AppConfig.TAG, "BootReceiver: Starting saved non-premium V2Ray profile")
            V2RayServiceManager.startVService(applicationContext)
            return
        }

        // WorkManager waits for actual network availability. Directly
        // requesting a premium session here fails when Wi-Fi starts later
        // (especially common on Android TV after a reboot).
        // Start the ACTUAL VPN foreground service before invoking any
        // WorkManager / SQLite setup, while the OS boot exemption is active.
        // WorkManager remains a network/process death fallback.

        // Start the ACTUAL VPN foreground service directly under BOOT_COMPLETED
        // exemption; it will acquire signed device-bound session asynchronously
        // once the network comes up. No Activity or second FGS start is needed.
        // USER_UNLOCKED is not a permitted FGS start exemption; its receiver
        // only queues WorkManager. A system Always-on VPN remains supported.
        var directBootStarted = false
        if (shouldStartForegroundRecoveryFromBoot(action)) {
            try {
                ContextCompat.startForegroundService(
                    applicationContext,
                    Intent(applicationContext, V2RayVpnService::class.java).apply {
                        putExtra(V2RayVpnService.EXTRA_BOOT_RECOVERY_START, true)
                    },
                )
                directBootStarted = true
                Log.i(AppConfig.TAG, "BootReceiver: Started headless VpnService recovery")
            } catch (error: Exception) {
                Log.e(AppConfig.TAG, "BootReceiver: Direct VPN boot launch rejected", error)
            }
        }
        SkryonVpnAutoConnectScheduler.ensureScheduled(applicationContext)
        // Avoid racing a WorkManager lease acquire against the boot-started
        // foreground VpnService. The latter schedules its own fallback when
        // it stops without a tunnel; periodic work remains installed.
        if (!directBootStarted) {
            SkryonVpnAutoConnectScheduler.requestAfterBoot(applicationContext)
        }
        Log.i(AppConfig.TAG, "BootReceiver: Headless VPN boot handled: $action")

    }
}

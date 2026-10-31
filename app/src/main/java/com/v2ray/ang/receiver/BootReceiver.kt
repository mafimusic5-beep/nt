package com.v2ray.ang.receiver

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log
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
                    // User opted out of reconnecting after a reboot. A stale
                    // WorkManager job must not resurrect the previous session.
                    SkryonVpnConnectionIntent.markUserDisconnected()
                    SkryonVpnAutoConnectScheduler.cancel(applicationContext)
                    Log.i(AppConfig.TAG, "BootReceiver: Premium auto-start disabled by user")
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
        SkryonVpnAutoConnectScheduler.ensureScheduled(applicationContext)
        // Force a fresh immediate request after reboot/update/unlock. Ordinary
        // background jobs can retain an old 10s delay or remain in a terminal
        // state when Android kills processes during system startup.
        SkryonVpnAutoConnectScheduler.requestAfterBoot(applicationContext)
        Log.i(AppConfig.TAG, "BootReceiver: Queued premium boot recovery: $action")

    }
}

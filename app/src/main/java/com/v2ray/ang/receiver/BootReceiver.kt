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

class BootReceiver : BroadcastReceiver() {
    /**
     * Starts a saved manual profile immediately. Premium profiles are fail-closed:
     * the backend must confirm the signed current device and complete inventory
     * before the VPN service is allowed to start after boot.
     */
    override fun onReceive(context: Context?, intent: Intent?) {
        Log.i(AppConfig.TAG, "BootReceiver received: ${intent?.action}")

        if (context == null || intent?.action != Intent.ACTION_BOOT_COMPLETED) {
            Log.w(AppConfig.TAG, "BootReceiver: Invalid context or action")
            return
        }

        if (!MmkvManager.decodeStartOnBoot()) {
            Log.i(AppConfig.TAG, "BootReceiver: Auto-start on boot is disabled")
            return
        }

        val applicationContext = context.applicationContext
        val premiumProfile = EmeryAccessManager.loadProfile()
        // Restore a previously requested VPN after reboot, but never override
        // an explicit user disconnect. Enabling the boot toggle alone does not
        // silently reconnect a VPN the user intentionally turned off.
        if (premiumProfile != null && !SkryonVpnConnectionIntent.isArmed()) {
            Log.i(AppConfig.TAG, "BootReceiver: User explicitly disconnected premium VPN")
            return
        }
        if (premiumProfile == null) {
            val selectedGuid = MmkvManager.getSelectServer()
            if (selectedGuid.isNullOrEmpty()) {
                Log.w(AppConfig.TAG, "BootReceiver: No server selected")
                return
            }
            // A cached premium VLESS profile must not be treated as a free
            // manual configuration when its registered-device profile is gone.
            // All Skryon pool starts require a newly signed backend lease.
            if (selectedGuid in MmkvManager.decodeServerList(AppConfig.EMERY_BACKEND_SUBSCRIPTION_ID)) {
                Log.w(AppConfig.TAG, "BootReceiver: Refusing unauthenticated cached Skryon pool profile")
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
        SkryonVpnAutoConnectScheduler.requestOnce(applicationContext)
        Log.i(AppConfig.TAG, "BootReceiver: Queued premium auto-connect with network constraint")

    }
}

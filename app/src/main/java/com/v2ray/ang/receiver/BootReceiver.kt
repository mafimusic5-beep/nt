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
        // Preserve the explicit OFF action, including after a device reboot.
        // For premium VPN, auto-start is only allowed after an explicit
        // connection was armed by the user. Manual profiles keep old behavior.
        if (premiumProfile != null && !SkryonVpnConnectionIntent.isArmed()) {
            Log.i(AppConfig.TAG, "BootReceiver: User explicitly disconnected premium VPN")
            return
        }
        if (premiumProfile == null) {
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
        Log.i(AppConfig.TAG, "BootReceiver: Queued premium auto-connect with network constraint")

    }
}

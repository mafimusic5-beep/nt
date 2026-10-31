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

        val applicationContext = context.applicationContext
        val premiumProfile = EmeryAccessManager.loadProfile()
        // A premium subscription explicitly requested CONNECTED is kept alive
        // across Wi-Fi loss, process death, device sleep and reboot. Its one
        // durable OFF decision is the user's Disconnect button. Legacy/manual
        // V2Ray profiles retain the historical boot preference.
        if (premiumProfile != null && !SkryonVpnConnectionIntent.isArmed()) {
            Log.i(AppConfig.TAG, "BootReceiver: User explicitly disconnected premium VPN")
            return
        }
        if (premiumProfile == null) {
            if (!MmkvManager.decodeStartOnBoot()) {
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
        SkryonVpnAutoConnectScheduler.requestOnce(applicationContext)
        Log.i(AppConfig.TAG, "BootReceiver: Queued premium auto-connect with network constraint")

    }
}

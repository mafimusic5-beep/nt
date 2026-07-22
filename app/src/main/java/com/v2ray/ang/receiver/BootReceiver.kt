package com.v2ray.ang.receiver

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log
import com.v2ray.ang.AppConfig
import com.v2ray.ang.handler.EmeryAccessManager
import com.v2ray.ang.handler.MmkvManager
import com.v2ray.ang.handler.V2RayServiceManager
import com.v2ray.ang.network.EmeryBackendClient
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch

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

        if (MmkvManager.getSelectServer().isNullOrEmpty()) {
            Log.w(AppConfig.TAG, "BootReceiver: No server selected")
            return
        }

        val applicationContext = context.applicationContext
        val premiumProfile = EmeryAccessManager.loadProfile()
        if (premiumProfile == null) {
            Log.i(AppConfig.TAG, "BootReceiver: Starting saved non-premium V2Ray profile")
            V2RayServiceManager.startVService(applicationContext)
            return
        }

        val pendingResult = goAsync()
        CoroutineScope(SupervisorJob() + Dispatchers.IO).launch {
            try {
                val verification = EmeryBackendClient.fetchProfile(
                    accessKey = premiumProfile.accessKey,
                    requireDeviceInventory = true,
                )
                verification.fold(
                    onSuccess = { confirmedProfile ->
                        EmeryAccessManager.saveProfile(confirmedProfile)
                        Log.i(AppConfig.TAG, "BootReceiver: Premium device verified; starting VPN")
                        V2RayServiceManager.startVService(applicationContext)
                    },
                    onFailure = { error ->
                        Log.e(
                            AppConfig.TAG,
                            "BootReceiver: Premium device verification failed; VPN remains stopped",
                            error,
                        )
                    },
                )
            } finally {
                pendingResult.finish()
            }
        }
    }
}

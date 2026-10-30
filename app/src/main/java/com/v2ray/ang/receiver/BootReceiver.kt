package com.v2ray.ang.receiver

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log
import com.v2ray.ang.AppConfig
import com.v2ray.ang.handler.EmeryAccessManager
import com.v2ray.ang.handler.EmeryVpnSync
import com.v2ray.ang.handler.MmkvManager
import com.v2ray.ang.handler.V2RayServiceManager
import com.v2ray.ang.network.EmeryBackendClient
import com.v2ray.ang.ui.premium.SKRYON_SERVER_ID_PREF
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

        val applicationContext = context.applicationContext
        val premiumProfile = EmeryAccessManager.loadProfile()
        if (premiumProfile == null) {
            if (MmkvManager.getSelectServer().isNullOrEmpty()) {
                Log.w(AppConfig.TAG, "BootReceiver: No server selected")
                return
            }
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
                        var serverId = MmkvManager.decodeSettingsLong(SKRYON_SERVER_ID_PREF, -1L)
                        if (serverId <= 0L) {
                            serverId = EmeryBackendClient.fetchVpnServers()
                                .getOrNull()
                                ?.firstOrNull { it.isAvailable }
                                ?.id ?: -1L
                        }
                        if (serverId <= 0L) {
                            Log.w(AppConfig.TAG, "BootReceiver: No premium server available")
                            return@fold
                        }
                        val session = EmeryVpnSync.connectToServer(confirmedProfile.accessKey, serverId)
                        if (session.isSuccess) {
                            Log.i(AppConfig.TAG, "BootReceiver: Premium session acquired; starting VPN")
                            V2RayServiceManager.startVService(applicationContext)
                        } else {
                            Log.w(AppConfig.TAG, "BootReceiver: Premium session acquire failed; VPN remains stopped")
                        }
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

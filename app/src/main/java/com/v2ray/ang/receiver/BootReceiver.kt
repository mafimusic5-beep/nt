package com.v2ray.ang.receiver

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Handler
import android.os.Looper
import android.util.Log
import com.v2ray.ang.AppConfig
import com.v2ray.ang.handler.MmkvManager
import com.v2ray.ang.handler.V2RayServiceManager

class BootReceiver : BroadcastReceiver() {

    private companion object {
        val SUPPORTED_BOOT_ACTIONS = setOf(
            Intent.ACTION_BOOT_COMPLETED,
            Intent.ACTION_LOCKED_BOOT_COMPLETED,
            Intent.ACTION_MY_PACKAGE_REPLACED,
            "android.intent.action.QUICKBOOT_POWERON",
            "com.htc.intent.action.QUICKBOOT_POWERON",
        )
        val RETRY_DELAYS_MS = longArrayOf(1_500L, 6_000L, 12_000L)
    }

    override fun onReceive(context: Context?, intent: Intent?) {
        val action = intent?.action.orEmpty()
        Log.i(AppConfig.TAG, "BootReceiver received: $action")

        if (context == null || action !in SUPPORTED_BOOT_ACTIONS) {
            Log.w(AppConfig.TAG, "BootReceiver: ignored action=$action")
            return
        }

        if (!MmkvManager.decodeStartOnBoot()) {
            Log.i(AppConfig.TAG, "BootReceiver: auto-start on boot is disabled")
            return
        }

        if (MmkvManager.getSelectServer().isNullOrEmpty()) {
            Log.w(AppConfig.TAG, "BootReceiver: no selected server to start")
            return
        }

        val appContext = context.applicationContext
        startSelectedServer(appContext, "immediate:$action")

        val handler = Handler(Looper.getMainLooper())
        RETRY_DELAYS_MS.forEach { delayMs ->
            handler.postDelayed({
                if (MmkvManager.decodeStartOnBoot() && !MmkvManager.getSelectServer().isNullOrEmpty()) {
                    startSelectedServer(appContext, "retry_${delayMs}ms:$action")
                }
            }, delayMs)
        }
    }

    private fun startSelectedServer(context: Context, reason: String) {
        try {
            Log.i(AppConfig.TAG, "BootReceiver: starting selected VPN server ($reason)")
            V2RayServiceManager.startVService(context)
        } catch (error: Exception) {
            Log.e(AppConfig.TAG, "BootReceiver: start failed ($reason)", error)
        }
    }
}

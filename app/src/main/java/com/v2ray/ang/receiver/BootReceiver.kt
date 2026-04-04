package com.v2ray.ang.receiver

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log
import com.v2ray.ang.AppConfig
import com.v2ray.ang.handler.MmkvManager
import com.v2ray.ang.handler.VpnReconnectManager

class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context?, intent: Intent?) {
        val action = intent?.action
        Log.i(AppConfig.TAG, "BootReceiver received: $action")

        if (context == null || action.isNullOrBlank()) {
            Log.w(AppConfig.TAG, "BootReceiver: Invalid context or action")
            return
        }

        when (action) {
            Intent.ACTION_BOOT_COMPLETED,
            Intent.ACTION_MY_PACKAGE_REPLACED,
            AppConfig.BROADCAST_ACTION_RECONNECT -> {
                if (MmkvManager.getSelectServer().isNullOrEmpty()) {
                    Log.w(AppConfig.TAG, "BootReceiver: No server selected")
                    return
                }
                if (!VpnReconnectManager.shouldKeepVpnAlive()) {
                    Log.i(AppConfig.TAG, "BootReceiver: Auto reconnect not requested")
                    return
                }
                VpnReconnectManager.handleReconnectTrigger(context, action)
            }

            else -> Log.i(AppConfig.TAG, "BootReceiver: Ignored action $action")
        }
    }
}

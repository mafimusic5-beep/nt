package com.v2ray.ang.receiver

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.os.Build
import android.util.Log
import com.v2ray.ang.AppConfig
import com.v2ray.ang.handler.MmkvManager
import com.v2ray.ang.handler.V2RayServiceManager

class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context?, intent: Intent?) {
        val action = intent?.action.orEmpty()
        Log.i(AppConfig.TAG, "BootReceiver received: $action")

        if (context == null || !isAutoStartAction(action)) {
            Log.w(AppConfig.TAG, "BootReceiver: Invalid context or unsupported action")
            return
        }

        if (!MmkvManager.decodeStartOnBoot()) {
            Log.i(AppConfig.TAG, "BootReceiver: Auto-connect is disabled")
            return
        }

        if (MmkvManager.getSelectServer().isNullOrEmpty()) {
            Log.w(AppConfig.TAG, "BootReceiver: No server selected")
            return
        }

        if (V2RayServiceManager.isRunning()) {
            Log.i(AppConfig.TAG, "BootReceiver: VPN already running")
            return
        }

        if (!hasNetworkConnection(context)) {
            Log.i(AppConfig.TAG, "BootReceiver: No network, keeping auto-connect enabled")
            return
        }

        Log.i(AppConfig.TAG, "BootReceiver: Starting V2Ray service by $action")
        V2RayServiceManager.startVService(context)
    }

    private fun isAutoStartAction(action: String): Boolean {
        return action == Intent.ACTION_BOOT_COMPLETED ||
            action == Intent.ACTION_LOCKED_BOOT_COMPLETED ||
            action == ConnectivityManager.CONNECTIVITY_ACTION
    }

    private fun hasNetworkConnection(context: Context): Boolean {
        val connectivityManager = context.getSystemService(Context.CONNECTIVITY_SERVICE) as? ConnectivityManager
            ?: return true

        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            val network = connectivityManager.activeNetwork ?: return false
            val capabilities = connectivityManager.getNetworkCapabilities(network) ?: return false
            capabilities.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
        } else {
            @Suppress("DEPRECATION")
            connectivityManager.activeNetworkInfo?.isConnected == true
        }
    }
}

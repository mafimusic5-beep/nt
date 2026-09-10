package com.v2ray.ang.ui.premium.vpn

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import androidx.core.content.ContextCompat
import com.v2ray.ang.AppConfig
import com.v2ray.ang.handler.V2RayServiceManager
import com.v2ray.ang.util.Utils
import org.json.JSONObject

object VpnServiceDiagnosticsObserver {

    @Volatile
    private var started = false
    private var receiver: BroadcastReceiver? = null

    @Synchronized
    fun start(context: Context) {
        if (started) return

        val appContext = context.applicationContext
        val diagnosticsReceiver = object : BroadcastReceiver() {
            override fun onReceive(context: Context?, intent: Intent?) {
                val key = intent?.getIntExtra("key", 0) ?: 0
                VpnUiDebugLogger.log(
                    hypothesisId = "H-SERVICE",
                    location = "VpnServiceDiagnosticsObserver.kt:onReceive",
                    message = "service broadcast",
                    runId = "vpn-lifecycle",
                    data = JSONObject()
                        .put("key", key)
                        .put("event", eventName(key))
                        .put("runtimeRunning", runCatching { V2RayServiceManager.isRunning() }.getOrDefault(false)),
                )
            }
        }

        runCatching {
            ContextCompat.registerReceiver(
                appContext,
                diagnosticsReceiver,
                IntentFilter(AppConfig.BROADCAST_ACTION_ACTIVITY),
                Utils.receiverFlags(),
            )
        }.onSuccess {
            receiver = diagnosticsReceiver
            started = true
            VpnUiDebugLogger.log(
                hypothesisId = "H-SERVICE",
                location = "VpnServiceDiagnosticsObserver.kt:start",
                message = "service diagnostics observer started",
                runId = "vpn-lifecycle",
                data = JSONObject(),
            )
        }.onFailure { error ->
            VpnUiDebugLogger.log(
                hypothesisId = "H-SERVICE",
                location = "VpnServiceDiagnosticsObserver.kt:start",
                message = "premium service-state receiver registration failed",
                runId = "vpn-lifecycle",
                data = JSONObject().put("error", error.message ?: "unknown"),
            )
        }
    }

    private fun eventName(key: Int): String {
        return when (key) {
            AppConfig.MSG_STATE_RUNNING -> "STATE_RUNNING"
            AppConfig.MSG_STATE_NOT_RUNNING -> "STATE_NOT_RUNNING"
            AppConfig.MSG_STATE_START_SUCCESS -> "START_SUCCESS"
            AppConfig.MSG_STATE_START_FAILURE -> "START_FAILURE"
            AppConfig.MSG_STATE_STOP_SUCCESS -> "STOP_SUCCESS"
            else -> "KEY_$key"
        }
    }
}

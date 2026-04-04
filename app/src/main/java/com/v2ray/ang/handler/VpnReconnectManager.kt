package com.v2ray.ang.handler

import android.app.AlarmManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.os.SystemClock
import android.util.Log
import com.v2ray.ang.AppConfig
import com.v2ray.ang.receiver.BootReceiver

object VpnReconnectManager {
    private const val RECONNECT_REQUEST_CODE = 4107
    private const val RECONNECT_DELAY_MS = 4_000L

    fun markVpnDesired(enabled: Boolean) {
        MmkvManager.encodeAutoStartVpnEnabled(enabled)
        if (enabled && !MmkvManager.decodeAutoReconnectEnabled()) {
            MmkvManager.encodeAutoReconnectEnabled(true)
        }
    }

    fun shouldKeepVpnAlive(): Boolean {
        return MmkvManager.decodeAutoStartVpnEnabled() &&
            MmkvManager.decodeAutoReconnectEnabled(true)
    }

    fun scheduleReconnect(context: Context, reason: String, delayMs: Long = RECONNECT_DELAY_MS) {
        if (!shouldKeepVpnAlive()) {
            Log.i(AppConfig.TAG, "Reconnect skipped: desired flag disabled ($reason)")
            return
        }
        if (MmkvManager.getSelectServer().isNullOrEmpty()) {
            Log.w(AppConfig.TAG, "Reconnect skipped: selected server missing ($reason)")
            return
        }

        val alarmManager = context.getSystemService(Context.ALARM_SERVICE) as? AlarmManager
        if (alarmManager == null) {
            Log.w(AppConfig.TAG, "Reconnect fallback: alarm manager unavailable ($reason)")
            handleReconnectTrigger(context, reason)
            return
        }

        val pendingIntent = reconnectPendingIntent(context)
        alarmManager.cancel(pendingIntent)
        alarmManager.setAndAllowWhileIdle(
            AlarmManager.ELAPSED_REALTIME_WAKEUP,
            SystemClock.elapsedRealtime() + delayMs,
            pendingIntent,
        )
        Log.i(AppConfig.TAG, "Reconnect scheduled in ${delayMs}ms ($reason)")
    }

    fun cancelScheduledReconnect(context: Context) {
        val alarmManager = context.getSystemService(Context.ALARM_SERVICE) as? AlarmManager ?: return
        alarmManager.cancel(reconnectPendingIntent(context))
    }

    fun handleReconnectTrigger(context: Context, reason: String) {
        if (!shouldKeepVpnAlive()) {
            Log.i(AppConfig.TAG, "Reconnect trigger ignored: desired flag disabled ($reason)")
            return
        }
        if (MmkvManager.getSelectServer().isNullOrEmpty()) {
            Log.w(AppConfig.TAG, "Reconnect trigger ignored: selected server missing ($reason)")
            return
        }
        cancelScheduledReconnect(context)
        if (V2RayServiceManager.isRunning()) {
            Log.i(AppConfig.TAG, "Reconnect trigger ignored: service already running ($reason)")
            return
        }
        Log.i(AppConfig.TAG, "Reconnect trigger: starting VPN ($reason)")
        V2RayServiceManager.startVService(context)
    }

    private fun reconnectPendingIntent(context: Context): PendingIntent {
        val intent = Intent(context, BootReceiver::class.java).apply {
            action = AppConfig.BROADCAST_ACTION_RECONNECT
            `package` = context.packageName
        }
        return PendingIntent.getBroadcast(
            context,
            RECONNECT_REQUEST_CODE,
            intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
    }
}

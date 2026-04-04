$root = Resolve-Path (Join-Path $PSScriptRoot '..')
$appConfig = Join-Path $root 'app/src/main/java/com/v2ray/ang/AppConfig.kt'
$mmkv = Join-Path $root 'app/src/main/java/com/v2ray/ang/handler/MmkvManager.kt'
$serviceManager = Join-Path $root 'app/src/main/java/com/v2ray/ang/handler/V2RayServiceManager.kt'
$bootReceiver = Join-Path $root 'app/src/main/java/com/v2ray/ang/receiver/BootReceiver.kt'
$manifest = Join-Path $root 'app/src/main/AndroidManifest.xml'

$app = Get-Content $appConfig -Raw -Encoding UTF8
if ($app -notmatch 'BROADCAST_ACTION_RECONNECT') {
  $app = $app.Replace(
    '    const val BROADCAST_ACTION_WIDGET_CLICK = "$ANG_PACKAGE.action.widget.click"',
    "    const val BROADCAST_ACTION_WIDGET_CLICK = `"`$ANG_PACKAGE.action.widget.click`"`r`n    const val BROADCAST_ACTION_RECONNECT = `"`$ANG_PACKAGE.action.reconnect_vpn`""
  )
  Set-Content $appConfig $app -Encoding UTF8
}

$mm = Get-Content $mmkv -Raw -Encoding UTF8
if ($mm -notmatch 'fun encodeAutoStartVpnEnabled') {
  $mm = $mm.Replace(@'
    fun decodeStartOnBoot(): Boolean {
        return decodeSettingsBool(PREF_IS_BOOTED, false)
    }
'@, @'
    fun decodeStartOnBoot(): Boolean {
        return decodeSettingsBool(PREF_IS_BOOTED, false)
    }

    fun encodeAutoStartVpnEnabled(enabled: Boolean) {
        encodeSettings(PREF_AUTO_START_VPN, enabled)
    }

    fun decodeAutoStartVpnEnabled(): Boolean {
        return decodeSettingsBool(PREF_AUTO_START_VPN, false)
    }

    fun encodeAutoReconnectEnabled(enabled: Boolean) {
        encodeSettings(PREF_AUTO_RECONNECT, enabled)
    }

    fun decodeAutoReconnectEnabled(defaultValue: Boolean = true): Boolean {
        return decodeSettingsBool(PREF_AUTO_RECONNECT, defaultValue)
    }
'@)
  Set-Content $mmkv $mm -Encoding UTF8
}

@'
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
'@ | Set-Content $bootReceiver -Encoding UTF8

$man = Get-Content $manifest -Raw -Encoding UTF8
if ($man -notmatch 'android.intent.action.MY_PACKAGE_REPLACED') {
  $man = $man.Replace(
    '<action android:name="android.intent.action.BOOT_COMPLETED" />',
    "<action android:name=`"android.intent.action.BOOT_COMPLETED`" />`r`n                <action android:name=`"android.intent.action.MY_PACKAGE_REPLACED`" />"
  )
  Set-Content $manifest $man -Encoding UTF8
}

$svc = Get-Content $serviceManager -Raw -Encoding UTF8
if ($svc -notmatch 'import com.v2ray.ang.handler.VpnReconnectManager') {
  $svc = $svc.Replace(
    'import com.v2ray.ang.handler.V2RayServiceManager',
    "import com.v2ray.ang.handler.V2RayServiceManager`r`nimport com.v2ray.ang.handler.VpnReconnectManager"
  )
}
if ($svc -notmatch 'markVpnDesired\(false\)') {
  $svc = $svc.Replace(@'
    fun stopVService(context: Context) {
        //context.toast(R.string.toast_services_stop)
        updateVpnState(VpnRuntimeState.DISCONNECTING, "stop_requested_by_ui")
        MessageUtil.sendMsg2Service(context, AppConfig.MSG_STATE_STOP, "")
    }
'@, @'
    fun stopVService(context: Context) {
        //context.toast(R.string.toast_services_stop)
        VpnReconnectManager.markVpnDesired(false)
        VpnReconnectManager.cancelScheduledReconnect(context)
        updateVpnState(VpnRuntimeState.DISCONNECTING, "stop_requested_by_ui")
        MessageUtil.sendMsg2Service(context, AppConfig.MSG_STATE_STOP, "")
    }
'@)
}
if ($svc -notmatch 'start_foreground_service_requested[\s\S]*markVpnDesired\(true\)') {
  $svc = $svc.Replace(@'
            updateVpnState(VpnRuntimeState.CONNECTING, "start_foreground_service_requested")
            ContextCompat.startForegroundService(context, intent)
'@, @'
            updateVpnState(VpnRuntimeState.CONNECTING, "start_foreground_service_requested")
            VpnReconnectManager.markVpnDesired(true)
            VpnReconnectManager.cancelScheduledReconnect(context)
            ContextCompat.startForegroundService(context, intent)
'@)
}
if ($svc -notmatch 'scheduleReconnect\(service, "stop_core_loop_success"\)') {
  $svc = $svc.Replace(@'
        MessageUtil.sendMsg2UI(service, AppConfig.MSG_STATE_STOP_SUCCESS, "")
        NotificationManager.cancelNotification()
        updateVpnState(VpnRuntimeState.DISCONNECTED, "stop_core_loop_success")
'@, @'
        MessageUtil.sendMsg2UI(service, AppConfig.MSG_STATE_STOP_SUCCESS, "")
        NotificationManager.cancelNotification()
        updateVpnState(VpnRuntimeState.DISCONNECTED, "stop_core_loop_success")
        if (VpnReconnectManager.shouldKeepVpnAlive()) {
            VpnReconnectManager.scheduleReconnect(service, "stop_core_loop_success")
        }
'@)
}
Set-Content $serviceManager $svc -Encoding UTF8

Write-Host 'patched VPN auto reconnect support'

package com.v2ray.ang.handler

import android.app.Service
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.os.ParcelFileDescriptor
import android.util.Log
import androidx.core.content.ContextCompat
import com.v2ray.ang.AppConfig
import com.v2ray.ang.R
import com.v2ray.ang.contracts.ServiceControl
import com.v2ray.ang.dto.ProfileItem
import com.v2ray.ang.enums.EConfigType
import com.v2ray.ang.extension.toast
import com.v2ray.ang.service.V2RayProxyOnlyService
import com.v2ray.ang.service.V2RayVpnService
import com.v2ray.ang.util.AgentDebugNdjsonLogger
import com.v2ray.ang.util.MessageUtil
import com.v2ray.ang.util.Utils
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import libv2ray.CoreCallbackHandler
import libv2ray.CoreController
import org.json.JSONObject
import java.lang.ref.SoftReference
import java.util.concurrent.atomic.AtomicBoolean

object V2RayServiceManager {
    enum class VpnRuntimeState {
        DISCONNECTED,
        CONNECTING,
        CONNECTED,
        DISCONNECTING,
        ERROR,
    }

    private val expectedCoreShutdown = AtomicBoolean(false)
    private val coreController: CoreController = V2RayNativeManager.newCoreController(CoreCallback())
    private val mMsgReceive = ReceiveMessageHandler()
    private var currentConfig: ProfileItem? = null

    @Volatile
    private var serviceReceiverRegistered = false

    private val _vpnState = MutableStateFlow(if (coreController.isRunning) VpnRuntimeState.CONNECTED else VpnRuntimeState.DISCONNECTED)
    val vpnState: StateFlow<VpnRuntimeState> = _vpnState.asStateFlow()

    var serviceControl: SoftReference<ServiceControl>? = null
        set(value) {
            field = value
            V2RayNativeManager.initCoreEnv(value?.get()?.getService())
            syncVpnStateWithCore("service_control_set")
        }

    private fun updateVpnState(newState: VpnRuntimeState, reason: String) {
        if (_vpnState.value == newState) return
        _vpnState.value = newState
        ManualModeDebugLogger.log(
            hypothesisId = "H1",
            location = "V2RayServiceManager.kt:updateVpnState",
            message = "vpn_state_transition",
            data = JSONObject()
                .put("state", newState.name)
                .put("reason", reason)
                .put("coreRunning", coreController.isRunning),
        )
    }

    fun syncVpnStateWithCore(reason: String = "sync_with_core") {
        if (coreController.isRunning) {
            updateVpnState(VpnRuntimeState.CONNECTED, reason)
        } else {
            updateVpnState(VpnRuntimeState.DISCONNECTED, reason)
        }
    }

    fun startVServiceFromToggle(context: Context): Boolean {
        AgentDebugNdjsonLogger.log(
            hypothesisId = "H3",
            location = "V2RayServiceManager.kt:startVServiceFromToggle",
            message = "start_toggle_called",
            runId = "service-lifecycle",
            data = JSONObject()
                .put("coreRunning", coreController.isRunning)
                .put("hasSelectedServer", !MmkvManager.getSelectServer().isNullOrEmpty()),
        )

        if (MmkvManager.getSelectServer().isNullOrEmpty()) {
            updateVpnState(VpnRuntimeState.ERROR, "start_rejected_no_selected_server")
            ManualModeDiagnostics.reportError(
                code = ManualDiagnosticCodes.SELECTED_SERVER_MISSING_AFTER_IMPORT,
                message = "No selected server to start",
                source = "V2RayServiceManager",
                details = "startVServiceFromToggle",
            )
            context.toast(R.string.app_tile_first_use)
            return false
        }

        val started = startContextService(context)
        if (!started) {
            updateVpnState(VpnRuntimeState.ERROR, "start_context_service_failed")
        }
        return started
    }

    fun startVService(context: Context, guid: String? = null): Boolean {
        Log.i(AppConfig.TAG, "StartCore-Manager: startVService from ${context::class.java.simpleName}")
        AgentDebugNdjsonLogger.log(
            hypothesisId = "H4",
            location = "V2RayServiceManager.kt:startVService",
            message = "start_vservice_called",
            runId = "service-lifecycle",
            data = JSONObject()
                .put("coreRunning", coreController.isRunning)
                .put("guidProvided", guid != null)
                .put("selectedBefore", MmkvManager.getSelectServer().orEmpty()),
        )

        if (guid != null) {
            MmkvManager.setSelectServer(guid)
        }

        return startContextService(context)
    }

    fun stopVService(context: Context) {
        updateVpnState(VpnRuntimeState.DISCONNECTING, "stop_requested_by_ui")
        expectedCoreShutdown.set(true)
        MessageUtil.sendMsg2Service(context, AppConfig.MSG_STATE_STOP, "")
    }

    fun isRunning() = coreController.isRunning

    fun getRunningServerName() = currentConfig?.remarks.orEmpty()

    private fun startContextService(context: Context): Boolean {
        if (coreController.isRunning) {
            Log.w(AppConfig.TAG, "StartCore-Manager: Core already running")
            updateVpnState(VpnRuntimeState.CONNECTED, "start_context_already_running")
            return true
        }

        val guid = MmkvManager.getSelectServer()
        if (guid == null) {
            Log.e(AppConfig.TAG, "StartCore-Manager: No server selected")
            ManualModeDiagnostics.reportError(
                code = ManualDiagnosticCodes.SELECTED_SERVER_MISSING_AFTER_IMPORT,
                message = "No selected server to start",
                source = "V2RayServiceManager",
                details = "startContextService",
            )
            return false
        }

        val config = MmkvManager.decodeServerConfig(guid)
        if (config == null) {
            Log.e(AppConfig.TAG, "StartCore-Manager: Failed to decode server config for $guid")
            ManualModeDiagnostics.reportError(
                code = ManualDiagnosticCodes.SERVER_CONFIG_DECODE_FAILED,
                message = "Failed to decode selected server config",
                source = "V2RayServiceManager",
                details = "guid=$guid",
            )
            return false
        }

        if (config.configType != EConfigType.CUSTOM
            && config.configType != EConfigType.POLICYGROUP
            && !Utils.isValidUrl(config.server)
            && !Utils.isPureIpAddress(config.server.orEmpty())
        ) {
            Log.e(AppConfig.TAG, "StartCore-Manager: Invalid server host/ip: ${config.server}")
            ManualModeDiagnostics.reportError(
                code = ManualDiagnosticCodes.SERVER_CONFIG_DECODE_FAILED,
                message = "Invalid selected server address",
                source = "V2RayServiceManager",
                details = "guid=$guid; server=${config.server.orEmpty()}",
            )
            return false
        }

        if (MmkvManager.decodeSettingsBool(AppConfig.PREF_PROXY_SHARING)) {
            context.toast(R.string.toast_warning_pref_proxysharing_short)
        } else {
            context.toast(R.string.toast_services_start)
        }

        val isVpnMode = SettingsManager.isVpnMode()
        val intent = if (isVpnMode) {
            Log.i(AppConfig.TAG, "StartCore-Manager: Starting VPN service")
            Intent(context.applicationContext, V2RayVpnService::class.java)
        } else {
            Log.i(AppConfig.TAG, "StartCore-Manager: Starting Proxy service")
            Intent(context.applicationContext, V2RayProxyOnlyService::class.java)
        }

        return try {
            updateVpnState(VpnRuntimeState.CONNECTING, "start_foreground_service_requested")
            ContextCompat.startForegroundService(context, intent)
            ManualModeDiagnostics.recordSuccessStep("VPN foreground service start requested")
            ManualModeDebugLogger.log(
                hypothesisId = "H4",
                location = "V2RayServiceManager.kt:startContextService",
                message = "start_foreground_service_called",
                data = JSONObject()
                    .put("guid", guid)
                    .put("isVpnMode", isVpnMode),
            )
            true
        } catch (e: Exception) {
            Log.e(AppConfig.TAG, "StartCore-Manager: Failed to start service", e)
            ManualModeDiagnostics.reportError(
                code = ManualDiagnosticCodes.VPN_SERVICE_START_FAILED,
                message = "Failed to start VPN foreground service",
                source = "V2RayServiceManager",
                details = e.message.orEmpty(),
            )
            updateVpnState(VpnRuntimeState.ERROR, "start_foreground_service_exception")
            false
        }
    }

    fun startCoreLoop(vpnInterface: ParcelFileDescriptor?): Boolean {
        if (coreController.isRunning) {
            Log.w(AppConfig.TAG, "StartCore-Manager: Core already running")
            updateVpnState(VpnRuntimeState.CONNECTED, "start_core_loop_already_running")
            return false
        }

        val service = getService()
        if (service == null) {
            Log.e(AppConfig.TAG, "StartCore-Manager: Service is null")
            updateVpnState(VpnRuntimeState.ERROR, "start_core_loop_service_null")
            return false
        }

        val guid = MmkvManager.getSelectServer()
        if (guid == null) {
            Log.e(AppConfig.TAG, "StartCore-Manager: No server selected")
            updateVpnState(VpnRuntimeState.ERROR, "start_core_loop_no_selected_server")
            return false
        }

        val config = MmkvManager.decodeServerConfig(guid)
        if (config == null) {
            Log.e(AppConfig.TAG, "StartCore-Manager: Failed to decode server config")
            updateVpnState(VpnRuntimeState.ERROR, "start_core_loop_decode_selected_server_failed")
            return false
        }

        Log.i(AppConfig.TAG, "StartCore-Manager: Starting core loop for ${config.remarks}")
        val result = V2rayConfigManager.getV2rayConfig(service, guid)
        if (!result.status) {
            Log.e(AppConfig.TAG, "StartCore-Manager: Failed to get V2Ray config")
            updateVpnState(VpnRuntimeState.ERROR, "start_core_loop_build_runtime_config_failed")
            ManualModeDiagnostics.reportError(
                code = ManualDiagnosticCodes.SERVER_CONFIG_DECODE_FAILED,
                message = "Failed to build runtime server config",
                source = "V2RayServiceManager",
                details = "guid=$guid",
            )
            return false
        }

        if (!serviceReceiverRegistered) {
            try {
                val filter = IntentFilter(AppConfig.BROADCAST_ACTION_SERVICE)
                filter.addAction(Intent.ACTION_SCREEN_ON)
                filter.addAction(Intent.ACTION_SCREEN_OFF)
                filter.addAction(Intent.ACTION_USER_PRESENT)
                ContextCompat.registerReceiver(service, mMsgReceive, filter, Utils.receiverFlags())
                serviceReceiverRegistered = true
            } catch (e: Exception) {
                Log.e(AppConfig.TAG, "StartCore-Manager: Failed to register receiver", e)
                serviceReceiverRegistered = false
                updateVpnState(VpnRuntimeState.ERROR, "start_core_loop_register_receiver_failed")
                return false
            }
        }

        currentConfig = config
        var tunFd = vpnInterface?.fd ?: 0
        if (SettingsManager.isUsingHevTun()) {
            tunFd = 0
        }

        expectedCoreShutdown.set(false)
        try {
            NotificationManager.showNotification(currentConfig)
            coreController.startLoop(result.content, tunFd)
        } catch (e: Exception) {
            Log.e(AppConfig.TAG, "StartCore-Manager: Failed to start core loop", e)
            updateVpnState(VpnRuntimeState.ERROR, "start_core_loop_exception")
            ManualModeDiagnostics.reportError(
                code = ManualDiagnosticCodes.CORE_START_FAILED,
                message = "Exception while starting core loop",
                source = "V2RayServiceManager",
                details = e.message.orEmpty(),
            )
            return false
        }

        if (!coreController.isRunning) {
            Log.e(AppConfig.TAG, "StartCore-Manager: Core failed to start")
            MessageUtil.sendMsg2UI(service, AppConfig.MSG_STATE_START_FAILURE, "")
            NotificationManager.cancelNotification()
            updateVpnState(VpnRuntimeState.ERROR, "start_core_loop_not_running_after_start")
            ManualModeDiagnostics.reportError(
                code = ManualDiagnosticCodes.CORE_START_FAILED,
                message = "Core loop did not enter running state",
                source = "V2RayServiceManager",
                details = "guid=$guid",
            )
            return false
        }

        return try {
            MessageUtil.sendMsg2UI(service, AppConfig.MSG_STATE_START_SUCCESS, "")
            NotificationManager.startSpeedNotification(currentConfig)
            Log.i(AppConfig.TAG, "StartCore-Manager: Core started successfully")
            updateVpnState(VpnRuntimeState.CONNECTED, "start_core_loop_success")
            ManualModeDiagnostics.clearError()
            ManualModeDiagnostics.recordSuccessStep("Core loop started")
            ManualModeDebugLogger.log(
                hypothesisId = "H4",
                location = "V2RayServiceManager.kt:startCoreLoop",
                message = "core_start_success",
                data = JSONObject().put("guid", guid),
            )
            true
        } catch (e: Exception) {
            Log.e(AppConfig.TAG, "StartCore-Manager: Failed to complete startup", e)
            updateVpnState(VpnRuntimeState.ERROR, "start_core_loop_post_start_exception")
            ManualModeDiagnostics.reportError(
                code = ManualDiagnosticCodes.CORE_START_FAILED,
                message = "Core startup post-processing failed",
                source = "V2RayServiceManager",
                details = e.message.orEmpty(),
            )
            false
        }
    }

    fun stopCoreLoop(): Boolean {
        val service = getService() ?: return false

        updateVpnState(VpnRuntimeState.DISCONNECTING, "stop_core_loop_enter")
        expectedCoreShutdown.set(true)
        if (coreController.isRunning) {
            CoroutineScope(Dispatchers.IO).launch {
                try {
                    coreController.stopLoop()
                } catch (e: Exception) {
                    Log.e(AppConfig.TAG, "StartCore-Manager: Failed to stop V2Ray loop", e)
                    expectedCoreShutdown.set(false)
                }
            }
        } else {
            expectedCoreShutdown.set(false)
        }

        MessageUtil.sendMsg2UI(service, AppConfig.MSG_STATE_STOP_SUCCESS, "")
        NotificationManager.cancelNotification()
        updateVpnState(VpnRuntimeState.DISCONNECTED, "stop_core_loop_success")

        if (serviceReceiverRegistered) {
            try {
                service.unregisterReceiver(mMsgReceive)
                serviceReceiverRegistered = false
            } catch (e: Exception) {
                Log.e(AppConfig.TAG, "StartCore-Manager: Failed to unregister receiver", e)
                ManualModeDiagnostics.reportError(
                    code = ManualDiagnosticCodes.RECEIVER_CLEANUP_ERROR,
                    message = "Failed to unregister service receiver",
                    source = "V2RayServiceManager",
                    details = e.message.orEmpty(),
                )
            }
        }

        return true
    }

    fun queryStats(tag: String, link: String): Long {
        return coreController.queryStats(tag, link)
    }

    private fun measureV2rayDelay() {
        if (!coreController.isRunning) {
            return
        }

        CoroutineScope(Dispatchers.IO).launch {
            val service = getService() ?: return@launch
            var time = -1L
            var errorStr = ""

            try {
                time = coreController.measureDelay(SettingsManager.getDelayTestUrl())
            } catch (e: Exception) {
                Log.e(AppConfig.TAG, "StartCore-Manager: Failed to measure delay", e)
                errorStr = e.message?.substringAfter("\":") ?: "empty message"
            }
            if (time == -1L) {
                try {
                    time = coreController.measureDelay(SettingsManager.getDelayTestUrl(true))
                } catch (e: Exception) {
                    Log.e(AppConfig.TAG, "StartCore-Manager: Failed to measure delay", e)
                    errorStr = e.message?.substringAfter("\":") ?: "empty message"
                }
            }

            val result = if (time >= 0) {
                service.getString(R.string.connection_test_available, time)
            } else {
                service.getString(R.string.connection_test_error, errorStr)
            }
            MessageUtil.sendMsg2UI(service, AppConfig.MSG_MEASURE_DELAY_SUCCESS, result)

            if (time >= 0) {
                SpeedtestManager.getRemoteIPInfo()?.let { ip ->
                    MessageUtil.sendMsg2UI(service, AppConfig.MSG_MEASURE_DELAY_SUCCESS, "$result\n$ip")
                }
            }
        }
    }

    private fun getService(): Service? {
        return serviceControl?.get()?.getService()
    }

    private class CoreCallback : CoreCallbackHandler {
        override fun startup(): Long {
            return 0
        }

        override fun shutdown(): Long {
            val service = getService()
            val expected = expectedCoreShutdown.getAndSet(false)
            val guid = MmkvManager.getSelectServer().orEmpty()

            if (expected) {
                Log.i(AppConfig.TAG, "StartCore-Manager: Core shutdown expected")
                updateVpnState(VpnRuntimeState.DISCONNECTED, "core_callback_shutdown_expected")
                ManualModeDebugLogger.log(
                    hypothesisId = "H14",
                    location = "V2RayServiceManager.kt:CoreCallback.shutdown",
                    message = "core_shutdown_expected",
                    data = JSONObject().put("guid", guid),
                )
                return 0
            }

            Log.w(AppConfig.TAG, "StartCore-Manager: Core shutdown unexpectedly; keeping service alive for watchdog restart")
            updateVpnState(VpnRuntimeState.ERROR, "core_callback_shutdown_unexpected")
            ManualModeDiagnostics.reportError(
                code = ManualDiagnosticCodes.CORE_START_FAILED,
                message = "VPN core stopped unexpectedly",
                source = "V2RayServiceManager",
                details = "guid=$guid",
            )
            ManualModeDebugLogger.log(
                hypothesisId = "H14",
                location = "V2RayServiceManager.kt:CoreCallback.shutdown",
                message = "core_shutdown_unexpected_keep_service_alive",
                data = JSONObject()
                    .put("guid", guid)
                    .put("hasService", service != null),
            )
            service?.let {
                MessageUtil.sendMsg2UI(it, AppConfig.MSG_STATE_NOT_RUNNING, "")
            }
            return 0
        }

        override fun onEmitStatus(l: Long, s: String?): Long {
            val status = s.orEmpty()
            if (status.isNotBlank()) {
                ManualModeDebugLogger.log(
                    hypothesisId = "H14",
                    location = "V2RayServiceManager.kt:CoreCallback.onEmitStatus",
                    message = "core_status",
                    data = JSONObject()
                        .put("code", l)
                        .put("status", status.take(500)),
                )
            }
            return 0
        }
    }

    private class ReceiveMessageHandler : BroadcastReceiver() {
        override fun onReceive(ctx: Context?, intent: Intent?) {
            val serviceControl = serviceControl?.get() ?: return
            when (intent?.getIntExtra("key", 0)) {
                AppConfig.MSG_REGISTER_CLIENT -> {
                    if (coreController.isRunning) {
                        updateVpnState(VpnRuntimeState.CONNECTED, "register_client_core_running")
                        MessageUtil.sendMsg2UI(serviceControl.getService(), AppConfig.MSG_STATE_RUNNING, "")
                    } else {
                        updateVpnState(VpnRuntimeState.DISCONNECTED, "register_client_core_not_running")
                        MessageUtil.sendMsg2UI(serviceControl.getService(), AppConfig.MSG_STATE_NOT_RUNNING, "")
                    }
                }

                AppConfig.MSG_UNREGISTER_CLIENT -> {
                    // nothing to do
                }

                AppConfig.MSG_STATE_START -> {
                    updateVpnState(VpnRuntimeState.CONNECTING, "msg_state_start")
                }

                AppConfig.MSG_STATE_STOP -> {
                    Log.i(AppConfig.TAG, "StartCore-Manager: Stop service")
                    updateVpnState(VpnRuntimeState.DISCONNECTING, "msg_state_stop")
                    expectedCoreShutdown.set(true)
                    serviceControl.stopService()
                }

                AppConfig.MSG_STATE_RESTART -> {
                    Log.i(AppConfig.TAG, "StartCore-Manager: Restart service")
                    expectedCoreShutdown.set(true)
                    serviceControl.stopService()
                    Thread.sleep(500L)
                    startVService(serviceControl.getService())
                }

                AppConfig.MSG_MEASURE_DELAY -> {
                    measureV2rayDelay()
                }
            }

            when (intent?.action) {
                Intent.ACTION_SCREEN_OFF -> {
                    Log.i(AppConfig.TAG, "StartCore-Manager: Screen off")
                    NotificationManager.stopSpeedNotification(currentConfig)
                }

                Intent.ACTION_SCREEN_ON -> {
                    Log.i(AppConfig.TAG, "StartCore-Manager: Screen on")
                    NotificationManager.startSpeedNotification(currentConfig)
                }
            }
        }
    }
}

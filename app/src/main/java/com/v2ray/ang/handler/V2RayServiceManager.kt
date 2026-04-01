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

object V2RayServiceManager {
    enum class VpnRuntimeState {
        DISCONNECTED,
        CONNECTING,
        CONNECTED,
        DISCONNECTING,
        ERROR,
    }

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
        // #region agent log
        ManualModeDebugLogger.log(
            hypothesisId = "H1",
            location = "V2RayServiceManager.kt:updateVpnState",
            message = "vpn_state_transition",
            data = JSONObject()
                .put("state", newState.name)
                .put("reason", reason)
                .put("coreRunning", coreController.isRunning),
        )
        // #endregion
    }

    fun syncVpnStateWithCore(reason: String = "sync_with_core") {
        if (coreController.isRunning) {
            updateVpnState(VpnRuntimeState.CONNECTED, reason)
        } else {
            updateVpnState(VpnRuntimeState.DISCONNECTED, reason)
        }
    }

    /**
     * Starts the V2Ray service from a toggle action.
     * @param context The context from which the service is started.
     * @return True if the service was started successfully, false otherwise.
     */
    fun startVServiceFromToggle(context: Context): Boolean {
        // #region agent log
        AgentDebugNdjsonLogger.log(
            hypothesisId = "H3",
            location = "V2RayServiceManager.kt:startVServiceFromToggle",
            message = "start_toggle_called",
            runId = "pre-fix",
            data = JSONObject()
                .put("coreRunning", coreController.isRunning)
                .put("hasSelectedServer", !MmkvManager.getSelectServer().isNullOrEmpty()),
        )
        // #endregion
        if (MmkvManager.getSelectServer().isNullOrEmpty()) {
            updateVpnState(VpnRuntimeState.ERROR, "start_rejected_no_selected_server")
            ManualModeDiagnostics.reportError(
                code = ManualDiagnosticCodes.SELECTED_SERVER_MISSING_AFTER_IMPORT,
                message = "No selected server to start",
                source = "V2RayServiceManager",
                details = "startVServiceFromToggle",
            )
            // #region agent log
            ManualModeDebugLogger.log(
                hypothesisId = "H4",
                location = "V2RayServiceManager.kt:startVServiceFromToggle",
                message = "start_toggle_rejected_no_selected_server",
                data = JSONObject(),
            )
            // #endregion
            context.toast(R.string.app_tile_first_use)
            return false
        }
        val started = startContextService(context)
        if (!started) {
            updateVpnState(VpnRuntimeState.ERROR, "start_context_service_failed")
        }
        // #region agent log
        ManualModeDebugLogger.log(
            hypothesisId = "H4",
            location = "V2RayServiceManager.kt:startVServiceFromToggle",
            message = "start_toggle_exit",
            data = JSONObject().put("started", started),
        )
        // #endregion
        return started
    }

    /**
     * Starts the V2Ray service.
     * @param context The context from which the service is started.
     * @param guid The GUID of the server configuration to use (optional).
     */
    fun startVService(context: Context, guid: String? = null): Boolean {
        Log.i(AppConfig.TAG, "StartCore-Manager: startVService from ${context::class.java.simpleName}")
        // #region agent log
        AgentDebugNdjsonLogger.log(
            hypothesisId = "H4",
            location = "V2RayServiceManager.kt:startVService",
            message = "start_vservice_called",
            runId = "pre-fix",
            data = JSONObject()
                .put("coreRunning", coreController.isRunning)
                .put("guidProvided", guid != null)
                .put("selectedBefore", MmkvManager.getSelectServer().orEmpty()),
        )
        // #endregion

        if (guid != null) {
            MmkvManager.setSelectServer(guid)
        }

        return startContextService(context)
    }

    /**
     * Stops the V2Ray service.
     * @param context The context from which the service is stopped.
     */
    fun stopVService(context: Context) {
        //context.toast(R.string.toast_services_stop)
        updateVpnState(VpnRuntimeState.DISCONNECTING, "stop_requested_by_ui")
        MessageUtil.sendMsg2Service(context, AppConfig.MSG_STATE_STOP, "")
    }

    /**
     * Checks if the V2Ray service is running.
     * @return True if the service is running, false otherwise.
     */
    fun isRunning() = coreController.isRunning

    /**
     * Gets the name of the currently running server.
     * @return The name of the running server.
     */
    fun getRunningServerName() = currentConfig?.remarks.orEmpty()

    /**
     * Starts the context service for V2Ray.
     * Chooses between VPN service or Proxy-only service based on user settings.
     * @param context The context from which the service is started.
     */
    private fun startContextService(context: Context): Boolean {
        if (coreController.isRunning) {
            Log.w(AppConfig.TAG, "StartCore-Manager: Core already running")
            updateVpnState(VpnRuntimeState.CONNECTED, "start_context_already_running")
            // #region agent log
            ManualModeDebugLogger.log(
                hypothesisId = "H4",
                location = "V2RayServiceManager.kt:startContextService",
                message = "start_context_already_running",
                data = JSONObject(),
            )
            // #endregion
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
            // #region agent log
            ManualModeDebugLogger.log(
                hypothesisId = "H4",
                location = "V2RayServiceManager.kt:startContextService",
                message = "start_context_failed_no_selected_server",
                data = JSONObject(),
            )
            // #endregion
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
            // #region agent log
            ManualModeDebugLogger.log(
                hypothesisId = "H4",
                location = "V2RayServiceManager.kt:startContextService",
                message = "start_context_failed_decode_selected_server",
                data = JSONObject().put("guid", guid),
            )
            // #endregion
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
            // #region agent log
            ManualModeDebugLogger.log(
                hypothesisId = "H4",
                location = "V2RayServiceManager.kt:startContextService",
                message = "start_context_failed_invalid_server_host",
                data = JSONObject()
                    .put("guid", guid)
                    .put("serverHost", config.server ?: ""),
            )
            // #endregion
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
            // #region agent log
            ManualModeDebugLogger.log(
                hypothesisId = "H4",
                location = "V2RayServiceManager.kt:startContextService",
                message = "start_foreground_service_called",
                data = JSONObject()
                    .put("guid", guid)
                    .put("isVpnMode", isVpnMode),
            )
            // #endregion
            true
        } catch (e: Exception) {
            Log.e(AppConfig.TAG, "StartCore-Manager: Failed to start service", e)
            ManualModeDiagnostics.reportError(
                code = ManualDiagnosticCodes.VPN_SERVICE_START_FAILED,
                message = "Failed to start VPN foreground service",
                source = "V2RayServiceManager",
                details = e.message.orEmpty(),
            )
            // #region agent log
            ManualModeDebugLogger.log(
                hypothesisId = "H4",
                location = "V2RayServiceManager.kt:startContextService",
                message = "start_foreground_service_exception",
                data = JSONObject()
                    .put("guid", guid)
                    .put("error", e.message ?: ""),
            )
            // #endregion
            updateVpnState(VpnRuntimeState.ERROR, "start_foreground_service_exception")
            false
        }
    }

    /**
     * Refer to the official documentation for [registerReceiver](https://developer.android.com/reference/androidx/core/content/ContextCompat#registerReceiver(android.content.Context,android.content.BroadcastReceiver,android.content.IntentFilter,int):
     * `registerReceiver(Context, BroadcastReceiver, IntentFilter, int)`.
     * Starts the V2Ray core service.
     */
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
            // #region agent log
            ManualModeDebugLogger.log(
                hypothesisId = "H4",
                location = "V2RayServiceManager.kt:startCoreLoop",
                message = "core_start_failed_decode_server_config",
                data = JSONObject().put("guid", guid),
            )
            // #endregion
            return false
        }

        try {
            val mFilter = IntentFilter(AppConfig.BROADCAST_ACTION_SERVICE)
            mFilter.addAction(Intent.ACTION_SCREEN_ON)
            mFilter.addAction(Intent.ACTION_SCREEN_OFF)
            mFilter.addAction(Intent.ACTION_USER_PRESENT)
            ContextCompat.registerReceiver(service, mMsgReceive, mFilter, Utils.receiverFlags())
            serviceReceiverRegistered = true
        } catch (e: Exception) {
            Log.e(AppConfig.TAG, "StartCore-Manager: Failed to register receiver", e)
            serviceReceiverRegistered = false
            updateVpnState(VpnRuntimeState.ERROR, "start_core_loop_register_receiver_failed")
            return false
        }

        currentConfig = config
        var tunFd = vpnInterface?.fd ?: 0
        if (SettingsManager.isUsingHevTun()) {
            tunFd = 0
        }

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
            // #region agent log
            ManualModeDebugLogger.log(
                hypothesisId = "H4",
                location = "V2RayServiceManager.kt:startCoreLoop",
                message = "core_start_exception",
                data = JSONObject()
                    .put("guid", guid)
                    .put("error", e.message ?: ""),
            )
            // #endregion
            return false
        }

        if (coreController.isRunning == false) {
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
            // #region agent log
            ManualModeDebugLogger.log(
                hypothesisId = "H4",
                location = "V2RayServiceManager.kt:startCoreLoop",
                message = "core_start_returned_not_running",
                data = JSONObject().put("guid", guid),
            )
            // #endregion
            return false
        }

        try {
            MessageUtil.sendMsg2UI(service, AppConfig.MSG_STATE_START_SUCCESS, "")
            NotificationManager.startSpeedNotification(currentConfig)
            Log.i(AppConfig.TAG, "StartCore-Manager: Core started successfully")
            updateVpnState(VpnRuntimeState.CONNECTED, "start_core_loop_success")
            ManualModeDiagnostics.clearError()
            ManualModeDiagnostics.recordSuccessStep("Core loop started")
            // #region agent log
            ManualModeDebugLogger.log(
                hypothesisId = "H4",
                location = "V2RayServiceManager.kt:startCoreLoop",
                message = "core_start_success",
                data = JSONObject().put("guid", guid),
            )
            // #endregion
        } catch (e: Exception) {
            Log.e(AppConfig.TAG, "StartCore-Manager: Failed to complete startup", e)
            updateVpnState(VpnRuntimeState.ERROR, "start_core_loop_post_start_exception")
            ManualModeDiagnostics.reportError(
                code = ManualDiagnosticCodes.CORE_START_FAILED,
                message = "Core startup post-processing failed",
                source = "V2RayServiceManager",
                details = e.message.orEmpty(),
            )
            // #region agent log
            ManualModeDebugLogger.log(
                hypothesisId = "H4",
                location = "V2RayServiceManager.kt:startCoreLoop",
                message = "core_start_post_success_exception",
                data = JSONObject()
                    .put("guid", guid)
                    .put("error", e.message ?: ""),
            )
            // #endregion
            return false
        }
        return true
    }

    /**
     * Stops the V2Ray core service.
     * Unregisters broadcast receivers, stops notifications, and shuts down plugins.
     * @return True if the core was stopped successfully, false otherwise.
     */
    fun stopCoreLoop(): Boolean {
        val service = getService() ?: return false

        updateVpnState(VpnRuntimeState.DISCONNECTING, "stop_core_loop_enter")
        if (coreController.isRunning) {
            CoroutineScope(Dispatchers.IO).launch {
                try {
                    coreController.stopLoop()
                } catch (e: Exception) {
                    Log.e(AppConfig.TAG, "StartCore-Manager: Failed to stop V2Ray loop", e)
                }
            }
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

    /**
     * Queries the statistics for a given tag and link.
     * @param tag The tag to query.
     * @param link The link to query.
     * @return The statistics value.
     */
    fun queryStats(tag: String, link: String): Long {
        return coreController.queryStats(tag, link)
    }

    /**
     * Measures the connection delay for the current V2Ray configuration.
     * Tests with primary URL first, then falls back to alternative URL if needed.
     * Also fetches remote IP information if the delay test was successful.
     */
    private fun measureV2rayDelay() {
        if (coreController.isRunning == false) {
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

            // Only fetch IP info if the delay test was successful
            if (time >= 0) {
                SpeedtestManager.getRemoteIPInfo()?.let { ip ->
                    MessageUtil.sendMsg2UI(service, AppConfig.MSG_MEASURE_DELAY_SUCCESS, "$result\n$ip")
                }
            }
        }
    }

    /**
     * Gets the current service instance.
     * @return The current service instance, or null if not available.
     */
    private fun getService(): Service? {
        return serviceControl?.get()?.getService()
    }

    /**
     * Core callback handler implementation for handling V2Ray core events.
     * Handles startup, shutdown, socket protection, and status emission.
     */
    private class CoreCallback : CoreCallbackHandler {
        /**
         * Called when V2Ray core starts up.
         * @return 0 for success, any other value for failure.
         */
        override fun startup(): Long {
            return 0
        }

        /**
         * Called when V2Ray core shuts down.
         * @return 0 for success, any other value for failure.
         */
        override fun shutdown(): Long {
            val serviceControl = serviceControl?.get() ?: return -1
            return try {
                updateVpnState(VpnRuntimeState.DISCONNECTING, "core_callback_shutdown")
                serviceControl.stopService()
                updateVpnState(VpnRuntimeState.DISCONNECTED, "core_callback_shutdown_completed")
                0
            } catch (e: Exception) {
                Log.e(AppConfig.TAG, "StartCore-Manager: Failed to stop service", e)
                updateVpnState(VpnRuntimeState.ERROR, "core_callback_shutdown_exception")
                -1
            }
        }

        /**
         * Called when V2Ray core emits status information.
         * @param l Status code.
         * @param s Status message.
         * @return Always returns 0.
         */
        override fun onEmitStatus(l: Long, s: String?): Long {
            return 0
        }
    }

    /**
     * Broadcast receiver for handling messages sent to the service.
     * Handles registration, service control, and screen events.
     */
    private class ReceiveMessageHandler : BroadcastReceiver() {
        /**
         * Handles received broadcast messages.
         * Processes service control messages and screen state changes.
         * @param ctx The context in which the receiver is running.
         * @param intent The intent being received.
         */
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
                    serviceControl.stopService()
                }

                AppConfig.MSG_STATE_RESTART -> {
                    Log.i(AppConfig.TAG, "StartCore-Manager: Restart service")
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
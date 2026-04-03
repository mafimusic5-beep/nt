package com.v2ray.ang.ui.premium.vpn

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.v2ray.ang.AppConfig
import com.v2ray.ang.handler.EmeryAccessManager
import com.v2ray.ang.handler.MmkvManager
import com.v2ray.ang.handler.EmeryVpnSync
import com.v2ray.ang.util.AgentDebugNdjsonLogger
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import org.json.JSONObject

class VpnMainViewModel : ViewModel() {

    private val _uiState = MutableStateFlow(VpnMainUiState())
    val uiState: StateFlow<VpnMainUiState> = _uiState.asStateFlow()

    private var connectJob: Job? = null
    private var timerJob: Job? = null

    init {
        val storedKey = MmkvManager.decodeSettingsString(AppConfig.PREF_EMERY_ACCESS_KEY).orEmpty()
        if (storedKey.isNotBlank()) {
            _uiState.update { it.copy(activationKey = storedKey) }
        }
    }

    fun onActivationKeyChanged(value: String) {
        VpnUiDebugLogger.log(
            hypothesisId = "H4",
            location = "VpnMainViewModel.kt:onActivationKeyChanged",
            message = "activation key changed",
            data = JSONObject().put("length", value.length),
        )
        _uiState.update { state ->
            state.copy(activationKey = value, errorMessage = null)
        }
    }

    fun onLocationSelected(location: String) {
        val selected = VpnDemoData.locations.firstOrNull { it.title == location } ?: return
        VpnUiDebugLogger.log(
            hypothesisId = "H5",
            location = "VpnMainViewModel.kt:onLocationSelected",
            message = "location selected",
            data = JSONObject().put("location", selected.title),
        )
        _uiState.update { state ->
            state.copy(selectedLocation = selected)
        }
    }

    fun onConnectClick() {
        val currentState = _uiState.value
        AgentDebugNdjsonLogger.log(
            hypothesisId = "H1",
            location = "VpnMainViewModel.kt:onConnectClick",
            message = "premium_connect_clicked",
            runId = "post-fix",
            data = JSONObject()
                .put("state", currentState.connectionState.name)
                .put("activationKeyLen", currentState.activationKey.length)
                .put("activationKeyBlank", currentState.activationKey.isBlank()),
        )
        if (currentState.connectionState != VpnConnectionState.Disconnected) {
            VpnUiDebugLogger.log(
                hypothesisId = "H3",
                location = "VpnMainViewModel.kt:onConnectClick",
                message = "connect ignored due to state",
                data = JSONObject().put("state", currentState.connectionState.name),
            )
            return
        }
        if (currentState.activationKey.isBlank()) {
            VpnUiDebugLogger.log(
                hypothesisId = "H3",
                location = "VpnMainViewModel.kt:onConnectClick",
                message = "connect blocked by empty activation key",
                data = JSONObject(),
            )
            return
        }

        connectJob?.cancel()
        timerJob?.cancel()

        _uiState.update { state ->
            state.copy(
                connectionState = VpnConnectionState.Connecting,
                elapsedSeconds = 0L,
                errorMessage = null,
            )
        }
        VpnUiDebugLogger.log(
            hypothesisId = "H3",
            location = "VpnMainViewModel.kt:onConnectClick",
            message = "state moved to connecting, calling syncProfileAndVpnConfig",
            data = JSONObject(),
        )

        val accessKey = currentState.activationKey
        connectJob = viewModelScope.launch {
            val result = EmeryVpnSync.syncProfileAndVpnConfig(accessKey)
            result.fold(
                onSuccess = {
                    AgentDebugNdjsonLogger.log(
                        hypothesisId = "H2",
                        location = "VpnMainViewModel.kt:onConnectClick",
                        message = "premium_sync_success_setting_connected",
                        runId = "post-fix",
                        data = JSONObject(),
                    )
                    _uiState.update { state ->
                        state.copy(
                            connectionState = VpnConnectionState.Connected,
                            elapsedSeconds = 0L,
                            errorMessage = null,
                        )
                    }
                    startTimer()
                },
                onFailure = { err ->
                    AgentDebugNdjsonLogger.log(
                        hypothesisId = "H2",
                        location = "VpnMainViewModel.kt:onConnectClick",
                        message = "premium_sync_failed",
                        runId = "post-fix",
                        data = JSONObject().put("error", err.message ?: "unknown"),
                    )
                    _uiState.update { state ->
                        state.copy(
                            connectionState = VpnConnectionState.Disconnected,
                            elapsedSeconds = 0L,
                            errorMessage = err.message ?: "Connection failed",
                        )
                    }
                },
            )
        }
    }

    fun onDisconnectClick() {
        connectJob?.cancel()
        timerJob?.cancel()
        AgentDebugNdjsonLogger.log(
            hypothesisId = "H2",
            location = "VpnMainViewModel.kt:onDisconnectClick",
            message = "premium_disconnect_clicked",
            runId = "post-fix",
            data = JSONObject().put("prevState", _uiState.value.connectionState.name),
        )
        _uiState.update { state ->
            state.copy(
                connectionState = VpnConnectionState.Disconnected,
                elapsedSeconds = 0L,
                errorMessage = null,
            )
        }
        VpnUiDebugLogger.log(
            hypothesisId = "H3",
            location = "VpnMainViewModel.kt:onDisconnectClick",
            message = "state moved to disconnected",
            data = JSONObject(),
        )
    }

    private fun startTimer() {
        timerJob?.cancel()
        timerJob = viewModelScope.launch {
            while (isActive) {
                delay(1000L)
                _uiState.update { state ->
                    if (state.connectionState == VpnConnectionState.Connected) {
                        state.copy(elapsedSeconds = state.elapsedSeconds + 1L)
                    } else {
                        state
                    }
                }
            }
        }
    }

    override fun onCleared() {
        connectJob?.cancel()
        timerJob?.cancel()
        super.onCleared()
    }
}

package com.v2ray.ang.ui.premium.vpn

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
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

    fun onActivationKeyChanged(value: String) {
        // #region agent log
        VpnUiDebugLogger.log(
            hypothesisId = "H4",
            location = "VpnMainViewModel.kt:onActivationKeyChanged",
            message = "activation key changed",
            data = JSONObject().put("length", value.length),
        )
        // #endregion
        _uiState.update { state ->
            state.copy(activationKey = value)
        }
    }

    fun onLocationSelected(location: String) {
        val selected = VpnDemoData.locations.firstOrNull { it.title == location } ?: return
        // #region agent log
        VpnUiDebugLogger.log(
            hypothesisId = "H5",
            location = "VpnMainViewModel.kt:onLocationSelected",
            message = "location selected",
            data = JSONObject().put("location", selected.title),
        )
        // #endregion
        _uiState.update { state ->
            state.copy(selectedLocation = selected)
        }
    }

    fun onConnectClick() {
        val currentState = _uiState.value
        // #region agent log
        AgentDebugNdjsonLogger.log(
            hypothesisId = "H1",
            location = "VpnMainViewModel.kt:onConnectClick",
            message = "premium_connect_clicked",
            runId = "pre-fix",
            data = JSONObject()
                .put("state", currentState.connectionState.name)
                .put("activationKeyLen", currentState.activationKey.length)
                .put("activationKeyBlank", currentState.activationKey.isBlank()),
        )
        // #endregion
        if (currentState.connectionState != VpnConnectionState.Disconnected) {
            // #region agent log
            VpnUiDebugLogger.log(
                hypothesisId = "H3",
                location = "VpnMainViewModel.kt:onConnectClick",
                message = "connect ignored due to state",
                data = JSONObject().put("state", currentState.connectionState.name),
            )
            // #endregion
            return
        }
        if (currentState.activationKey.isBlank()) {
            // #region agent log
            VpnUiDebugLogger.log(
                hypothesisId = "H3",
                location = "VpnMainViewModel.kt:onConnectClick",
                message = "connect blocked by empty activation key",
                data = JSONObject(),
            )
            // #endregion
            return
        }

        connectJob?.cancel()
        timerJob?.cancel()

        _uiState.update { state ->
            state.copy(
                connectionState = VpnConnectionState.Connecting,
                elapsedSeconds = 0L,
            )
        }
        // #region agent log
        VpnUiDebugLogger.log(
            hypothesisId = "H3",
            location = "VpnMainViewModel.kt:onConnectClick",
            message = "state moved to connecting",
            data = JSONObject(),
        )
        // #endregion

        connectJob = viewModelScope.launch {
            delay(1600L)
            _uiState.update { state ->
                state.copy(
                    connectionState = VpnConnectionState.Connected,
                    elapsedSeconds = 0L,
                )
            }
            // #region agent log
            AgentDebugNdjsonLogger.log(
                hypothesisId = "H2",
                location = "VpnMainViewModel.kt:onConnectClick",
                message = "premium_state_set_connected",
                runId = "pre-fix",
                data = JSONObject(),
            )
            // #endregion
            // #region agent log
            VpnUiDebugLogger.log(
                hypothesisId = "H3",
                location = "VpnMainViewModel.kt:onConnectClick",
                message = "state moved to connected",
                data = JSONObject(),
            )
            // #endregion
            startTimer()
        }
    }

    fun onDisconnectClick() {
        connectJob?.cancel()
        timerJob?.cancel()
        // #region agent log
        AgentDebugNdjsonLogger.log(
            hypothesisId = "H2",
            location = "VpnMainViewModel.kt:onDisconnectClick",
            message = "premium_disconnect_clicked",
            runId = "pre-fix",
            data = JSONObject().put("prevState", _uiState.value.connectionState.name),
        )
        // #endregion
        _uiState.update { state ->
            state.copy(
                connectionState = VpnConnectionState.Disconnected,
                elapsedSeconds = 0L,
            )
        }
        // #region agent log
        VpnUiDebugLogger.log(
            hypothesisId = "H3",
            location = "VpnMainViewModel.kt:onDisconnectClick",
            message = "state moved to disconnected",
            data = JSONObject(),
        )
        // #endregion
    }

    private fun startTimer() {
        timerJob?.cancel()
        timerJob = viewModelScope.launch {
            while (isActive) {
                delay(1000L)
                _uiState.update { state ->
                    if (state.connectionState == VpnConnectionState.Connected) {
                        // #region agent log
                        VpnUiDebugLogger.log(
                            hypothesisId = "H3",
                            location = "VpnMainViewModel.kt:startTimer",
                            message = "timer tick",
                            data = JSONObject().put("nextElapsedSeconds", state.elapsedSeconds + 1L),
                        )
                        // #endregion
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

package com.v2ray.ang.ui.premium.vpn

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.v2ray.ang.handler.EmeryAccessManager
import com.v2ray.ang.handler.EmeryVpnSync
import com.v2ray.ang.handler.V2RayServiceManager
import com.v2ray.ang.network.EmeryBackendClient
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import org.json.JSONObject

data class VpnServerRegionUi(
    val serverId: Long,
    val title: String,
    val healthStatus: String,
)

sealed class VpnServiceCommand {
    object Start : VpnServiceCommand()
    object Stop : VpnServiceCommand()
}

class VpnMainViewModel : ViewModel() {

    private val _uiState = MutableStateFlow(VpnMainUiState())
    val uiState: StateFlow<VpnMainUiState> = _uiState.asStateFlow()

    private val _availableRegions = MutableStateFlow<List<VpnServerRegionUi>>(emptyList())
    val availableRegions: StateFlow<List<VpnServerRegionUi>> = _availableRegions.asStateFlow()

    private val _selectedRegion = MutableStateFlow<VpnServerRegionUi?>(null)
    val selectedRegion: StateFlow<VpnServerRegionUi?> = _selectedRegion.asStateFlow()

    private val _commands = MutableSharedFlow<VpnServiceCommand>(extraBufferCapacity = 1)
    val commands: SharedFlow<VpnServiceCommand> = _commands.asSharedFlow()

    private var connectJob: Job? = null
    private var timerJob: Job? = null

    init {
        refreshAvailableRegions()
        observeVpnRuntimeState()
    }

    private fun observeVpnRuntimeState() {
        viewModelScope.launch {
            V2RayServiceManager.vpnState.collectLatest { runtimeState ->
                when (runtimeState) {
                    V2RayServiceManager.VpnRuntimeState.CONNECTING -> {
                        timerJob?.cancel()
                        _uiState.update { state ->
                            state.copy(
                                connectionState = VpnConnectionState.Connecting,
                                errorMessage = null,
                            )
                        }
                    }

                    V2RayServiceManager.VpnRuntimeState.CONNECTED -> {
                        val wasConnected = _uiState.value.connectionState == VpnConnectionState.Connected
                        _uiState.update { state ->
                            state.copy(
                                connectionState = VpnConnectionState.Connected,
                                errorMessage = null,
                            )
                        }
                        if (!wasConnected) {
                            startTimer()
                        }
                    }

                    V2RayServiceManager.VpnRuntimeState.DISCONNECTING -> {
                        // Keep the current UI state until the runtime reports DISCONNECTED.
                    }

                    V2RayServiceManager.VpnRuntimeState.DISCONNECTED -> {
                        timerJob?.cancel()
                        _uiState.update { state ->
                            state.copy(
                                connectionState = VpnConnectionState.Disconnected,
                                elapsedSeconds = 0L,
                            )
                        }
                    }

                    V2RayServiceManager.VpnRuntimeState.ERROR -> {
                        timerJob?.cancel()
                        _uiState.update { state ->
                            state.copy(
                                connectionState = VpnConnectionState.Disconnected,
                                elapsedSeconds = 0L,
                                errorMessage = "VPN start failed",
                            )
                        }
                    }
                }
            }
        }
    }

    fun refreshAvailableRegions() {
        viewModelScope.launch {
            val currentSelectedId = _selectedRegion.value?.serverId
            val regions = EmeryBackendClient.fetchVpnServers()
                .getOrElse { emptyList() }
                .filter { it.isAvailable }
                .map {
                    VpnServerRegionUi(
                        serverId = it.id,
                        title = it.city,
                        healthStatus = it.healthStatus,
                    )
                }

            _availableRegions.value = regions
            _selectedRegion.value = regions.firstOrNull { it.serverId == currentSelectedId } ?: regions.firstOrNull()

            VpnUiDebugLogger.log(
                hypothesisId = "H6",
                location = "VpnMainViewModel.kt:refreshAvailableRegions",
                message = "available regions refreshed",
                data = JSONObject()
                    .put("count", regions.size)
                    .put("selectedServerId", _selectedRegion.value?.serverId ?: -1),
            )
        }
    }

    fun onLocationSelected(serverId: Long) {
        val selected = _availableRegions.value.firstOrNull { it.serverId == serverId } ?: return
        _selectedRegion.value = selected
        VpnUiDebugLogger.log(
            hypothesisId = "H5",
            location = "VpnMainViewModel.kt:onLocationSelected",
            message = "location selected",
            data = JSONObject().put("serverId", selected.serverId).put("title", selected.title),
        )
    }

    fun onConnectClick() {
        val currentState = _uiState.value
        val selectedRegion = _selectedRegion.value ?: run {
            VpnUiDebugLogger.log(
                hypothesisId = "H3",
                location = "VpnMainViewModel.kt:onConnectClick",
                message = "connect blocked by missing region",
                data = JSONObject(),
            )
            return
        }
        val accessKey = EmeryAccessManager.loadProfile()?.accessKey?.trim().orEmpty()
        if (accessKey.isBlank()) {
            VpnUiDebugLogger.log(
                hypothesisId = "H3",
                location = "VpnMainViewModel.kt:onConnectClick",
                message = "connect blocked by missing saved access key",
                data = JSONObject(),
            )
            return
        }
        if (currentState.connectionState != VpnConnectionState.Disconnected) {
            VpnUiDebugLogger.log(
                hypothesisId = "H3",
                location = "VpnMainViewModel.kt:onConnectClick",
                message = "connect ignored due to state",
                data = JSONObject().put("state", currentState.connectionState.name),
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

        connectJob = viewModelScope.launch {
            val result = EmeryVpnSync.connectToServer(accessKey = accessKey, serverId = selectedRegion.serverId)
            result.fold(
                onSuccess = {
                    _commands.tryEmit(VpnServiceCommand.Start)
                    VpnUiDebugLogger.log(
                        hypothesisId = "H3",
                        location = "VpnMainViewModel.kt:onConnectClick",
                        message = "server imported, waiting for runtime connected",
                        data = JSONObject()
                            .put("serverId", selectedRegion.serverId)
                            .put("title", selectedRegion.title),
                    )
                },
                onFailure = { error ->
                    timerJob?.cancel()
                    _uiState.update { state ->
                        state.copy(
                            connectionState = VpnConnectionState.Disconnected,
                            elapsedSeconds = 0L,
                            errorMessage = error.message ?: "connect_failed",
                        )
                    }
                    VpnUiDebugLogger.log(
                        hypothesisId = "H3",
                        location = "VpnMainViewModel.kt:onConnectClick",
                        message = "backend connect failed",
                        data = JSONObject()
                            .put("serverId", selectedRegion.serverId)
                            .put("reason", error.message ?: "unknown"),
                    )
                    refreshAvailableRegions()
                },
            )
        }
    }

    fun onDisconnectClick() {
        if (!V2RayServiceManager.isRunning()) {
            timerJob?.cancel()
            _uiState.update { state ->
                state.copy(
                    connectionState = VpnConnectionState.Disconnected,
                    elapsedSeconds = 0L,
                )
            }
            return
        }

        _commands.tryEmit(VpnServiceCommand.Stop)

        VpnUiDebugLogger.log(
            hypothesisId = "H3",
            location = "VpnMainViewModel.kt:onDisconnectClick",
            message = "stop requested, waiting for runtime disconnected",
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

package com.v2ray.ang.ui.premium.vpn

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.v2ray.ang.AppConfig
import com.v2ray.ang.handler.AngConfigManager
import com.v2ray.ang.handler.EmeryAccessManager
import com.v2ray.ang.handler.EmeryVpnSync
import com.v2ray.ang.handler.MmkvManager
import com.v2ray.ang.network.EmeryBackendClient
import com.v2ray.ang.network.EmeryPoolClient
import com.v2ray.ang.util.AgentDebugNdjsonLogger
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import org.json.JSONObject
import java.net.URLDecoder
import java.nio.charset.StandardCharsets

class VpnMainViewModel : ViewModel() {

    private val _uiState = MutableStateFlow(
        VpnMainUiState(
            activationKey = EmeryAccessManager.loadProfile()?.accessKey.orEmpty(),
        )
    )
    val uiState: StateFlow<VpnMainUiState> = _uiState.asStateFlow()

    private var connectJob: Job? = null
    private var timerJob: Job? = null
    private var serversJob: Job? = null

    init {
        refreshLocations()
    }

    fun refreshLocations() {
        serversJob?.cancel()
        serversJob = viewModelScope.launch {
            _uiState.update { state ->
                state.copy(locationsLoading = true, locationsError = "")
            }

            val result = withTimeoutOrNull(4_000L) {
                EmeryBackendClient.fetchVpnServers()
            } ?: Result.failure(IllegalStateException("server_list_timeout"))
            result.fold(
                onSuccess = { servers ->
                    val locations = servers
                        .filter { it.isAvailable }
                        .map { server ->
                            VpnLocationOption(
                                id = server.id.toString(),
                                title = server.city.ifBlank { "Server #${server.id}" },
                            )
                        }
                        .distinctBy { it.id }

                    if (locations.isNotEmpty()) {
                        applyLocations(locations, "")
                    } else {
                        refreshPoolLocationsFallback("Серверы пока недоступны")
                    }
                },
                onFailure = { error ->
                    VpnUiDebugLogger.log(
                        hypothesisId = "H6",
                        location = "VpnMainViewModel.kt:refreshLocations",
                        message = "server list fetch failed",
                        data = JSONObject().put("error", error.message ?: "unknown"),
                    )
                    refreshPoolLocationsFallback("Не удалось загрузить серверы")
                },
            )
        }
    }

    private suspend fun refreshPoolLocationsFallback(fallbackError: String) {
        val key = _uiState.value.activationKey
        if (key.isBlank()) {
            applyLocations(VpnDemoData.unavailableLocations, fallbackError)
            return
        }
        val poolResult = withTimeoutOrNull(8_000L) {
            EmeryPoolClient.fetchPoolImportText(key)
        } ?: Result.failure(IllegalStateException("pool_list_timeout"))
        poolResult.fold(
            onSuccess = { importText ->
                val locations = importText
                    .lineSequence()
                    .map { it.trim() }
                    .filter { isImportProfileLink(it) }
                    .distinct()
                    .mapIndexed { index, link ->
                        VpnLocationOption(
                            id = "pool-${index + 1}",
                            title = titleFromConfigLink(link, index + 1),
                            importText = link,
                        )
                    }
                    .toList()

                applyLocations(
                    locations.ifEmpty { VpnDemoData.unavailableLocations },
                    if (locations.isEmpty()) fallbackError else "",
                )
            },
            onFailure = { error ->
                VpnUiDebugLogger.log(
                    hypothesisId = "H6",
                    location = "VpnMainViewModel.kt:refreshPoolLocationsFallback",
                    message = "pool list fetch failed",
                    data = JSONObject().put("error", error.message ?: "unknown"),
                )
                applyLocations(VpnDemoData.unavailableLocations, fallbackError)
            },
        )
    }

    private fun applyLocations(locations: List<VpnLocationOption>, error: String) {
        _uiState.update { state ->
            val selected = locations.firstOrNull { it.id == state.selectedLocation.id }
                ?: locations.first()
            state.copy(
                locations = locations,
                selectedLocation = selected,
                locationsLoading = false,
                locationsError = error,
            )
        }
    }

    private fun isImportProfileLink(link: String): Boolean {
        val value = link.trim().lowercase()
        return value.contains("://") && !value.startsWith("http://") && !value.startsWith("https://")
    }

    private fun titleFromConfigLink(link: String, index: Int): String {
        val rawTitle = link.substringAfter('#', "").trim()
        if (rawTitle.isBlank()) return "Server #$index"
        return try {
            URLDecoder.decode(rawTitle, StandardCharsets.UTF_8.name()).ifBlank { "Server #$index" }
        } catch (_: Exception) {
            rawTitle
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
            state.copy(activationKey = value)
        }
    }

    fun onLocationSelected(location: String) {
        val selected = _uiState.value.locations.firstOrNull { it.title == location } ?: return
        VpnUiDebugLogger.log(
            hypothesisId = "H5",
            location = "VpnMainViewModel.kt:onLocationSelected",
            message = "location selected",
            data = JSONObject()
                .put("location", selected.title)
                .put("serverId", selected.id),
        )
        _uiState.update { state ->
            state.copy(selectedLocation = selected)
        }
    }

    fun onConnectClick(startVpnService: () -> Boolean = { true }) {
        val currentState = _uiState.value
        AgentDebugNdjsonLogger.log(
            hypothesisId = "H1",
            location = "VpnMainViewModel.kt:onConnectClick",
            message = "premium_connect_clicked",
            runId = "dynamic-server-list",
            data = JSONObject()
                .put("state", currentState.connectionState.name)
                .put("activationKeyLen", currentState.activationKey.length)
                .put("activationKeyBlank", currentState.activationKey.isBlank())
                .put("selectedServerId", currentState.selectedLocation.id),
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
                locationsError = "",
            )
        }
        VpnUiDebugLogger.log(
            hypothesisId = "H3",
            location = "VpnMainViewModel.kt:onConnectClick",
            message = "state moved to connecting",
            data = JSONObject(),
        )

        connectJob = viewModelScope.launch {
            val result = connectSelectedLocation(currentState)
            result.fold(
                onSuccess = { payload ->
                    val serviceStarted = try {
                        startVpnService()
                    } catch (e: Exception) {
                        VpnUiDebugLogger.log(
                            hypothesisId = "H8",
                            location = "VpnMainViewModel.kt:onConnectClick",
                            message = "vpn service start threw",
                            data = JSONObject().put("error", e.message ?: "unknown"),
                        )
                        false
                    }
                    if (!serviceStarted) {
                        _uiState.update { state ->
                            state.copy(
                                connectionState = VpnConnectionState.Disconnected,
                                elapsedSeconds = 0L,
                                locationsError = "Не удалось запустить VPN-сервис",
                            )
                        }
                        VpnUiDebugLogger.log(
                            hypothesisId = "H8",
                            location = "VpnMainViewModel.kt:onConnectClick",
                            message = "vpn service start failed",
                            data = JSONObject()
                                .put("serverId", payload.serverId)
                                .put("city", payload.city),
                        )
                        return@fold
                    }

                    _uiState.update { state ->
                        state.copy(
                            connectionState = VpnConnectionState.Connected,
                            elapsedSeconds = 0L,
                            locationsError = "",
                        )
                    }
                    AgentDebugNdjsonLogger.log(
                        hypothesisId = "H2",
                        location = "VpnMainViewModel.kt:onConnectClick",
                        message = "premium_state_set_connected",
                        runId = "dynamic-server-list",
                        data = JSONObject()
                            .put("serverId", payload.serverId)
                            .put("city", payload.city),
                    )
                    VpnUiDebugLogger.log(
                        hypothesisId = "H3",
                        location = "VpnMainViewModel.kt:onConnectClick",
                        message = "state moved to connected",
                        data = JSONObject(),
                    )
                    startTimer()
                },
                onFailure = { error ->
                    _uiState.update { state ->
                        state.copy(
                            connectionState = VpnConnectionState.Disconnected,
                            elapsedSeconds = 0L,
                            locationsError = "Не удалось подключиться к серверу",
                        )
                    }
                    VpnUiDebugLogger.log(
                        hypothesisId = "H7",
                        location = "VpnMainViewModel.kt:onConnectClick",
                        message = "connect failed",
                        data = JSONObject()
                            .put("serverId", currentState.selectedLocation.id)
                            .put("error", error.message ?: "unknown"),
                    )
                },
            )
        }
    }

    private suspend fun connectSelectedLocation(state: VpnMainUiState): Result<EmeryVpnSync.ConnectServerResult> {
        val serverId = state.selectedLocation.id.toLongOrNull()
        if (serverId != null) {
            return EmeryVpnSync.connectToServer(state.activationKey, serverId)
        }

        val importText = state.selectedLocation.importText.orEmpty().trim()
        if (importText.isBlank()) {
            return Result.failure(IllegalStateException("missing_import_text"))
        }

        return withContext(Dispatchers.IO) {
            val (count, _) = AngConfigManager.importBatchConfig(
                importText,
                AppConfig.EMERY_BACKEND_SUBSCRIPTION_ID,
                append = false,
            )
            if (count <= 0) {
                return@withContext Result.failure(IllegalStateException("import_failed"))
            }

            val selectedGuid = MmkvManager.decodeServerList(AppConfig.EMERY_BACKEND_SUBSCRIPTION_ID).firstOrNull().orEmpty()
            if (selectedGuid.isBlank()) {
                return@withContext Result.failure(IllegalStateException("selected_server_missing"))
            }

            MmkvManager.setSelectServer(selectedGuid)
            Result.success(
                EmeryVpnSync.ConnectServerResult(
                    serverId = -1L,
                    city = state.selectedLocation.title,
                    selectedGuid = selectedGuid,
                )
            )
        }
    }

    fun onDisconnectClick(stopVpnService: () -> Unit = {}) {
        connectJob?.cancel()
        timerJob?.cancel()
        stopVpnService()
        AgentDebugNdjsonLogger.log(
            hypothesisId = "H2",
            location = "VpnMainViewModel.kt:onDisconnectClick",
            message = "premium_disconnect_clicked",
            runId = "dynamic-server-list",
            data = JSONObject().put("prevState", _uiState.value.connectionState.name),
        )
        _uiState.update { state ->
            state.copy(
                connectionState = VpnConnectionState.Disconnected,
                elapsedSeconds = 0L,
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
                        VpnUiDebugLogger.log(
                            hypothesisId = "H3",
                            location = "VpnMainViewModel.kt:startTimer",
                            message = "timer tick",
                            data = JSONObject().put("nextElapsedSeconds", state.elapsedSeconds + 1L),
                        )
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
        serversJob?.cancel()
        super.onCleared()
    }
}

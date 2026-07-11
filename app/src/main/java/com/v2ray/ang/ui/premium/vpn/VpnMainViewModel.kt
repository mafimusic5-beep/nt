package com.v2ray.ang.ui.premium.vpn

import android.app.Application
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import androidx.core.content.ContextCompat
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.v2ray.ang.AppConfig
import com.v2ray.ang.handler.AngConfigManager
import com.v2ray.ang.handler.EmeryVpnSync
import com.v2ray.ang.handler.MmkvManager
import com.v2ray.ang.network.EmeryBackendClient
import com.v2ray.ang.network.EmeryPoolClient
import com.v2ray.ang.util.AgentDebugNdjsonLogger
import com.v2ray.ang.util.MessageUtil
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

private const val SKRYON_ACTIVATION_CODE_PREF = "SKRYON_ACTIVATION_CODE"
private const val SKRYON_ACTIVATION_CONFIG_PREF = "SKRYON_ACTIVATION_CONFIG"

class VpnMainViewModel(application: Application) : AndroidViewModel(application) {

    private companion object {
        const val DEFAULT_ACCESS_KEY = "DEV"
        const val DEFAULT_REGION_TITLE = "Регион"
        const val SERVICE_STATE_SYNC_ATTEMPTS = 5
        const val SERVICE_START_CONFIRMATION_ATTEMPTS = 15
        const val SERVICE_STATE_SYNC_DELAY_MS = 700L
        const val SERVICE_START_CONFIRMATION_DELAY_MS = 1_000L
    }

    private val appContext: Context
        get() = getApplication<Application>().applicationContext

    private val _uiState = MutableStateFlow(
        VpnMainUiState(
            activationKey = savedActivationCode().ifBlank { DEFAULT_ACCESS_KEY },
        )
    )
    val uiState: StateFlow<VpnMainUiState> = _uiState.asStateFlow()

    private var connectJob: Job? = null
    private var timerJob: Job? = null
    private var serversJob: Job? = null
    private var serviceStateSyncJob: Job? = null
    private var serviceStateReceiverRegistered = false
    private var waitingForStartConfirmation = false

    private val serviceStateReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context?, intent: Intent?) {
            when (intent?.getIntExtra("key", 0)) {
                AppConfig.MSG_STATE_RUNNING,
                AppConfig.MSG_STATE_START_SUCCESS -> onServiceRunning()

                AppConfig.MSG_STATE_NOT_RUNNING -> {
                    if (!waitingForStartConfirmation) {
                        onServiceStopped()
                    }
                }

                AppConfig.MSG_STATE_START_FAILURE -> {
                    waitingForStartConfirmation = false
                    serviceStateSyncJob?.cancel()
                    setDisconnectedWithError("Не удалось запустить VPN-сервис")
                }

                AppConfig.MSG_STATE_STOP_SUCCESS -> {
                    waitingForStartConfirmation = false
                    onServiceStopped()
                }
            }
        }
    }

    init {
        registerServiceStateReceiver()
        refreshConnectionState()
        refreshLocations()
    }

    private fun registerServiceStateReceiver() {
        if (serviceStateReceiverRegistered) return

        try {
            ContextCompat.registerReceiver(
                appContext,
                serviceStateReceiver,
                IntentFilter(AppConfig.BROADCAST_ACTION_ACTIVITY),
                ContextCompat.RECEIVER_NOT_EXPORTED,
            )
            serviceStateReceiverRegistered = true
        } catch (error: Exception) {
            VpnUiDebugLogger.log(
                hypothesisId = "H10",
                location = "VpnMainViewModel.kt:registerServiceStateReceiver",
                message = "failed to register service state receiver",
                data = JSONObject().put("error", error.message ?: "unknown"),
            )
        }
    }

    fun refreshConnectionState() {
        registerServiceStateReceiver()
        if (!serviceStateReceiverRegistered) return

        serviceStateSyncJob?.cancel()
        serviceStateSyncJob = viewModelScope.launch {
            repeat(SERVICE_STATE_SYNC_ATTEMPTS) { attempt ->
                MessageUtil.sendMsg2Service(appContext, AppConfig.MSG_REGISTER_CLIENT, "")
                if (attempt < SERVICE_STATE_SYNC_ATTEMPTS - 1) {
                    delay(SERVICE_STATE_SYNC_DELAY_MS)
                }
            }
        }
    }

    private fun awaitServiceStartConfirmation() {
        if (!waitingForStartConfirmation || _uiState.value.connectionState != VpnConnectionState.Connecting) {
            return
        }

        serviceStateSyncJob?.cancel()
        serviceStateSyncJob = viewModelScope.launch {
            repeat(SERVICE_START_CONFIRMATION_ATTEMPTS) {
                MessageUtil.sendMsg2Service(appContext, AppConfig.MSG_REGISTER_CLIENT, "")
                delay(SERVICE_START_CONFIRMATION_DELAY_MS)
                if (!waitingForStartConfirmation || _uiState.value.connectionState != VpnConnectionState.Connecting) {
                    return@launch
                }
            }

            if (waitingForStartConfirmation && _uiState.value.connectionState == VpnConnectionState.Connecting) {
                waitingForStartConfirmation = false
                setDisconnectedWithError("VPN-сервис не подтвердил подключение")
            }
        }
    }

    private fun onServiceRunning() {
        waitingForStartConfirmation = false
        serviceStateSyncJob?.cancel()
        _uiState.update { state ->
            state.copy(
                connectionState = VpnConnectionState.Connected,
                locationsError = "",
            )
        }
        startTimer()
    }

    private fun onServiceStopped() {
        serviceStateSyncJob?.cancel()
        timerJob?.cancel()
        _uiState.update { state ->
            state.copy(
                connectionState = VpnConnectionState.Disconnected,
                elapsedSeconds = 0L,
            )
        }
    }

    fun refreshLocations() {
        serversJob?.cancel()
        serversJob = viewModelScope.launch {
            _uiState.update { state ->
                state.copy(
                    activationKey = savedActivationCode().ifBlank { state.activationKey.ifBlank { DEFAULT_ACCESS_KEY } },
                    locationsLoading = true,
                    locationsError = "",
                )
            }

            val activatedLocation = savedSkryonConfigLocation()
            if (activatedLocation != null) {
                applyLocations(listOf(activatedLocation), "")
                VpnUiDebugLogger.log(
                    hypothesisId = "H9",
                    location = "VpnMainViewModel.kt:refreshLocations",
                    message = "using saved activated skryon config",
                    data = JSONObject().put("title", activatedLocation.title),
                )
                return@launch
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
                                title = serverRegionTitle(server.city.ifBlank { "Server #${server.id}" }, server.id.toInt()),
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
        val activatedLocation = savedSkryonConfigLocation()
        if (activatedLocation != null) {
            applyLocations(listOf(activatedLocation), "")
            return
        }

        val key = _uiState.value.activationKey.ifBlank { DEFAULT_ACCESS_KEY }
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
        val safeLocations = locations.ifEmpty { VpnDemoData.unavailableLocations }
        _uiState.update { state ->
            val selected = safeLocations.firstOrNull { it.id == state.selectedLocation.id }
                ?: safeLocations.first()
            state.copy(
                activationKey = savedActivationCode().ifBlank { state.activationKey.ifBlank { DEFAULT_ACCESS_KEY } },
                locations = safeLocations,
                selectedLocation = selected,
                locationsLoading = false,
                locationsError = error,
            )
        }
    }

    private fun savedSkryonConfigLocation(): VpnLocationOption? {
        val config = MmkvManager.decodeSettingsString(SKRYON_ACTIVATION_CONFIG_PREF, "")
            ?.trim()
            .orEmpty()
        if (!isImportProfileLink(config)) {
            return null
        }
        return VpnLocationOption(
            id = "skryon-activated",
            title = titleFromConfigLink(config, 1),
            importText = config,
        )
    }

    private fun savedActivationCode(): String {
        return MmkvManager.decodeSettingsString(SKRYON_ACTIVATION_CODE_PREF, "")
            ?.trim()
            .orEmpty()
    }

    private fun isImportProfileLink(link: String): Boolean {
        val value = link.trim().lowercase()
        return value.contains("://") && !value.startsWith("http://") && !value.startsWith("https://")
    }

    private fun titleFromConfigLink(link: String, index: Int): String {
        val rawTitle = link.substringAfter('#', "").trim()
        val decodedTitle = try {
            URLDecoder.decode(rawTitle, StandardCharsets.UTF_8.name()).trim()
        } catch (_: Exception) {
            rawTitle
        }

        val fromRemark = serverRegionTitleOrBlank(decodedTitle)
        if (fromRemark.isNotBlank()) return fromRemark

        val host = link.substringAfter('@', "")
            .substringBefore('?')
            .substringBefore('#')
            .substringBefore(':')
            .trim()
        val fromHost = serverRegionTitleOrBlank(host)
        if (fromHost.isNotBlank()) return fromHost

        return decodedTitle.ifBlank { "$DEFAULT_REGION_TITLE #$index" }
    }

    private fun serverRegionTitle(raw: String, index: Int): String {
        return serverRegionTitleOrBlank(raw).ifBlank {
            raw.trim().takeIf { it.isNotBlank() } ?: "$DEFAULT_REGION_TITLE #$index"
        }
    }

    private fun serverRegionTitleOrBlank(raw: String): String {
        val value = raw.trim()
        if (value.isBlank()) return ""

        val lower = value.lowercase()
            .replace('_', '-')
            .replace('.', '-')
            .replace(' ', '-')

        val code = when {
            hasRegionToken(lower, "de") || lower.contains("germany") || lower.contains("deutschland") || lower.contains("герман") || lower.contains("frankfurt") || lower.contains("франкфурт") -> "DE"
            hasRegionToken(lower, "nl") || lower.contains("netherlands") || lower.contains("nederland") || lower.contains("нидер") || lower.contains("amsterdam") || lower.contains("амстердам") -> "NL"
            hasRegionToken(lower, "fr") || lower.contains("france") || lower.contains("франц") || lower.contains("paris") || lower.contains("париж") -> "FR"
            hasRegionToken(lower, "ru") || lower.contains("russia") || lower.contains("росси") || lower.contains("moscow") || lower.contains("москва") -> "RU"
            hasRegionToken(lower, "eu") || lower.contains("europe") || lower.contains("европ") -> "EU"
            hasRegionToken(lower, "pl") || lower.contains("poland") || lower.contains("польш") || lower.contains("warsaw") || lower.contains("варшав") -> "PL"
            hasRegionToken(lower, "uk") || hasRegionToken(lower, "gb") || lower.contains("united-kingdom") || lower.contains("london") || lower.contains("лондон") -> "UK"
            hasRegionToken(lower, "us") || hasRegionToken(lower, "usa") || lower.contains("united-states") || lower.contains("america") || lower.contains("new-york") -> "US"
            hasRegionToken(lower, "se") || lower.contains("sweden") || lower.contains("stockholm") -> "SE"
            hasRegionToken(lower, "fi") || lower.contains("finland") || lower.contains("helsinki") -> "FI"
            hasRegionToken(lower, "es") || lower.contains("spain") || lower.contains("madrid") -> "ES"
            hasRegionToken(lower, "it") || lower.contains("italy") || lower.contains("milan") || lower.contains("rome") -> "IT"
            hasRegionToken(lower, "tr") || lower.contains("turkey") || lower.contains("istanbul") -> "TR"
            hasRegionToken(lower, "sg") || lower.contains("singapore") -> "SG"
            else -> ""
        }

        return if (code.isBlank()) "" else "$DEFAULT_REGION_TITLE $code"
    }

    private fun hasRegionToken(value: String, token: String): Boolean {
        return Regex("(^|[^a-z0-9])${Regex.escape(token.lowercase())}([^a-z0-9]|$)").containsMatchIn(value)
    }

    fun onActivationKeyChanged(value: String) {
        VpnUiDebugLogger.log(
            hypothesisId = "H4",
            location = "VpnMainViewModel.kt:onActivationKeyChanged",
            message = "activation key changed",
            data = JSONObject().put("length", value.length),
        )
        _uiState.update { state ->
            state.copy(activationKey = value.ifBlank { DEFAULT_ACCESS_KEY })
        }
    }

    fun onLocationSelected(location: String) {
        val selected = _uiState.value.locations.firstOrNull {
            it.id == location || it.title == location
        } ?: return
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

    fun onConnectClick(startVpnService: (String) -> Boolean = { true }) {
        val currentState = _uiState.value.let { state ->
            state.copy(activationKey = state.activationKey.ifBlank { savedActivationCode().ifBlank { DEFAULT_ACCESS_KEY } })
        }
        AgentDebugNdjsonLogger.log(
            hypothesisId = "H1",
            location = "VpnMainViewModel.kt:onConnectClick",
            message = "premium_connect_clicked",
            runId = "dynamic-server-list",
            data = JSONObject()
                .put("state", currentState.connectionState.name)
                .put("activationKeyLen", currentState.activationKey.length)
                .put("activationKeyBlank", false)
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

        connectJob?.cancel()
        timerJob?.cancel()
        waitingForStartConfirmation = true

        _uiState.update { state ->
            state.copy(
                activationKey = state.activationKey.ifBlank { savedActivationCode().ifBlank { DEFAULT_ACCESS_KEY } },
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
                    val serviceStartRequested = try {
                        startVpnService(payload.selectedGuid)
                    } catch (e: Exception) {
                        VpnUiDebugLogger.log(
                            hypothesisId = "H8",
                            location = "VpnMainViewModel.kt:onConnectClick",
                            message = "vpn service start threw",
                            data = JSONObject().put("error", e.message ?: "unknown"),
                        )
                        false
                    }
                    if (!serviceStartRequested) {
                        waitingForStartConfirmation = false
                        setDisconnectedWithError("Не удалось запустить VPN-сервис")
                        VpnUiDebugLogger.log(
                            hypothesisId = "H8",
                            location = "VpnMainViewModel.kt:onConnectClick",
                            message = "vpn service start request failed",
                            data = JSONObject()
                                .put("serverId", payload.serverId)
                                .put("city", payload.city),
                        )
                        return@fold
                    }

                    AgentDebugNdjsonLogger.log(
                        hypothesisId = "H2",
                        location = "VpnMainViewModel.kt:onConnectClick",
                        message = "premium_waiting_for_service_confirmation",
                        runId = "service-state-sync",
                        data = JSONObject()
                            .put("serverId", payload.serverId)
                            .put("city", payload.city)
                            .put("selectedGuid", payload.selectedGuid),
                    )
                    awaitServiceStartConfirmation()
                },
                onFailure = { error ->
                    waitingForStartConfirmation = false
                    setDisconnectedWithError("Не удалось подключиться к серверу")
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

    private fun setDisconnectedWithError(message: String) {
        waitingForStartConfirmation = false
        serviceStateSyncJob?.cancel()
        timerJob?.cancel()
        _uiState.update { state ->
            state.copy(
                connectionState = VpnConnectionState.Disconnected,
                elapsedSeconds = 0L,
                locationsError = message,
            )
        }
    }

    private suspend fun connectSelectedLocation(state: VpnMainUiState): Result<EmeryVpnSync.ConnectServerResult> {
        val normalizedState = state.copy(activationKey = state.activationKey.ifBlank { savedActivationCode().ifBlank { DEFAULT_ACCESS_KEY } })
        val serverId = normalizedState.selectedLocation.id.toLongOrNull()
        if (serverId != null) {
            return EmeryVpnSync.connectToServer(normalizedState.activationKey, serverId)
        }

        val importText = normalizedState.selectedLocation.importText.orEmpty().trim()
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
                    city = normalizedState.selectedLocation.title,
                    selectedGuid = selectedGuid,
                )
            )
        }
    }

    fun onDisconnectClick(stopVpnService: () -> Unit = {}) {
        connectJob?.cancel()
        serviceStateSyncJob?.cancel()
        timerJob?.cancel()
        waitingForStartConfirmation = false
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
                activationKey = state.activationKey.ifBlank { savedActivationCode().ifBlank { DEFAULT_ACCESS_KEY } },
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
        if (timerJob?.isActive == true) return
        timerJob = viewModelScope.launch {
            while (isActive) {
                delay(1000L)
                _uiState.update { state ->
                    if (state.connectionState == VpnConnectionState.Connected) {
                        state.copy(elapsedSeconds = state.elapsedSeconds + 1)
                    } else {
                        state.copy(activationKey = state.activationKey.ifBlank { savedActivationCode().ifBlank { DEFAULT_ACCESS_KEY } })
                    }
                }
            }
        }
    }

    override fun onCleared() {
        connectJob?.cancel()
        timerJob?.cancel()
        serversJob?.cancel()
        serviceStateSyncJob?.cancel()
        if (serviceStateReceiverRegistered) {
            MessageUtil.sendMsg2Service(appContext, AppConfig.MSG_UNREGISTER_CLIENT, "")
            try {
                appContext.unregisterReceiver(serviceStateReceiver)
            } catch (_: IllegalArgumentException) {
                // Receiver was already unregistered by the framework.
            }
            serviceStateReceiverRegistered = false
        }
        super.onCleared()
    }
}

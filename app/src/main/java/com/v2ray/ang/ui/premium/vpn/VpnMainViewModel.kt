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
import com.v2ray.ang.network.EmeryRegionEventsClient
import com.v2ray.ang.util.AgentDebugNdjsonLogger
import com.v2ray.ang.util.MessageUtil
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.async
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import org.json.JSONObject
import java.net.URLDecoder
import java.nio.charset.StandardCharsets

private const val SKRYON_ACTIVATION_CODE_PREF = "SKRYON_ACTIVATION_CODE"
private const val SKRYON_ACTIVATION_CONFIG_PREF = "SKRYON_ACTIVATION_CONFIG"
private const val SKRYON_SELECTED_REGION_PREF = "SKRYON_SELECTED_REGION"
private const val SKRYON_LAST_CONNECTED_REGION_ID_PREF = "SKRYON_LAST_CONNECTED_REGION_ID"
private const val SKRYON_LAST_CONNECTED_REGION_TITLE_PREF = "SKRYON_LAST_CONNECTED_REGION_TITLE"

class VpnMainViewModel(application: Application) : AndroidViewModel(application) {

    private companion object {
        const val DEFAULT_ACCESS_KEY = "DEV"
        const val DEFAULT_REGION_TITLE = "Регион"
        const val SERVICE_STATE_SYNC_ATTEMPTS = 5
        const val SERVICE_START_CONFIRMATION_ATTEMPTS = 15
        const val SERVICE_STATE_SYNC_DELAY_MS = 700L
        const val SERVICE_START_CONFIRMATION_DELAY_MS = 1_000L
        const val SERVER_LIST_TIMEOUT_MS = 1_500L
        const val POOL_LIST_TIMEOUT_MS = 1_500L
        const val REGION_EVENT_RECONNECT_DELAY_MS = 5_000L
    }

    private data class ConnectAttemptResult(
        val payload: EmeryVpnSync.ConnectServerResult,
        val location: VpnLocationOption,
    )

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
    private var regionEventsJob: Job? = null
    private var serviceStateSyncJob: Job? = null
    private var serviceStateReceiverRegistered = false
    private var waitingForStartConfirmation = false
    private var lastAppliedLocationsSignature = ""
    private var lastRegionRevision = ""

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
        startRegionEventListener()
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
        refreshLocationsInternal(
            source = "manual_or_startup",
            forceApply = true,
            showLoadingWhenEmpty = true,
        )
    }

    private fun startRegionEventListener() {
        if (regionEventsJob?.isActive == true) return
        regionEventsJob = viewModelScope.launch {
            while (isActive) {
                val accessKey = _uiState.value.activationKey.ifBlank {
                    savedActivationCode().ifBlank { DEFAULT_ACCESS_KEY }
                }

                if (accessKey.isBlank() || accessKey == DEFAULT_ACCESS_KEY) {
                    delay(REGION_EVENT_RECONNECT_DELAY_MS)
                    continue
                }

                if (lastRegionRevision.isBlank()) {
                    val revisionResult = EmeryRegionEventsClient.fetchRegionsRevision(accessKey)
                    if (revisionResult.isSuccess) {
                        val revision = revisionResult.getOrNull().orEmpty()
                        lastRegionRevision = revision
                        VpnUiDebugLogger.log(
                            hypothesisId = "H14",
                            location = "VpnMainViewModel.kt:startRegionEventListener",
                            message = "region revision listener initialized",
                            data = JSONObject().put("revision", revision),
                        )
                    } else {
                        val error = revisionResult.exceptionOrNull()
                        VpnUiDebugLogger.log(
                            hypothesisId = "H14",
                            location = "VpnMainViewModel.kt:startRegionEventListener",
                            message = "region revision init failed",
                            data = JSONObject().put("error", error?.message ?: "unknown"),
                        )
                        delay(REGION_EVENT_RECONNECT_DELAY_MS)
                        continue
                    }
                }

                val knownRevision = lastRegionRevision
                EmeryRegionEventsClient.awaitRegionsChanged(accessKey, knownRevision)
                    .onSuccess { revision ->
                        if (revision.isNotBlank() && revision != lastRegionRevision) {
                            val previousRevision = lastRegionRevision
                            lastRegionRevision = revision
                            VpnUiDebugLogger.log(
                                hypothesisId = "H14",
                                location = "VpnMainViewModel.kt:startRegionEventListener",
                                message = "backend region change event received",
                                data = JSONObject()
                                    .put("previousRevision", previousRevision)
                                    .put("revision", revision),
                            )
                            refreshLocationsInternal(
                                source = "backend_region_event",
                                forceApply = false,
                                showLoadingWhenEmpty = false,
                            )
                        }
                    }
                    .onFailure { error ->
                        VpnUiDebugLogger.log(
                            hypothesisId = "H14",
                            location = "VpnMainViewModel.kt:startRegionEventListener",
                            message = "region event listener disconnected",
                            data = JSONObject().put("error", error.message ?: "unknown"),
                        )
                    }

                delay(REGION_EVENT_RECONNECT_DELAY_MS)
            }
        }
    }

    private fun refreshLocationsInternal(
        source: String,
        forceApply: Boolean,
        showLoadingWhenEmpty: Boolean,
    ) {
        if (!forceApply && serversJob?.isActive == true) {
            return
        }
        serversJob?.cancel()
        serversJob = viewModelScope.launch {
            val fallbackLocation = savedSkryonConfigLocation()
            val existingLocations = _uiState.value.locations.filter(::isSelectableLocation)
            val instantLocations = when {
                existingLocations.isNotEmpty() -> existingLocations
                fallbackLocation != null -> listOf(fallbackLocation)
                else -> emptyList()
            }

            if (forceApply && instantLocations.isNotEmpty()) {
                applyLocations(instantLocations, "")
                _uiState.update { state ->
                    state.copy(
                        activationKey = savedActivationCode().ifBlank { state.activationKey.ifBlank { DEFAULT_ACCESS_KEY } },
                        locationsLoading = false,
                        locationsError = "",
                    )
                }
            } else if (showLoadingWhenEmpty && instantLocations.isEmpty()) {
                _uiState.update { state ->
                    state.copy(
                        activationKey = savedActivationCode().ifBlank { state.activationKey.ifBlank { DEFAULT_ACCESS_KEY } },
                        locationsLoading = true,
                        locationsError = "",
                    )
                }
            }

            val accessKey = _uiState.value.activationKey.ifBlank {
                savedActivationCode().ifBlank { DEFAULT_ACCESS_KEY }
            }

            val backendDeferred = async(Dispatchers.IO) {
                withTimeoutOrNull(SERVER_LIST_TIMEOUT_MS) {
                    EmeryBackendClient.fetchVpnServers()
                } ?: Result.failure(IllegalStateException("server_list_timeout"))
            }
            val poolDeferred = async(Dispatchers.IO) {
                withTimeoutOrNull(POOL_LIST_TIMEOUT_MS) {
                    EmeryPoolClient.fetchPoolImportText(accessKey)
                } ?: Result.failure(IllegalStateException("pool_list_timeout"))
            }

            val backendResult = backendDeferred.await()
            val poolResult = poolDeferred.await()

            val backendLocations = backendResult.getOrNull()
                .orEmpty()
                .asSequence()
                .filter { it.isAvailable }
                .map { server ->
                    VpnLocationOption(
                        id = server.id.toString(),
                        title = serverRegionTitle(
                            raw = server.city.ifBlank { "Server #${server.id}" },
                            index = server.id.toInt(),
                        ),
                    )
                }
                .toList()

            val poolLocations = poolResult.getOrNull()
                ?.let(::locationsFromPoolImportText)
                .orEmpty()

            val onlineLocations = mergeOnlineLocations(backendLocations, poolLocations)
            val visibleLocations = if (onlineLocations.isNotEmpty()) {
                onlineLocations
            } else {
                instantLocations.ifEmpty { listOfNotNull(fallbackLocation) }
            }
            val visibleSignature = locationsSignature(visibleLocations)
            val changed = visibleSignature.isNotBlank() && visibleSignature != lastAppliedLocationsSignature

            val error = when {
                onlineLocations.isNotEmpty() -> ""
                visibleLocations.isNotEmpty() -> ""
                backendResult.isFailure && poolResult.isFailure -> "Не удалось загрузить регионы из сети"
                else -> "Серверы сейчас недоступны"
            }

            VpnUiDebugLogger.log(
                hypothesisId = "H14",
                location = "VpnMainViewModel.kt:refreshLocationsInternal",
                message = "region list applied from explicit refresh/event",
                data = JSONObject()
                    .put("source", source)
                    .put("backendCount", backendLocations.size)
                    .put("poolCount", poolLocations.size)
                    .put("visibleCount", visibleLocations.size)
                    .put("changed", changed)
                    .put("forceApply", forceApply)
                    .put("signature", visibleSignature)
                    .put("previousSignature", lastAppliedLocationsSignature)
                    .put("fallbackUsed", fallbackLocation != null)
                    .put("lastConnectedRegionId", savedLastConnectedRegionId())
                    .put("backendError", backendResult.exceptionOrNull()?.message.orEmpty())
                    .put("poolError", poolResult.exceptionOrNull()?.message.orEmpty()),
            )

            if (forceApply || changed || _uiState.value.locations.none(::isSelectableLocation)) {
                if (visibleSignature.isNotBlank()) {
                    lastAppliedLocationsSignature = visibleSignature
                }
                applyLocations(visibleLocations, error)
            } else if (error.isBlank()) {
                _uiState.update { state -> state.copy(locationsLoading = false, locationsError = "") }
            } else if (showLoadingWhenEmpty && _uiState.value.locations.none(::isSelectableLocation)) {
                _uiState.update { state -> state.copy(locationsLoading = false, locationsError = error) }
            }
        }
    }

    private fun locationsSignature(locations: List<VpnLocationOption>): String {
        return locations
            .filter(::isSelectableLocation)
            .map { location ->
                listOf(
                    regionIdentity(location),
                    location.id.trim(),
                    location.title.trim(),
                    location.importText.trim().hashCode().toString(),
                ).joinToString("|")
            }
            .sorted()
            .joinToString(";")
    }

    private fun locationsFromPoolImportText(importText: String): List<VpnLocationOption> {
        return importText
            .lineSequence()
            .map { it.trim() }
            .filter { isImportProfileLink(it) }
            .distinct()
            .mapIndexed { index, link ->
                VpnLocationOption(
                    id = "pool-${link.hashCode().toUInt().toString(16)}",
                    title = titleFromConfigLink(link, index + 1),
                    importText = link,
                )
            }
            .toList()
    }

    private fun mergeOnlineLocations(
        backendLocations: List<VpnLocationOption>,
        poolLocations: List<VpnLocationOption>,
    ): List<VpnLocationOption> {
        val byRegion = linkedMapOf<String, VpnLocationOption>()
        (backendLocations + poolLocations).forEach { location ->
            if (!isSelectableLocation(location)) return@forEach
            val key = regionIdentity(location)
            if (key.isNotBlank() && key !in byRegion) {
                byRegion[key] = location
            }
        }
        return byRegion.values.sortedWith(
            compareBy<VpnLocationOption> { regionSortOrder(it.title) }
                .thenBy { it.title.lowercase() }
        )
    }

    private fun regionIdentity(location: VpnLocationOption): String {
        return serverRegionTitleOrBlank(location.title)
            .ifBlank { location.title.trim().lowercase() }
    }

    private fun regionSortOrder(title: String): Int {
        val normalized = serverRegionTitleOrBlank(title)
        val code = normalized.substringAfterLast(' ', "").uppercase()
        val order = listOf("FR", "DE", "NL", "PL", "UK", "FI", "SE", "ES", "IT", "TR", "RU", "US", "SG", "EU")
        val index = order.indexOf(code)
        return if (index >= 0) index else Int.MAX_VALUE
    }

    private fun isSelectableLocation(location: VpnLocationOption): Boolean {
        return location.id.toLongOrNull() != null || location.importText.isNotBlank()
    }

    private fun applyLocations(locations: List<VpnLocationOption>, error: String) {
        val safeLocations = locations
            .filter(::isSelectableLocation)
            .ifEmpty { VpnDemoData.unavailableLocations }
        val savedLastRegionId = savedLastConnectedRegionId()
        val savedLastRegionTitle = savedLastConnectedRegionTitle()
        val savedRegion = savedSelectedRegion()

        _uiState.update { state ->
            val currentSelected = state.selectedLocation.takeIf(::isSelectableLocation)
            val selected = safeLocations.firstOrNull { currentSelected != null && it.id == currentSelected.id }
                ?: safeLocations.firstOrNull { savedLastRegionId.isNotBlank() && it.id == savedLastRegionId }
                ?: safeLocations.firstOrNull { savedLastRegionTitle.isNotBlank() && it.title.equals(savedLastRegionTitle, ignoreCase = true) }
                ?: safeLocations.firstOrNull { savedRegion.isNotBlank() && it.title.equals(savedRegion, ignoreCase = true) }
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

    private fun savedSelectedRegion(): String {
        return MmkvManager.decodeSettingsString(SKRYON_SELECTED_REGION_PREF, "")
            ?.trim()
            .orEmpty()
    }

    private fun savedLastConnectedRegionId(): String {
        return MmkvManager.decodeSettingsString(SKRYON_LAST_CONNECTED_REGION_ID_PREF, "")
            ?.trim()
            .orEmpty()
    }

    private fun savedLastConnectedRegionTitle(): String {
        return MmkvManager.decodeSettingsString(SKRYON_LAST_CONNECTED_REGION_TITLE_PREF, "")
            ?.trim()
            .orEmpty()
    }

    private fun saveLastConnectedLocation(location: VpnLocationOption) {
        MmkvManager.encodeSettings(SKRYON_LAST_CONNECTED_REGION_ID_PREF, location.id)
        MmkvManager.encodeSettings(SKRYON_LAST_CONNECTED_REGION_TITLE_PREF, location.title)
        MmkvManager.encodeSettings(SKRYON_SELECTED_REGION_PREF, location.title)
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

    fun onLocationSelected(locationIdOrTitle: String) {
        if (_uiState.value.connectionState != VpnConnectionState.Disconnected) {
            return
        }

        val selected = _uiState.value.locations.firstOrNull {
            it.id == locationIdOrTitle || it.title == locationIdOrTitle
        } ?: return

        MmkvManager.encodeSettings(SKRYON_SELECTED_REGION_PREF, selected.title)
        VpnUiDebugLogger.log(
            hypothesisId = "H5",
            location = "VpnMainViewModel.kt:onLocationSelected",
            message = "location selected",
            data = JSONObject()
                .put("location", selected.title)
                .put("serverId", selected.id),
        )
        _uiState.update { state ->
            state.copy(
                selectedLocation = selected,
                locationsError = "",
            )
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
                onSuccess = { attempt ->
                    val payload = attempt.payload
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

                    saveLastConnectedLocation(attempt.location)
                    _uiState.update { state ->
                        state.copy(
                            selectedLocation = attempt.location,
                            locationsError = "",
                        )
                    }

                    AgentDebugNdjsonLogger.log(
                        hypothesisId = "H2",
                        location = "VpnMainViewModel.kt:onConnectClick",
                        message = "premium_waiting_for_service_confirmation",
                        runId = "service-state-sync",
                        data = JSONObject()
                            .put("serverId", payload.serverId)
                            .put("city", payload.city)
                            .put("selectedGuid", payload.selectedGuid)
                            .put("locationId", attempt.location.id)
                            .put("locationTitle", attempt.location.title),
                    )
                    awaitServiceStartConfirmation()
                },
                onFailure = { error ->
                    waitingForStartConfirmation = false
                    setDisconnectedWithError("Не удалось подключиться к выбранному региону")
                    VpnUiDebugLogger.log(
                        hypothesisId = "H7",
                        location = "VpnMainViewModel.kt:onConnectClick",
                        message = "connect failed for selected region",
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

    private suspend fun connectSelectedLocation(state: VpnMainUiState): Result<ConnectAttemptResult> {
        val normalizedState = state.copy(activationKey = state.activationKey.ifBlank { savedActivationCode().ifBlank { DEFAULT_ACCESS_KEY } })
        val selectedLocation = normalizedState.selectedLocation.takeIf(::isSelectableLocation)
            ?: return Result.failure(IllegalStateException("missing_selected_region"))

        val result = connectLocationCandidate(normalizedState.activationKey, selectedLocation)
        if (result.isFailure) {
            VpnUiDebugLogger.log(
                hypothesisId = "H12",
                location = "VpnMainViewModel.kt:connectSelectedLocation",
                message = "selected region unavailable",
                data = JSONObject()
                    .put("locationId", selectedLocation.id)
                    .put("locationTitle", selectedLocation.title)
                    .put("error", result.exceptionOrNull()?.message ?: "unknown"),
            )
        }
        return result
    }

    private suspend fun connectLocationCandidate(accessKey: String, location: VpnLocationOption): Result<ConnectAttemptResult> {
        val serverId = location.id.toLongOrNull()
        if (serverId != null) {
            val payload = EmeryVpnSync.connectToServer(accessKey, serverId).getOrElse { error ->
                return Result.failure(error)
            }
            return Result.success(ConnectAttemptResult(payload, location))
        }

        val importText = location.importText.trim()
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
                ConnectAttemptResult(
                    payload = EmeryVpnSync.ConnectServerResult(
                        serverId = -1L,
                        city = location.title,
                        selectedGuid = selectedGuid,
                    ),
                    location = location,
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
        regionEventsJob?.cancel()
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

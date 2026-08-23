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
import com.v2ray.ang.handler.EmeryAccessManager
import com.v2ray.ang.handler.EmeryVpnSync
import com.v2ray.ang.handler.MmkvManager
import com.v2ray.ang.handler.RegionalPolicyManager
import com.v2ray.ang.handler.V2RayServiceManager
import com.v2ray.ang.network.EmeryBackendClient
import com.v2ray.ang.network.EmeryPoolClient
import com.v2ray.ang.ui.premium.SKRYON_ACTIVATION_CODE_PREF
import com.v2ray.ang.ui.premium.SKRYON_ACTIVATION_CONFIG_PREF
import com.v2ray.ang.ui.premium.SKRYON_CONFIG_REVISION_PREF
import com.v2ray.ang.ui.premium.SKRYON_SERVER_GUID_PREF
import com.v2ray.ang.ui.premium.SKRYON_SERVER_ID_PREF
import com.v2ray.ang.ui.premium.SKRYON_ACTIVATION_CODE_LENGTH
import com.v2ray.ang.ui.premium.activateSkryonCode
import com.v2ray.ang.ui.premium.clearActivatedSkryonConfig
import com.v2ray.ang.ui.premium.formatSkryonActivationCode
import com.v2ray.ang.ui.premium.saveActivatedSkryonConfig
import com.v2ray.ang.security.EmeryDeviceGateConfig
import com.v2ray.ang.ui.premium.sanitizeSkryonActivationCode
import com.v2ray.ang.ui.premium.syncSkryonConfig
import com.v2ray.ang.util.AgentDebugNdjsonLogger
import com.v2ray.ang.util.MessageUtil
import com.v2ray.ang.util.Utils
import java.net.URLDecoder
import java.nio.charset.StandardCharsets
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
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

class VpnMainViewModel(application: Application) : AndroidViewModel(application) {

    private companion object {
        const val DEFAULT_ACCESS_KEY = "DEV"
        const val DEFAULT_REGION_TITLE = "Регион"
        const val SERVICE_STATE_RECHECK_DELAY_MS = 1_500L
        const val CONFIG_SYNC_RETRY_DELAY_MS = 3_000L
        const val CONFIG_SYNC_ACCESS_RETRY_DELAY_MS = 30_000L
    }

    private val _uiState = MutableStateFlow(
        VpnMainUiState(
            activationKey = savedActivationCode().ifBlank { DEFAULT_ACCESS_KEY },
        )
    )
    val uiState: StateFlow<VpnMainUiState> = _uiState.asStateFlow()

    private var connectJob: Job? = null
    private var timerJob: Job? = null
    private var serversJob: Job? = null
    private var configSyncJob: Job? = null
    private var serviceStateRecheckJob: Job? = null
    private var serviceReceiverRegistered = false

    private val serviceStateReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context?, intent: Intent?) {
            when (intent?.getIntExtra("key", 0)) {
                AppConfig.MSG_STATE_RUNNING,
                AppConfig.MSG_STATE_START_SUCCESS -> setConnectedFromService()

                AppConfig.MSG_STATE_NOT_RUNNING,
                AppConfig.MSG_STATE_STOP_SUCCESS -> setDisconnectedFromService()

                AppConfig.MSG_STATE_START_FAILURE -> {
                    setDisconnectedWithError("Не удалось запустить VPN-сервис")
                }
            }
        }
    }

    init {
        registerServiceStateReceiver()
        requestServiceState()
        refreshLocations()
        startSkryonConfigSync()
    }

    private fun registerServiceStateReceiver() {
        if (serviceReceiverRegistered) return

        val application = getApplication<Application>()
        runCatching {
            ContextCompat.registerReceiver(
                application,
                serviceStateReceiver,
                IntentFilter(AppConfig.BROADCAST_ACTION_ACTIVITY),
                Utils.receiverFlags(),
            )
        }.onSuccess {
            serviceReceiverRegistered = true
        }.onFailure { error ->
            VpnUiDebugLogger.log(
                hypothesisId = "H10",
                location = "VpnMainViewModel.kt:registerServiceStateReceiver",
                message = "premium service-state receiver registration failed",
                data = JSONObject().put("error", error.message ?: "unknown"),
            )
        }
    }

    private fun requestServiceState() {
        MessageUtil.sendMsg2Service(
            getApplication(),
            AppConfig.MSG_REGISTER_CLIENT,
            "",
        )
    }

    private fun scheduleServiceStateRecheck() {
        serviceStateRecheckJob?.cancel()
        serviceStateRecheckJob = viewModelScope.launch {
            delay(SERVICE_STATE_RECHECK_DELAY_MS)
            if (_uiState.value.connectionState == VpnConnectionState.Connecting) {
                requestServiceState()
            }
        }
    }

    private fun setConnectedFromService() {
        connectJob?.cancel()
        serviceStateRecheckJob?.cancel()
        _uiState.update { state ->
            state.copy(
                activationKey = state.activationKey.ifBlank {
                    savedActivationCode().ifBlank { DEFAULT_ACCESS_KEY }
                },
                connectionState = VpnConnectionState.Connected,
                locationsError = "",
            )
        }
        startTimer()
        VpnUiDebugLogger.log(
            hypothesisId = "H10",
            location = "VpnMainViewModel.kt:setConnectedFromService",
            message = "premium UI synchronized with running VPN service",
            data = JSONObject(),
        )
    }

    private fun setDisconnectedFromService() {
        connectJob?.cancel()
        timerJob?.cancel()
        serviceStateRecheckJob?.cancel()
        _uiState.update { state ->
            state.copy(
                activationKey = state.activationKey.ifBlank {
                    savedActivationCode().ifBlank { DEFAULT_ACCESS_KEY }
                },
                connectionState = VpnConnectionState.Disconnected,
                elapsedSeconds = 0L,
                locationsError = "",
            )
        }
    }

    private fun startSkryonConfigSync() {
        configSyncJob?.cancel()
        configSyncJob = viewModelScope.launch {
            while (isActive) {
                val code = savedActivationCode()
                if (code.isBlank()) {
                    delay(CONFIG_SYNC_RETRY_DELAY_MS)
                    continue
                }

                val knownRevision = MmkvManager.decodeSettingsLong(
                    SKRYON_CONFIG_REVISION_PREF,
                    -1L,
                )
                val result = syncSkryonConfig(
                    context = getApplication(),
                    code = code,
                    revision = knownRevision,
                )
                if (!isActive) return@launch

                if (!result.ok) {
                    if (result.reason in setOf("not_found", "expired", "banned", "not_bound", "upgrade_required")) {
                        removeSyncedSkryonConfig(result.error.ifBlank { "Доступ к серверу отключён" })
                        delay(CONFIG_SYNC_ACCESS_RETRY_DELAY_MS)
                    } else {
                        delay(CONFIG_SYNC_RETRY_DELAY_MS)
                    }
                    continue
                }

                MmkvManager.encodeSettings(SKRYON_CONFIG_REVISION_PREF, result.revision)
                if (result.config.isBlank()) {
                    removeSyncedSkryonConfig("Сервер удалён администратором")
                } else {
                    applySyncedSkryonConfig(result.config, result.serverId)
                }
            }
        }
    }

    private fun applySyncedSkryonConfig(config: String, serverId: Long) {
        val savedConfig = MmkvManager.decodeSettingsString(SKRYON_ACTIVATION_CONFIG_PREF, "")
            ?.trim()
            .orEmpty()
        if (savedConfig == config) {
            MmkvManager.encodeSettings(SKRYON_SERVER_ID_PREF, serverId)
            val location = savedSkryonConfigLocation()
            if (location != null && _uiState.value.selectedLocation.id == "unavailable") {
                applyLocations(listOf(location), "")
            }
            return
        }

        if (savedConfig.isNotBlank() && V2RayServiceManager.isRunning()) {
            V2RayServiceManager.stopVService(getApplication())
        }
        MmkvManager.removeServerViaSubid(AppConfig.EMERY_BACKEND_SUBSCRIPTION_ID)

        val guid = runCatching { saveActivatedSkryonConfig(config) }
            .getOrElse {
                setDisconnectedWithError("Не удалось обновить сервер")
                return
            }
        MmkvManager.encodeSettings(SKRYON_ACTIVATION_CONFIG_PREF, config)
        MmkvManager.encodeSettings(SKRYON_SERVER_GUID_PREF, guid)
        MmkvManager.encodeSettings(SKRYON_SERVER_ID_PREF, serverId)

        savedSkryonConfigLocation()?.let { location ->
            applyLocations(listOf(location), "")
        }
        timerJob?.cancel()
        _uiState.update { state ->
            state.copy(
                connectionState = VpnConnectionState.Disconnected,
                elapsedSeconds = 0L,
                locationsError = "",
            )
        }
    }

    private fun removeSyncedSkryonConfig(message: String) {
        if (V2RayServiceManager.isRunning()) {
            V2RayServiceManager.stopVService(getApplication())
        }
        clearActivatedSkryonConfig()
        timerJob?.cancel()

        val unavailable = VpnDemoData.unavailableLocations
        _uiState.update { state ->
            state.copy(
                locations = unavailable,
                selectedLocation = unavailable.first(),
                connectionState = VpnConnectionState.Disconnected,
                elapsedSeconds = 0L,
                locationsLoading = false,
                locationsError = message,
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
                    val updateMessage = servers
                        .firstOrNull { it.healthStatus == "upgrade_required" }
                        ?.city
                        ?.trim()
                        .orEmpty()
                    if (updateMessage.isNotBlank()) {
                        removeSyncedSkryonConfig(updateMessage)
                    } else {
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

    suspend fun activateReplacementCode(rawCode: String): Result<Unit> {
        if (_uiState.value.connectionState != VpnConnectionState.Disconnected) {
            return Result.failure(IllegalStateException("Сначала отключите VPN"))
        }

        val normalized = sanitizeSkryonActivationCode(rawCode)
        if (normalized.length != SKRYON_ACTIVATION_CODE_LENGTH) {
            return Result.failure(IllegalArgumentException("Введите код полностью"))
        }
        val formatted = formatSkryonActivationCode(normalized)
        val activation = activateSkryonCode(
            context = getApplication(),
            code = normalized,
            formattedCode = formatted,
        )
        if (!activation.ok) {
            return Result.failure(
                IllegalStateException(activation.error.ifBlank { "Ошибка активации" }),
            )
        }

        return runCatching {
            val guid = saveActivatedSkryonConfig(activation.config)
            val confirmedCode = activation.code.ifBlank { formatted }
            MmkvManager.encodeSettings(SKRYON_ACTIVATION_CODE_PREF, confirmedCode)
            MmkvManager.encodeSettings(SKRYON_ACTIVATION_CONFIG_PREF, activation.config)
            MmkvManager.encodeSettings(SKRYON_SERVER_GUID_PREF, guid)
            MmkvManager.encodeSettings(SKRYON_SERVER_ID_PREF, activation.serverId)
            MmkvManager.encodeSettings(SKRYON_CONFIG_REVISION_PREF, activation.revision)

            val location = VpnLocationOption(
                id = "skryon-activated",
                title = titleFromConfigLink(activation.config, 1),
                importText = activation.config,
            )
            _uiState.update { state ->
                state.copy(
                    activationKey = confirmedCode,
                    locations = listOf(location),
                    selectedLocation = location,
                    locationsLoading = false,
                    locationsError = "",
                )
            }
            // Cancel an outstanding long-poll authenticated with the old code;
            // otherwise its late response could restore the previous profile.
            startSkryonConfigSync()
        }.onFailure { error ->
            VpnUiDebugLogger.log(
                hypothesisId = "H13",
                location = "VpnMainViewModel.kt:activateReplacementCode",
                message = "replacement activation failed locally",
                data = JSONObject().put("error", error.message ?: "unknown"),
            )
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
        serviceStateRecheckJob?.cancel()

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
            // The device table is a manual UI action. The connect path performs only a
            // lightweight access refresh and never requires the full device inventory.
            val accessVerification = EmeryBackendClient.fetchProfile(
                accessKey = currentState.activationKey,
                requireDeviceInventory = false,
            )
            accessVerification.fold(
                onSuccess = { profile ->
                    EmeryAccessManager.saveProfile(profile)
                },
                onFailure = { error ->
                    val reason = error.message.orEmpty()
                    if (isBlockingAccessFailure(reason)) {
                        setDisconnectedWithError(deviceAccessError(reason))
                        VpnUiDebugLogger.log(
                            hypothesisId = "H12",
                            location = "VpnMainViewModel.kt:onConnectClick",
                            message = "access denied before VPN start",
                            data = JSONObject().put("error", reason.ifBlank { "unknown" }),
                        )
                        return@launch
                    }

                    VpnUiDebugLogger.log(
                        hypothesisId = "H12",
                        location = "VpnMainViewModel.kt:onConnectClick",
                        message = "optional access refresh unavailable; continuing with activated configuration",
                        data = JSONObject().put("error", reason.ifBlank { "unknown" }),
                    )
                },
            )

            val policyAssets = RegionalPolicyManager.prepareForConnection(getApplication())
            if (policyAssets.isFailure) {
                setDisconnectedWithError("Не удалось обновить список ограничений РФ")
                VpnUiDebugLogger.log(
                    hypothesisId = "H11",
                    location = "VpnMainViewModel.kt:onConnectClick",
                    message = "regional policy data refresh failed",
                    data = JSONObject().put(
                        "error",
                        policyAssets.exceptionOrNull()?.message ?: "unknown",
                    ),
                )
                return@launch
            }

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

                    VpnUiDebugLogger.log(
                        hypothesisId = "H10",
                        location = "VpnMainViewModel.kt:onConnectClick",
                        message = "VPN service start requested; waiting for runtime confirmation",
                        data = JSONObject()
                            .put("serverId", payload.serverId)
                            .put("city", payload.city)
                            .put("selectedGuid", payload.selectedGuid),
                    )
                    scheduleServiceStateRecheck()
                },
                onFailure = { error ->
                    setDisconnectedWithError(vpnConnectError(error.message.orEmpty()))
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

    private fun isBlockingAccessFailure(reason: String): Boolean {
        val normalized = reason.trim().lowercase()
        return normalized in setOf(
            "not_found",
            "expired",
            "banned",
            "blocked",
            "revoked",
            "invalid_or_expired_key",
            "vpn_disabled",
            "upgrade_required",
        )
    }

    private fun deviceAccessError(reason: String): String {
        return when (reason) {
            "device_not_registered", "device_confirmation_missing" ->
                "Это устройство не зарегистрировано для тарифа"
            "device_mismatch", "device_inventory_mismatch" ->
                "Сервер не подтвердил текущее устройство"
            "device_counter_missing", "device_counter_mismatch" ->
                "Сервер не подтвердил список устройств"
            "plan_limit_mismatch" ->
                "Лимит устройств не соответствует тарифу"
            "invalid_or_expired_key", "not_found", "expired", "banned", "blocked", "revoked" ->
                "Код доступа недействителен или истёк"
            "vpn_disabled" ->
                "Доступ к VPN отключён"
            "upgrade_required" ->
                "Требуется обновить приложение"
            "network" ->
                "Не удалось проверить устройство. Проверьте интернет"
            else -> "Не удалось подтвердить доступ этого устройства"
        }
    }

    private fun vpnConnectError(reason: String): String {
        return when (reason) {
            "server_capacity_unavailable" ->
                "В этом регионе пока нет свободных мест. Новый сервер уже подготавливается"
            "device_assignment_region_locked" ->
                "Для устройства уже подготовлен другой сервер. Выберите регион из активированного профиля"
            "assignment_install_in_progress", "assignment_maintenance_in_progress", "assignment_state_changed_retry" ->
                "Персональный доступ обновляется. Повторите через несколько секунд"
            "credential_install_failed", "pool_backend_unreachable" ->
                "Сервер подготавливает персональный доступ. Попробуйте немного позже"
            else -> "Не удалось подключиться к серверу"
        }
    }

    private fun setDisconnectedWithError(message: String) {
        timerJob?.cancel()
        serviceStateRecheckJob?.cancel()
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
            val preparedImportText = runCatching {
                EmeryDeviceGateConfig.prepareImportText(importText)
            }.getOrElse {
                return@withContext Result.failure(IllegalStateException("device_gate_config_invalid"))
            }
            val (count, _) = AngConfigManager.importBatchConfig(
                preparedImportText,
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
        timerJob?.cancel()
        serviceStateRecheckJob?.cancel()
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
                locationsError = "",
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
        configSyncJob?.cancel()
        serviceStateRecheckJob?.cancel()

        if (serviceReceiverRegistered) {
            MessageUtil.sendMsg2Service(getApplication(), AppConfig.MSG_UNREGISTER_CLIENT, "")
            runCatching {
                getApplication<Application>().unregisterReceiver(serviceStateReceiver)
            }
            serviceReceiverRegistered = false
        }

        super.onCleared()
    }
}

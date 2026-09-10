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
import com.v2ray.ang.security.EmeryDeviceGateConfig
import com.v2ray.ang.ui.premium.SKRYON_ACTIVATION_CODE_LENGTH
import com.v2ray.ang.ui.premium.SKRYON_ACTIVATION_CODE_PREF
import com.v2ray.ang.ui.premium.SKRYON_ACTIVATION_CONFIG_PREF
import com.v2ray.ang.ui.premium.SKRYON_CONFIG_REVISION_PREF
import com.v2ray.ang.ui.premium.SKRYON_SERVER_GUID_PREF
import com.v2ray.ang.ui.premium.SKRYON_SERVER_ID_PREF
import com.v2ray.ang.ui.premium.activateSkryonCode
import com.v2ray.ang.ui.premium.clearActivatedSkryonConfig
import com.v2ray.ang.ui.premium.formatSkryonActivationCode
import com.v2ray.ang.ui.premium.sanitizeSkryonActivationCode
import com.v2ray.ang.ui.premium.saveActivatedSkryonConfig
import com.v2ray.ang.ui.premium.syncSkryonConfig
import com.v2ray.ang.util.AgentDebugNdjsonLogger
import com.v2ray.ang.util.MessageUtil
import com.v2ray.ang.util.Utils
import java.net.URLDecoder
import java.nio.charset.StandardCharsets
import kotlinx.coroutines.CompletableDeferred
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
        const val STALE_RUNTIME_STOP_TIMEOUT_MS = 10_000L
        const val STALE_RUNTIME_SETTLE_DELAY_MS = 300L
        const val CONFIG_SYNC_RETRY_DELAY_MS = 3_000L
        const val CONFIG_SYNC_ACCESS_RETRY_DELAY_MS = 30_000L
        const val TUNNEL_VERIFY_TIMEOUT_MS = 12_000L
    }

    private val _uiState = MutableStateFlow(
        VpnMainUiState(
            activationKey = savedActivationCode().ifBlank { DEFAULT_ACCESS_KEY },
        )
    )
    val uiState: StateFlow<VpnMainUiState> = _uiState.asStateFlow()

    private val tunnelVerifier = VpnTunnelTrafficVerifier(application)

    private var connectJob: Job? = null
    private var timerJob: Job? = null
    private var serversJob: Job? = null
    private var configSyncJob: Job? = null
    private var serviceStateRecheckJob: Job? = null
    private var trafficVerificationJob: Job? = null
    private var serviceReceiverRegistered = false

    @Volatile
    private var daemonRunning: Boolean? = null
    private var stopWaiter: CompletableDeferred<Boolean>? = null
    private var stateWaiter: CompletableDeferred<Boolean>? = null
    private var connectionAttempt: Long = 0L
    private var preserveConnectionFailure = false
    private var stopRequested = false
    private var awaitingStartConfirmation = false

    private val serviceStateReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context?, intent: Intent?) {
            val key = intent?.getIntExtra("key", 0) ?: return
            VpnUiDebugLogger.log(
                hypothesisId = "H-LIFECYCLE",
                location = "VpnMainViewModel.kt:serviceStateReceiver",
                message = "premium service event received",
                runId = "vpn-lifecycle",
                data = JSONObject()
                    .put("key", key)
                    .put("event", serviceEventName(key))
                    .put("state", _uiState.value.connectionState.name),
            )

            when (key) {
                AppConfig.MSG_STATE_RUNNING,
                AppConfig.MSG_STATE_START_SUCCESS -> {
                    daemonRunning = true
                    stopRequested = false
                    stateWaiter?.complete(true)
                    stateWaiter = null
                    onDaemonStarted()
                }

                AppConfig.MSG_STATE_NOT_RUNNING,
                AppConfig.MSG_STATE_STOP_SUCCESS -> {
                    val wasExpectedStop = stopRequested || stopWaiter != null
                    daemonRunning = false
                    stopRequested = false
                    stateWaiter?.complete(false)
                    stateWaiter = null
                    stopWaiter?.complete(true)
                    stopWaiter = null
                    onDaemonStopped(wasExpectedStop)
                }

                AppConfig.MSG_STATE_START_FAILURE -> {
                    daemonRunning = false
                    awaitingStartConfirmation = false
                    stateWaiter?.complete(false)
                    stateWaiter = null
                    trafficVerificationJob?.cancel()
                    if (_uiState.value.connectionState == VpnConnectionState.Connecting) {
                        setDisconnectedWithError("Не удалось запустить VPN-сервис")
                    }
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

    private suspend fun refreshDaemonStateIfUnknown() {
        if (daemonRunning != null) return
        val waiter = CompletableDeferred<Boolean>()
        stateWaiter = waiter
        requestServiceState()
        withTimeoutOrNull(1_500L) { waiter.await() }
        if (stateWaiter === waiter) {
            stateWaiter = null
        }
        VpnUiDebugLogger.log(
            hypothesisId = "H-LIFECYCLE",
            location = "VpnMainViewModel.kt:refreshDaemonStateIfUnknown",
            message = "daemon state refresh completed",
            runId = "vpn-connect",
            data = JSONObject()
                .put("runtimeRunning", daemonRunning == true)
                .put("reason", if (daemonRunning == null) "no_service_response" else "service_response"),
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

    private fun onDaemonStarted() {
        serviceStateRecheckJob?.cancel()
        if (_uiState.value.connectionState == VpnConnectionState.Disconnected && connectionAttempt == 0L) {
            _uiState.update { state ->
                state.copy(
                    connectionState = VpnConnectionState.Connecting,
                    elapsedSeconds = 0L,
                    locationsError = "",
                )
            }
            VpnUiDebugLogger.log(
                hypothesisId = "H-LIFECYCLE",
                location = "VpnMainViewModel.kt:onDaemonStarted",
                message = "existing VPN runtime detected; verifying tunnel traffic",
                runId = "vpn-lifecycle",
                data = JSONObject().put("attempt", connectionAttempt),
            )
        }
        if (_uiState.value.connectionState != VpnConnectionState.Connecting) {
            return
        }

        if (connectionAttempt > 0L && !awaitingStartConfirmation) {
            VpnUiDebugLogger.log(
                hypothesisId = "H-LIFECYCLE",
                location = "VpnMainViewModel.kt:onDaemonStarted",
                message = "runtime event observed before current service start; waiting",
                runId = "vpn-connect",
                data = JSONObject().put("attempt", connectionAttempt),
            )
            return
        }

        val attempt = connectionAttempt
        if (trafficVerificationJob?.isActive == true) return

        VpnUiDebugLogger.log(
            hypothesisId = "H-TRAFFIC",
            location = "VpnMainViewModel.kt:onDaemonStarted",
            message = "VPN core started; verifying tunnel traffic",
            runId = "vpn-connect",
            data = JSONObject().put("attempt", attempt),
        )

        trafficVerificationJob = viewModelScope.launch {
            val result = withTimeoutOrNull(TUNNEL_VERIFY_TIMEOUT_MS) {
                tunnelVerifier.verify { probe ->
                    VpnUiDebugLogger.log(
                        hypothesisId = "H-TRAFFIC",
                        location = "VpnTunnelTrafficVerifier.kt:verify",
                        message = "tunnel traffic probe",
                        runId = "vpn-connect",
                        data = JSONObject()
                            .put("attempt", probe.attempt)
                            .put("stage", probe.stage)
                            .put("reason", probe.reason)
                            .put("quality", if (probe.ok) "verified" else "pending"),
                    )
                }
            }

            if (attempt != connectionAttempt || _uiState.value.connectionState != VpnConnectionState.Connecting) {
                return@launch
            }

            if (result?.ok == true) {
                awaitingStartConfirmation = false
                preserveConnectionFailure = false
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
                    hypothesisId = "H-TRAFFIC",
                    location = "VpnMainViewModel.kt:onDaemonStarted",
                    message = "tunnel traffic verified",
                    runId = "vpn-connect",
                    data = JSONObject()
                        .put("attempt", result.attempt)
                        .put("reason", result.reason)
                        .put("quality", "verified"),
                )
            } else {
                awaitingStartConfirmation = false
                preserveConnectionFailure = true
                val reason = result?.reason ?: "verification_timeout"
                VpnUiDebugLogger.log(
                    hypothesisId = "H-TRAFFIC",
                    location = "VpnMainViewModel.kt:onDaemonStarted",
                    message = "tunnel traffic verification failed",
                    runId = "vpn-connect",
                    data = JSONObject()
                        .put("attempt", result?.attempt ?: 0)
                        .put("reason", reason)
                        .put("quality", "failed"),
                )
                setDisconnectedWithError("VPN запущен, но трафик через туннель не проходит")
                if (daemonRunning == true) {
                    V2RayServiceManager.stopVService(getApplication())
                }
            }
        }
    }

    private fun onDaemonStopped(wasExpectedStop: Boolean) {
        if (wasExpectedStop) {
            VpnUiDebugLogger.log(
                hypothesisId = "H-LIFECYCLE",
                location = "VpnMainViewModel.kt:onDaemonStopped",
                message = "previous VPN runtime stopped; reconnect may continue",
                runId = "vpn-connect",
                data = JSONObject().put("attempt", connectionAttempt),
            )
            return
        }

        trafficVerificationJob?.cancel()
        timerJob?.cancel()

        if (_uiState.value.connectionState == VpnConnectionState.Connecting) {
            VpnUiDebugLogger.log(
                hypothesisId = "H-LIFECYCLE",
                location = "VpnMainViewModel.kt:onDaemonStopped",
                message = "service stopped while connection attempt was active",
                runId = "vpn-connect",
                data = JSONObject().put("attempt", connectionAttempt),
            )
            if (!preserveConnectionFailure) {
                setDisconnectedWithError("VPN-сервис остановился во время подключения")
            }
            return
        }

        _uiState.update { state ->
            state.copy(
                activationKey = state.activationKey.ifBlank {
                    savedActivationCode().ifBlank { DEFAULT_ACCESS_KEY }
                },
                connectionState = VpnConnectionState.Disconnected,
                elapsedSeconds = 0L,
                locationsError = if (preserveConnectionFailure) state.locationsError else "",
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

        if (savedConfig.isNotBlank() && daemonRunning == true) {
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
        if (daemonRunning == true) {
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
        if (_uiState.value.connectionState != VpnConnectionState.Disconnected || daemonRunning == true) {
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

        connectionAttempt += 1L
        val attempt = connectionAttempt
        preserveConnectionFailure = false
        awaitingStartConfirmation = false
        connectJob?.cancel()
        timerJob?.cancel()
        trafficVerificationJob?.cancel()
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
            runId = "vpn-connect",
            data = JSONObject().put("attempt", attempt),
        )

        connectJob = viewModelScope.launch {
            refreshDaemonStateIfUnknown()

            val accessVerification = EmeryBackendClient.fetchProfile(
                accessKey = currentState.activationKey,
                requireDeviceInventory = false,
            )
            accessVerification.fold(
                onSuccess = { profile ->
                    EmeryAccessManager.saveProfile(profile)
                    VpnUiDebugLogger.log(
                        hypothesisId = "H12",
                        location = "VpnMainViewModel.kt:onConnectClick",
                        message = "access refresh succeeded",
                        runId = "vpn-connect",
                        data = JSONObject().put("attempt", attempt),
                    )
                },
                onFailure = { error ->
                    val reason = error.message.orEmpty()
                    if (isBlockingAccessFailure(reason)) {
                        setDisconnectedWithError(deviceAccessError(reason))
                        VpnUiDebugLogger.log(
                            hypothesisId = "H12",
                            location = "VpnMainViewModel.kt:onConnectClick",
                            message = "access denied before VPN start",
                            runId = "vpn-connect",
                            data = JSONObject().put("error", reason.ifBlank { "unknown" }).put("attempt", attempt),
                        )
                        return@launch
                    }

                    VpnUiDebugLogger.log(
                        hypothesisId = "H12",
                        location = "VpnMainViewModel.kt:onConnectClick",
                        message = "optional access refresh unavailable; continuing with activated configuration",
                        runId = "vpn-connect",
                        data = JSONObject().put("error", reason.ifBlank { "unknown" }).put("attempt", attempt),
                    )
                },
            )

            val policyAssets = RegionalPolicyManager.prepareForConnection(getApplication())
            if (policyAssets.isFailure) {
                setDisconnectedWithError("Не удалось подготовить региональную политику")
                VpnUiDebugLogger.log(
                    hypothesisId = "H11",
                    location = "VpnMainViewModel.kt:onConnectClick",
                    message = "regional policy data refresh failed",
                    runId = "vpn-connect",
                    data = JSONObject()
                        .put("error", policyAssets.exceptionOrNull()?.message ?: "unknown")
                        .put("attempt", attempt),
                )
                return@launch
            }

            val result = connectSelectedLocation(currentState)
            result.fold(
                onSuccess = { payload ->
                    if (!stopStaleRuntimeBeforeProfileStart(attempt)) {
                        setDisconnectedWithError("Не удалось остановить предыдущий VPN-сеанс")
                        VpnUiDebugLogger.log(
                            hypothesisId = "H8",
                            location = "VpnMainViewModel.kt:onConnectClick",
                            message = "stale VPN runtime did not stop before profile start",
                            runId = "vpn-connect",
                            data = JSONObject().put("attempt", attempt),
                        )
                        return@fold
                    }
                    if (attempt != connectionAttempt || _uiState.value.connectionState != VpnConnectionState.Connecting) {
                        return@fold
                    }

                    awaitingStartConfirmation = true
                    val serviceStartRequested = try {
                        startVpnService(payload.selectedGuid)
                    } catch (e: Exception) {
                        VpnUiDebugLogger.log(
                            hypothesisId = "H8",
                            location = "VpnMainViewModel.kt:onConnectClick",
                            message = "vpn service start threw",
                            runId = "vpn-connect",
                            data = JSONObject().put("error", e.message ?: "unknown").put("attempt", attempt),
                        )
                        false
                    }
                    if (!serviceStartRequested) {
                        awaitingStartConfirmation = false
                        setDisconnectedWithError("Не удалось запустить VPN-сервис")
                        VpnUiDebugLogger.log(
                            hypothesisId = "H8",
                            location = "VpnMainViewModel.kt:onConnectClick",
                            message = "vpn service start request failed",
                            runId = "vpn-connect",
                            data = JSONObject()
                                .put("serverId", payload.serverId)
                                .put("city", payload.city)
                                .put("attempt", attempt),
                        )
                        return@fold
                    }

                    VpnUiDebugLogger.log(
                        hypothesisId = "H10",
                        location = "VpnMainViewModel.kt:onConnectClick",
                        message = "VPN service start requested; waiting for runtime confirmation",
                        runId = "vpn-connect",
                        data = JSONObject()
                            .put("serverId", payload.serverId)
                            .put("city", payload.city)
                            .put("attempt", attempt),
                    )
                    scheduleServiceStateRecheck()
                },
                onFailure = { error ->
                    setDisconnectedWithError(vpnConnectError(error.message.orEmpty()))
                    VpnUiDebugLogger.log(
                        hypothesisId = "H7",
                        location = "VpnMainViewModel.kt:onConnectClick",
                        message = "connect failed",
                        runId = "vpn-connect",
                        data = JSONObject()
                            .put("serverId", currentState.selectedLocation.id)
                            .put("error", error.message ?: "unknown")
                            .put("attempt", attempt),
                    )
                },
            )
        }
    }

    private suspend fun stopStaleRuntimeBeforeProfileStart(attempt: Long): Boolean {
        if (daemonRunning != true) {
            VpnUiDebugLogger.log(
                hypothesisId = "H8",
                location = "VpnMainViewModel.kt:stopStaleRuntimeBeforeProfileStart",
                message = "no stale daemon reported before profile start",
                runId = "vpn-connect",
                data = JSONObject().put("attempt", attempt).put("runtimeRunning", false),
            )
            return true
        }

        val waiter = CompletableDeferred<Boolean>()
        stopWaiter = waiter
        stopRequested = true
        VpnUiDebugLogger.log(
            hypothesisId = "H8",
            location = "VpnMainViewModel.kt:stopStaleRuntimeBeforeProfileStart",
            message = "stopping stale VPN runtime before selected profile start",
            runId = "vpn-connect",
            data = JSONObject().put("attempt", attempt).put("runtimeRunning", true),
        )
        V2RayServiceManager.stopVService(getApplication())
        val stopped = withTimeoutOrNull(STALE_RUNTIME_STOP_TIMEOUT_MS) {
            waiter.await()
        } == true
        if (stopWaiter === waiter) {
            stopWaiter = null
        }
        if (stopped) {
            delay(STALE_RUNTIME_SETTLE_DELAY_MS)
        }
        return stopped
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
        val normalizedState = state.copy(
            activationKey = state.activationKey.ifBlank { savedActivationCode().ifBlank { DEFAULT_ACCESS_KEY } },
        )
        val serverId = normalizedState.selectedLocation.id.toLongOrNull()
        if (serverId != null) {
            return EmeryVpnSync.connectToServer(normalizedState.activationKey, serverId)
        }

        val importText = normalizedState.selectedLocation.importText.trim()
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

            val selectedGuid = MmkvManager.decodeServerList(AppConfig.EMERY_BACKEND_SUBSCRIPTION_ID)
                .firstOrNull()
                .orEmpty()
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
        connectionAttempt += 1L
        preserveConnectionFailure = false
        awaitingStartConfirmation = false
        stopRequested = true
        connectJob?.cancel()
        timerJob?.cancel()
        trafficVerificationJob?.cancel()
        serviceStateRecheckJob?.cancel()

        VpnUiDebugLogger.log(
            hypothesisId = "H2",
            location = "VpnMainViewModel.kt:onDisconnectClick",
            message = "disconnect requested",
            runId = "vpn-lifecycle",
            data = JSONObject().put("state", _uiState.value.connectionState.name).put("runtimeRunning", daemonRunning == true),
        )
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
                        state.copy(
                            activationKey = state.activationKey.ifBlank {
                                savedActivationCode().ifBlank { DEFAULT_ACCESS_KEY }
                            },
                        )
                    }
                }
            }
        }
    }

    private fun serviceEventName(key: Int): String {
        return when (key) {
            AppConfig.MSG_STATE_RUNNING -> "STATE_RUNNING"
            AppConfig.MSG_STATE_NOT_RUNNING -> "STATE_NOT_RUNNING"
            AppConfig.MSG_STATE_START_SUCCESS -> "START_SUCCESS"
            AppConfig.MSG_STATE_START_FAILURE -> "START_FAILURE"
            AppConfig.MSG_STATE_STOP_SUCCESS -> "STOP_SUCCESS"
            else -> "KEY_$key"
        }
    }

    override fun onCleared() {
        connectionAttempt += 1L
        connectJob?.cancel()
        timerJob?.cancel()
        serversJob?.cancel()
        configSyncJob?.cancel()
        serviceStateRecheckJob?.cancel()
        trafficVerificationJob?.cancel()
        stopWaiter?.cancel()
        stateWaiter?.cancel()

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

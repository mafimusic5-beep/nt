package com.v2ray.ang.handler

import android.util.Log
import com.v2ray.ang.AppConfig
import com.v2ray.ang.dto.SubscriptionItem
import com.v2ray.ang.network.EmeryBackendClient
import com.v2ray.ang.network.EmeryPoolClient
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject

object EmeryVpnSync {

    private fun ensureEmerySubscription() {
        val id = AppConfig.EMERY_BACKEND_SUBSCRIPTION_ID
        if (MmkvManager.decodeSubscription(id) != null) {
            return
        }
        val item = SubscriptionItem()
        item.remarks = "Skryon Pool"
        item.url = ""
        item.enabled = false
        item.autoUpdate = false
        MmkvManager.encodeSubscription(id, item)
    }

    private fun ensureEmeryServerSelected(): Boolean {
        val subId = AppConfig.EMERY_BACKEND_SUBSCRIPTION_ID
        val servers = MmkvManager.decodeServerList(subId)
        if (servers.isEmpty()) {
            Log.e(AppConfig.TAG, "Emery sync: no servers in Emery subscription")
            return false
        }
        for (guid in servers) {
            val config = MmkvManager.decodeServerConfig(guid)
            if (config != null) {
                MmkvManager.setSelectServer(guid)
                Log.i(AppConfig.TAG, "Emery sync: selected server $guid (${config.remarks})")
                return true
            }
        }
        Log.e(AppConfig.TAG, "Emery sync: all servers in Emery subscription are invalid")
        return false
    }

    data class ConnectServerResult(
        val serverId: Long,
        val city: String,
        val selectedGuid: String,
    )

    /**
     * Activation sync for the Skryon/v2rayNG model:
     * one paid access key gives access to the whole public server pool, not to a personal VPS.
     *
     * New backend contract, preferred:
     *   GET /api/v1/vpn/pool/config
     *   returns either { importText: "vless://...\nvless://..." } or a JSON list of online servers.
     *
     * Legacy fallback:
     *   GET /vpn/config
     *   keeps older single-allocation backend builds working while the pool backend is being deployed.
     */
    suspend fun syncProfileAndVpnConfig(accessKey: String): Result<EmeryAccessProfile> = withContext(Dispatchers.IO) {
        Log.i(AppConfig.TAG, "EmerySync[H1]: starting sync, baseUrl=${EmeryApiConfig.baseUrl()}")
        val profileResult = EmeryBackendClient.fetchProfile(accessKey)
        val profile = profileResult.getOrElse {
            Log.e(AppConfig.TAG, "EmerySync[H1]: fetchProfile failed: ${it.message}")
            return@withContext Result.failure(it)
        }
        EmeryAccessManager.saveProfile(profile)

        ensureEmerySubscription()

        val poolImportText = EmeryPoolClient.fetchPoolImportText(accessKey).getOrElse { poolError ->
            Log.w(AppConfig.TAG, "Emery sync: pool config unavailable (${poolError.message}), trying legacy config")
            val legacyResult = EmeryBackendClient.fetchVpnConfigImportText(accessKey)
            legacyResult.getOrElse { legacyError ->
                when (legacyError.message) {
                    "no_allocation", "no_import_text", "no_vpn_config", "no_active_allocation", "vpn_disabled" -> {
                        Log.w(AppConfig.TAG, "Emery sync: no VPN config yet (${legacyError.message}), keeping activation successful")
                        return@withContext Result.success(profile)
                    }
                    "network" -> {
                        Log.w(AppConfig.TAG, "Emery sync: VPN config fetch failed (network), keeping activation successful")
                        return@withContext Result.success(profile)
                    }
                    else -> {
                        Log.w(AppConfig.TAG, "Emery sync: VPN import skipped (${legacyError.message})")
                        return@withContext Result.failure(IllegalStateException("import_failed: ${legacyError.message}"))
                    }
                }
            }
        }

        val (count, _) = AngConfigManager.importBatchConfig(
            poolImportText,
            AppConfig.EMERY_BACKEND_SUBSCRIPTION_ID,
            append = false,
        )
        Log.i(AppConfig.TAG, "Emery sync: imported $count Skryon pool profile(s)")

        if (count <= 0) {
            Log.e(AppConfig.TAG, "EmerySync[H2]: import returned count=$count, failing")
            return@withContext Result.failure(IllegalStateException("import_failed"))
        }

        if (!ensureEmeryServerSelected()) {
            Log.e(AppConfig.TAG, "EmerySync[H3]: ensureEmeryServerSelected returned false")
            return@withContext Result.failure(IllegalStateException("server_not_selected"))
        }

        Log.i(AppConfig.TAG, "EmerySync: sync complete, count=$count, selected=${MmkvManager.getSelectServer()}")
        Result.success(profile)
    }

    suspend fun connectToServer(accessKey: String, serverId: Long): Result<ConnectServerResult> = withContext(Dispatchers.IO) {
        ensureEmerySubscription()
        val connectResult = EmeryBackendClient.connectServer(accessKey, serverId)
        val payload = connectResult.getOrElse { err ->
            val code = when (err.message) {
                "server_config_unavailable" -> ManualDiagnosticCodes.SERVER_CONFIG_UNAVAILABLE
                "network" -> ManualDiagnosticCodes.SERVER_LIST_FETCH_FAILED
                else -> ManualDiagnosticCodes.VPN_SERVICE_START_FAILED
            }
            ManualModeDiagnostics.reportError(
                code = code,
                message = "Failed to fetch server config",
                source = "EmeryVpnSync.connectToServer",
                details = "serverId=$serverId reason=${err.message}",
            )
            ManualModeDebugLogger.log(
                hypothesisId = "H2",
                location = "EmeryVpnSync.kt:connectToServer",
                message = "connect endpoint failed",
                data = JSONObject()
                    .put("serverId", serverId)
                    .put("reason", err.message ?: "unknown"),
            )
            return@withContext Result.failure(err)
        }

        val (count, _) = AngConfigManager.importBatchConfig(
            payload.importText,
            AppConfig.EMERY_BACKEND_SUBSCRIPTION_ID,
            append = false,
        )
        if (count <= 0) {
            ManualModeDiagnostics.reportError(
                code = ManualDiagnosticCodes.VPN_IMPORT_FAILED,
                message = "Import returned zero profiles",
                source = "EmeryVpnSync.connectToServer",
                details = "serverId=$serverId",
            )
            return@withContext Result.failure(IllegalStateException("import_failed"))
        }

        val selectedGuid = MmkvManager.decodeServerList(AppConfig.EMERY_BACKEND_SUBSCRIPTION_ID).firstOrNull().orEmpty()
        if (selectedGuid.isBlank()) {
            ManualModeDiagnostics.reportError(
                code = ManualDiagnosticCodes.SELECTED_SERVER_MISSING,
                message = "Imported profile not selected",
                source = "EmeryVpnSync.connectToServer",
                details = "serverId=$serverId",
            )
            return@withContext Result.failure(IllegalStateException("selected_server_missing"))
        }
        MmkvManager.setSelectServer(selectedGuid)
        ManualModeDiagnostics.clearError()
        ManualModeDiagnostics.recordSuccessStep("Server selected: ${payload.city}")
        ManualModeDebugLogger.log(
            hypothesisId = "H4",
            location = "EmeryVpnSync.kt:connectToServer",
            message = "server imported and selected",
            data = JSONObject()
                .put("serverId", payload.serverId)
                .put("city", payload.city)
                .put("selectedGuid", selectedGuid),
        )
        Result.success(
            ConnectServerResult(
                serverId = payload.serverId,
                city = payload.city,
                selectedGuid = selectedGuid,
            )
        )
    }
}

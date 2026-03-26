package com.v2ray.ang.handler

import android.util.Log
import com.v2ray.ang.AppConfig
import com.v2ray.ang.dto.SubscriptionItem
import com.v2ray.ang.network.EmeryBackendClient
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/**
 * After POST /auth/key: GET /profile, GET /vpn/config, then import via existing [AngConfigManager] / MMKV paths.
 */
object EmeryVpnSync {

    private fun ensureEmerySubscription() {
        val id = AppConfig.EMERY_BACKEND_SUBSCRIPTION_ID
        if (MmkvManager.decodeSubscription(id) != null) {
            return
        }
        val item = SubscriptionItem()
        item.remarks = "Emery VPN"
        item.url = ""
        item.enabled = false
        item.autoUpdate = false
        MmkvManager.encodeSubscription(id, item)
    }

    /**
     * Refreshes profile from backend and imports VPN config when the server returns [import_text].
     * Missing allocation or empty v2ray-compatible payload is non-fatal (user can still open MainActivity).
     */
    suspend fun syncProfileAndVpnConfig(accessKey: String): Result<EmeryAccessProfile> = withContext(Dispatchers.IO) {
        val profileResult = EmeryBackendClient.fetchProfile(accessKey)
        val profile = profileResult.getOrElse { return@withContext Result.failure(it) }
        EmeryAccessManager.saveProfile(profile)

        ensureEmerySubscription()
        val cfgResult = EmeryBackendClient.fetchVpnConfigImportText(accessKey)
        cfgResult.fold(
            onSuccess = { text ->
                val (count, _) = AngConfigManager.importBatchConfig(
                    text,
                    AppConfig.EMERY_BACKEND_SUBSCRIPTION_ID,
                    append = false,
                )
                Log.i(AppConfig.TAG, "Emery sync: imported $count profile(s) into Emery subscription")
            },
            onFailure = { e ->
                when (e.message) {
                    "no_allocation", "no_import_text", "no_vpn_config", "no_active_allocation", "vpn_disabled" ->
                        Log.w(AppConfig.TAG, "Emery sync: skip VPN import (${e.message})")
                    "network" ->
                        Log.w(AppConfig.TAG, "Emery sync: VPN config fetch failed (network), continuing")
                    else ->
                        Log.w(AppConfig.TAG, "Emery sync: VPN import skipped (${e.message})")
                }
            },
        )
        Result.success(profile)
    }
}

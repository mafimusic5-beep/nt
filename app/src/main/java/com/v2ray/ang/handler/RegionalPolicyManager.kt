package com.v2ray.ang.handler

import android.content.Context
import com.v2ray.ang.AppConfig
import com.v2ray.ang.network.EmeryBackendClient
import com.v2ray.ang.ui.premium.SKRYON_ACTIVATION_CODE_PREF
import com.v2ray.ang.ui.premium.SKRYON_SERVER_ID_PREF

internal enum class RegionalPolicyMode(val storageValue: String) {
    International("international"),
    Russia("russia"),
}

/**
 * Stores the user-selected regional policy.
 *
 * Regional restrictions are enforced on the assigned VPS. The Android client
 * deliberately does not download or maintain Russia restriction assets and it
 * does not install local RKN blackhole rules. Keeping the client routing on the
 * global preset also cleans up rules left behind by older app versions.
 */
internal object RegionalPolicyManager {
    private const val ROUTING_PRESET_GLOBAL = 2

    fun readMode(): RegionalPolicyMode? {
        return when (MmkvManager.decodeSettingsString(AppConfig.PREF_REGIONAL_POLICY_MODE)) {
            RegionalPolicyMode.International.storageValue -> RegionalPolicyMode.International
            RegionalPolicyMode.Russia.storageValue -> RegionalPolicyMode.Russia
            else -> null
        }
    }

    fun isRussiaModeEnabled(): Boolean = readMode() == RegionalPolicyMode.Russia

    suspend fun apply(context: Context, mode: RegionalPolicyMode): Result<Unit> = runCatching {
        // A saved/activated profile is represented in the premium UI by the
        // synthetic "skryon-activated" location. Reconnecting that location can
        // reuse its local import text without calling /vpn/connect, so merely
        // storing the mode used to leave the VPS on the previous policy.
        // Push the selected policy to the authoritative server before switching
        // the local mode. Nothing is downloaded to Android.
        applyServerPolicyIfActivated(mode)
        configureClientRouting(context.applicationContext, mode)
    }

    suspend fun prepareForConnection(context: Context): Result<Unit> = runCatching {
        val mode = readMode() ?: RegionalPolicyMode.International
        // Re-apply the neutral client routing before every connection so an
        // upgrade from an older build cannot leave local restriction rules in
        // front of the server-enforced policy.
        configureClientRouting(context.applicationContext, mode)
    }

    fun isPolicyReadyForServiceStart(context: Context): Boolean {
        // No policy assets are required on Android anymore. The mode is sent to
        // the backend when a server connection is prepared and enforced there.
        return true
    }

    private suspend fun applyServerPolicyIfActivated(mode: RegionalPolicyMode) {
        val accessKey = MmkvManager.decodeSettingsString(SKRYON_ACTIVATION_CODE_PREF, "")
            ?.trim()
            .orEmpty()
        val serverId = MmkvManager.decodeSettingsLong(SKRYON_SERVER_ID_PREF, -1L)
        if (accessKey.isBlank() || serverId <= 0L) {
            return
        }

        EmeryBackendClient.connectServer(
            accessKey = accessKey,
            serverId = serverId,
            trafficPolicy = mode.storageValue,
        ).getOrThrow()
    }

    private fun configureClientRouting(context: Context, mode: RegionalPolicyMode) {
        SettingsManager.resetRoutingRulesetsFromPresets(
            context.applicationContext,
            ROUTING_PRESET_GLOBAL,
        )
        MmkvManager.encodeSettings(AppConfig.PREF_REGIONAL_POLICY_MODE, mode.storageValue)
    }
}

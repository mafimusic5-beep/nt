package com.v2ray.ang.handler

import android.content.Context
import com.v2ray.ang.AppConfig

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

    private fun configureClientRouting(context: Context, mode: RegionalPolicyMode) {
        SettingsManager.resetRoutingRulesetsFromPresets(
            context.applicationContext,
            ROUTING_PRESET_GLOBAL,
        )
        MmkvManager.encodeSettings(AppConfig.PREF_REGIONAL_POLICY_MODE, mode.storageValue)
    }
}

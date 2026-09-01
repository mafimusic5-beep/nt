package com.v2ray.ang.handler

import android.content.Context
import com.v2ray.ang.AppConfig
import com.v2ray.ang.dto.RulesetItem
import com.v2ray.ang.security.EmeryDeviceGateConfig

internal enum class RegionalPolicyMode(val storageValue: String) {
    International("international"),
    Russia("russia"),
}

/** Stores the choice locally. The authenticated gateway enforces it on the server. */
internal object RegionalPolicyManager {
    fun readMode(): RegionalPolicyMode? = RegionalPolicyMode.entries.firstOrNull {
        it.storageValue == MmkvManager.decodeSettingsString(AppConfig.PREF_REGIONAL_POLICY_MODE)
    }

    fun isRussiaModeEnabled(): Boolean = readMode() == RegionalPolicyMode.Russia

    // Deliberately no HTTP client, geo assets, freshness check or background download.
    suspend fun apply(context: Context, mode: RegionalPolicyMode): Result<Unit> = runCatching {
        configureRoutingPolicy(context.applicationContext, mode)
    }

    suspend fun prepareForConnection(context: Context): Result<Unit> = runCatching {
        if (isRussiaModeEnabled()) {
            // Also migrates old installations and removes legacy client-side list rules.
            configureRoutingPolicy(context.applicationContext, RegionalPolicyMode.Russia)
        }
    }

    fun isPolicyReadyForServiceStart(): Boolean {
        if (!isRussiaModeEnabled()) return true
        val profile = MmkvManager.getSelectServer()?.let(MmkvManager::decodeServerConfig)
        return EmeryDeviceGateConfig.descriptorFor(profile) != null &&
            areServerPolicyRulesReady(MmkvManager.decodeRoutingRulesets().orEmpty()) &&
            MmkvManager.decodeSettingsBool(AppConfig.PREF_LOCAL_DNS_ENABLED, false) &&
            MmkvManager.decodeSettingsBool(AppConfig.PREF_SNIFFING_ENABLED, true) &&
            MmkvManager.decodeSettingsBool(AppConfig.PREF_ROUTE_ONLY_ENABLED, false)
    }

    private fun configureRoutingPolicy(context: Context, mode: RegionalPolicyMode) {
        if (mode == RegionalPolicyMode.Russia) {
            // No direct/locked exception may bypass the server policy, including DNS.
            MmkvManager.encodeRoutingRulesets(serverRegionalPolicyRules().toMutableList())
            MmkvManager.encodeSettings(AppConfig.PREF_ROUTING_DOMAIN_STRATEGY, "AsIs")
            MmkvManager.encodeSettings(AppConfig.PREF_LOCAL_DNS_ENABLED, true)
            MmkvManager.encodeSettings(AppConfig.PREF_SNIFFING_ENABLED, true)
            MmkvManager.encodeSettings(AppConfig.PREF_ROUTE_ONLY_ENABLED, true)
        } else {
            SettingsManager.resetRoutingRulesetsFromPresets(context, 2)
        }
        check(MmkvManager.encodeSettings(AppConfig.PREF_REGIONAL_POLICY_MODE, mode.storageValue)) {
            "Unable to save regional policy"
        }
    }
}

internal fun serverRegionalPolicyRules(): List<RulesetItem> = listOf(
    RulesetItem(
        remarks = "Server regional policy",
        outboundTag = AppConfig.TAG_PROXY,
        network = "tcp,udp",
    ),
)

internal fun areServerPolicyRulesReady(rules: List<RulesetItem>): Boolean =
    rules == serverRegionalPolicyRules()

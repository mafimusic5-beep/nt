package com.v2ray.ang.handler

import com.v2ray.ang.AppConfig

data class EmeryAccessProfile(
    val accessKey: String,
    val vpnEnabled: Boolean,
    val routerEnabled: Boolean,
    val expiresAt: String,
    val planName: String,
)

object EmeryAccessManager {

    fun isActivated(): Boolean {
        return !MmkvManager.decodeSettingsString(AppConfig.PREF_EMERY_ACCESS_KEY).isNullOrBlank()
    }

    fun loadProfile(): EmeryAccessProfile? {
        val key = MmkvManager.decodeSettingsString(AppConfig.PREF_EMERY_ACCESS_KEY) ?: return null
        if (key.isBlank()) return null
        val expires = MmkvManager.decodeSettingsString(AppConfig.PREF_EMERY_EXPIRES_AT) ?: return null
        val plan = MmkvManager.decodeSettingsString(AppConfig.PREF_EMERY_PLAN_NAME) ?: ""
        return EmeryAccessProfile(
            accessKey = key,
            vpnEnabled = MmkvManager.decodeSettingsBool(AppConfig.PREF_EMERY_VPN_ENABLED, false),
            routerEnabled = MmkvManager.decodeSettingsBool(AppConfig.PREF_EMERY_ROUTER_ENABLED, false),
            expiresAt = expires,
            planName = plan,
        )
    }

    fun saveProfile(profile: EmeryAccessProfile) {
        MmkvManager.encodeSettings(AppConfig.PREF_EMERY_ACCESS_KEY, profile.accessKey)
        MmkvManager.encodeSettings(AppConfig.PREF_EMERY_VPN_ENABLED, profile.vpnEnabled)
        MmkvManager.encodeSettings(AppConfig.PREF_EMERY_ROUTER_ENABLED, profile.routerEnabled)
        MmkvManager.encodeSettings(AppConfig.PREF_EMERY_EXPIRES_AT, profile.expiresAt)
        MmkvManager.encodeSettings(AppConfig.PREF_EMERY_PLAN_NAME, profile.planName)
    }

    fun clearSession() {
        MmkvManager.encodeSettings(AppConfig.PREF_EMERY_ACCESS_KEY, "")
        MmkvManager.encodeSettings(AppConfig.PREF_EMERY_VPN_ENABLED, false)
        MmkvManager.encodeSettings(AppConfig.PREF_EMERY_ROUTER_ENABLED, false)
        MmkvManager.encodeSettings(AppConfig.PREF_EMERY_EXPIRES_AT, "")
        MmkvManager.encodeSettings(AppConfig.PREF_EMERY_PLAN_NAME, "")
    }
}

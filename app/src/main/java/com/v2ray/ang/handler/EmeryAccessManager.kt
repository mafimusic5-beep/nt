package com.v2ray.ang.handler

import com.v2ray.ang.AppConfig

private const val PREF_EMERY_DEVICE_ID_LOCAL = "pref_emery_device_id"
private const val PREF_EMERY_DEVICE_NAME_LOCAL = "pref_emery_device_name"
private const val PREF_EMERY_DEVICES_LIMIT_LOCAL = "pref_emery_devices_limit"

data class EmeryAccessProfile(
    val accessKey: String,
    val vpnEnabled: Boolean,
    val routerEnabled: Boolean,
    val expiresAt: String,
    val planName: String,
    val deviceId: String = "",
    val deviceName: String = "",
    val devicesUsed: Int = 0,
    val devicesLimit: Int = 5,
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
            deviceId = MmkvManager.decodeSettingsString(PREF_EMERY_DEVICE_ID_LOCAL).orEmpty(),
            deviceName = MmkvManager.decodeSettingsString(PREF_EMERY_DEVICE_NAME_LOCAL).orEmpty(),
            devicesUsed = MmkvManager.decodeSettingsInt(AppConfig.PREF_EMERY_DEVICES_USED, 0),
            devicesLimit = MmkvManager.decodeSettingsInt(PREF_EMERY_DEVICES_LIMIT_LOCAL, 5),
        )
    }

    fun saveProfile(profile: EmeryAccessProfile) {
        MmkvManager.encodeSettings(AppConfig.PREF_EMERY_ACCESS_KEY, profile.accessKey)
        MmkvManager.encodeSettings(AppConfig.PREF_EMERY_VPN_ENABLED, profile.vpnEnabled)
        MmkvManager.encodeSettings(AppConfig.PREF_EMERY_ROUTER_ENABLED, profile.routerEnabled)
        MmkvManager.encodeSettings(AppConfig.PREF_EMERY_EXPIRES_AT, profile.expiresAt)
        MmkvManager.encodeSettings(AppConfig.PREF_EMERY_PLAN_NAME, profile.planName)
        if (profile.deviceId.isNotBlank()) {
            MmkvManager.encodeSettings(PREF_EMERY_DEVICE_ID_LOCAL, profile.deviceId)
        }
        if (profile.deviceName.isNotBlank()) {
            MmkvManager.encodeSettings(PREF_EMERY_DEVICE_NAME_LOCAL, profile.deviceName)
        }
        MmkvManager.encodeSettings(AppConfig.PREF_EMERY_DEVICES_USED, profile.devicesUsed)
        MmkvManager.encodeSettings(PREF_EMERY_DEVICES_LIMIT_LOCAL, profile.devicesLimit)
    }

    fun clearSession() {
        MmkvManager.encodeSettings(AppConfig.PREF_EMERY_ACCESS_KEY, "")
        MmkvManager.encodeSettings(AppConfig.PREF_EMERY_VPN_ENABLED, false)
        MmkvManager.encodeSettings(AppConfig.PREF_EMERY_ROUTER_ENABLED, false)
        MmkvManager.encodeSettings(AppConfig.PREF_EMERY_EXPIRES_AT, "")
        MmkvManager.encodeSettings(AppConfig.PREF_EMERY_PLAN_NAME, "")
        MmkvManager.encodeSettings(AppConfig.PREF_EMERY_DEVICES_USED, 0)
        MmkvManager.encodeSettings(PREF_EMERY_DEVICES_LIMIT_LOCAL, 5)
    }
}

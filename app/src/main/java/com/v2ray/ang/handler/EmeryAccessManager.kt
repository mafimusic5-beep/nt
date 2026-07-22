package com.v2ray.ang.handler

import com.v2ray.ang.AppConfig
import com.v2ray.ang.BuildConfig
import com.v2ray.ang.security.EmeryDeviceIdentity

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

    private val developmentProfile = EmeryAccessProfile(
        accessKey = "DEV-SESSION",
        vpnEnabled = true,
        routerEnabled = true,
        expiresAt = "2099-12-31T23:59:59Z",
        planName = "Development",
        deviceId = "dev-device",
        deviceName = "Android-устройство",
        devicesUsed = 1,
        devicesLimit = 5,
    )

    fun isActivated(): Boolean {
        if (BuildConfig.DEBUG) return true
        return !MmkvManager.decodeSettingsString(AppConfig.PREF_EMERY_ACCESS_KEY).isNullOrBlank()
    }

    fun loadProfile(): EmeryAccessProfile? {
        if (BuildConfig.DEBUG) return developmentProfile

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
            deviceId = EmeryDeviceIdentity.deviceId(),
            deviceName = EmeryDeviceIdentity.deviceName(),
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
        if (profile.deviceName.isNotBlank()) {
            EmeryDeviceIdentity.setDeviceName(profile.deviceName)
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

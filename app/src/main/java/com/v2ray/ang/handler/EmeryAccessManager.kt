package com.v2ray.ang.handler

import com.v2ray.ang.AppConfig
import com.v2ray.ang.BuildConfig
import java.util.Locale
import org.json.JSONArray
import org.json.JSONObject

private const val PREF_EMERY_DEVICE_ID_LOCAL = "pref_emery_device_id"
private const val PREF_EMERY_DEVICE_NAME_LOCAL = "pref_emery_device_name"
private const val PREF_EMERY_DEVICES_LIMIT_LOCAL = "pref_emery_devices_limit"
private const val PREF_EMERY_DEVICES_JSON_LOCAL = "pref_emery_devices_json_v1"

data class EmeryDeviceRecord(
    val deviceId: String,
    val deviceName: String,
    val platform: String = "android",
    val appVersion: String = "",
    val firstSeenAt: String = "",
    val lastSeenAt: String = "",
    val active: Boolean = true,
    val isCurrent: Boolean = false,
)

data class EmeryAccessProfile(
    val accessKey: String,
    val vpnEnabled: Boolean,
    val routerEnabled: Boolean,
    val expiresAt: String,
    val planName: String,
    val deviceId: String = "",
    val deviceName: String = "",
    val devicesUsed: Int = 0,
    val devicesLimit: Int = 0,
    val devices: List<EmeryDeviceRecord> = emptyList(),
)

internal fun expectedDeviceLimitForPlan(planName: String): Int? {
    val normalized = planName
        .trim()
        .lowercase(Locale.ROOT)
        .replace('ё', 'е')
        .replace(" ", "")

    return when {
        normalized.contains("семейн") || normalized.contains("family") -> 5
        normalized.contains("личный+") || normalized.contains("личныйплюс") ||
            normalized.contains("personal+") || normalized.contains("personalplus") ||
            normalized.contains("plus") -> 2
        normalized.contains("личн") || normalized.contains("personal") -> 1
        normalized.contains("development") -> 5
        else -> null
    }
}

internal fun validateDeviceLimit(planName: String, devicesUsed: Int, devicesLimit: Int): Boolean {
    if (devicesLimit !in setOf(1, 2, 5)) return false
    if (devicesUsed !in 1..devicesLimit) return false
    val expected = expectedDeviceLimitForPlan(planName)
    return expected == null || expected == devicesLimit
}

object EmeryAccessManager {

    private val developmentProfile = EmeryAccessProfile(
        accessKey = "DEV-SESSION",
        vpnEnabled = true,
        routerEnabled = true,
        expiresAt = "2099-12-31T23:59:59Z",
        planName = "Development",
        deviceId = "dev-device",
        deviceName = "Development device",
        devicesUsed = 1,
        devicesLimit = 5,
        devices = listOf(
            EmeryDeviceRecord(
                deviceId = "dev-device",
                deviceName = "Development device",
                platform = "android",
                appVersion = BuildConfig.VERSION_NAME,
                active = true,
                isCurrent = true,
            )
        ),
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
        val deviceId = MmkvManager.decodeSettingsString(PREF_EMERY_DEVICE_ID_LOCAL).orEmpty()
        val deviceName = MmkvManager.decodeSettingsString(PREF_EMERY_DEVICE_NAME_LOCAL).orEmpty()
        val storedDevices = decodeDevices()
        val devices = if (storedDevices.isNotEmpty()) {
            storedDevices.map { it.copy(isCurrent = it.deviceId == deviceId) }
        } else if (deviceId.isNotBlank()) {
            listOf(
                EmeryDeviceRecord(
                    deviceId = deviceId,
                    deviceName = deviceName.ifBlank { "Это устройство" },
                    appVersion = BuildConfig.VERSION_NAME,
                    isCurrent = true,
                )
            )
        } else {
            emptyList()
        }

        return EmeryAccessProfile(
            accessKey = key,
            vpnEnabled = MmkvManager.decodeSettingsBool(AppConfig.PREF_EMERY_VPN_ENABLED, false),
            routerEnabled = MmkvManager.decodeSettingsBool(AppConfig.PREF_EMERY_ROUTER_ENABLED, false),
            expiresAt = expires,
            planName = plan,
            deviceId = deviceId,
            deviceName = deviceName,
            devicesUsed = MmkvManager.decodeSettingsInt(AppConfig.PREF_EMERY_DEVICES_USED, devices.count { it.active }),
            devicesLimit = MmkvManager.decodeSettingsInt(
                PREF_EMERY_DEVICES_LIMIT_LOCAL,
                expectedDeviceLimitForPlan(plan) ?: 0,
            ),
            devices = devices,
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

        val inventory = if (profile.devices.isNotEmpty()) {
            profile.devices
        } else if (profile.deviceId.isNotBlank()) {
            listOf(
                EmeryDeviceRecord(
                    deviceId = profile.deviceId,
                    deviceName = profile.deviceName.ifBlank { "Это устройство" },
                    appVersion = BuildConfig.VERSION_NAME,
                    isCurrent = true,
                )
            )
        } else {
            emptyList()
        }
        encodeDevices(inventory)
    }

    fun clearSession() {
        MmkvManager.encodeSettings(AppConfig.PREF_EMERY_ACCESS_KEY, "")
        MmkvManager.encodeSettings(AppConfig.PREF_EMERY_VPN_ENABLED, false)
        MmkvManager.encodeSettings(AppConfig.PREF_EMERY_ROUTER_ENABLED, false)
        MmkvManager.encodeSettings(AppConfig.PREF_EMERY_EXPIRES_AT, "")
        MmkvManager.encodeSettings(AppConfig.PREF_EMERY_PLAN_NAME, "")
        MmkvManager.encodeSettings(AppConfig.PREF_EMERY_DEVICES_USED, 0)
        MmkvManager.encodeSettings(PREF_EMERY_DEVICES_LIMIT_LOCAL, 0)
        MmkvManager.encodeSettings(PREF_EMERY_DEVICES_JSON_LOCAL, "[]")
    }

    private fun encodeDevices(devices: List<EmeryDeviceRecord>) {
        val array = JSONArray()
        devices.forEach { device ->
            array.put(
                JSONObject()
                    .put("deviceId", device.deviceId)
                    .put("deviceName", device.deviceName)
                    .put("platform", device.platform)
                    .put("appVersion", device.appVersion)
                    .put("firstSeenAt", device.firstSeenAt)
                    .put("lastSeenAt", device.lastSeenAt)
                    .put("active", device.active)
                    .put("isCurrent", device.isCurrent)
            )
        }
        MmkvManager.encodeSettings(PREF_EMERY_DEVICES_JSON_LOCAL, array.toString())
    }

    private fun decodeDevices(): List<EmeryDeviceRecord> {
        val raw = MmkvManager.decodeSettingsString(PREF_EMERY_DEVICES_JSON_LOCAL).orEmpty()
        if (raw.isBlank()) return emptyList()
        return runCatching {
            val array = JSONArray(raw)
            buildList {
                for (index in 0 until array.length()) {
                    val item = array.optJSONObject(index) ?: continue
                    val id = item.optString("deviceId").trim()
                    if (id.isBlank()) continue
                    add(
                        EmeryDeviceRecord(
                            deviceId = id,
                            deviceName = item.optString("deviceName").trim().ifBlank { "Устройство" },
                            platform = item.optString("platform", "android"),
                            appVersion = item.optString("appVersion"),
                            firstSeenAt = item.optString("firstSeenAt"),
                            lastSeenAt = item.optString("lastSeenAt"),
                            active = item.optBoolean("active", true),
                            isCurrent = item.optBoolean("isCurrent", false),
                        )
                    )
                }
            }
        }.getOrDefault(emptyList())
    }
}

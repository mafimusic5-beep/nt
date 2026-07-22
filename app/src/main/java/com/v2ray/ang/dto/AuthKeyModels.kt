package com.v2ray.ang.dto

import com.google.gson.annotations.SerializedName

data class AuthKeyRequestBody(
    @SerializedName("key") val key: String,
)

data class DeviceApiResponseBody(
    @SerializedName("device_id") val deviceId: String? = null,
    @SerializedName("device_name") val deviceName: String? = null,
    @SerializedName("platform") val platform: String? = null,
    @SerializedName("app_version") val appVersion: String? = null,
    @SerializedName("first_seen_at") val firstSeenAt: String? = null,
    @SerializedName("last_seen_at") val lastSeenAt: String? = null,
    @SerializedName("active") val active: Boolean? = null,
    @SerializedName("is_current") val isCurrent: Boolean? = null,
)

data class AuthKeyResponseBody(
    @SerializedName("valid") val valid: Boolean,
    @SerializedName("error") val error: String? = null,
    @SerializedName("vpn_enabled") val vpnEnabled: Boolean? = null,
    @SerializedName("router_enabled") val routerEnabled: Boolean? = null,
    @SerializedName("expires_at") val expiresAt: String? = null,
    @SerializedName("plan_name") val planName: String? = null,
    @SerializedName("order_id") val orderId: String? = null,
    @SerializedName("device_id") val deviceId: String? = null,
    @SerializedName("device_name") val deviceName: String? = null,
    @SerializedName("devices_used") val devicesUsed: Int? = null,
    @SerializedName("devices_limit") val devicesLimit: Int? = null,
    @SerializedName("devices") val devices: List<DeviceApiResponseBody>? = null,
)

/** GET /profile (Bearer access key + signed device headers). */
data class ProfileApiResponseBody(
    @SerializedName("user_id") val userId: Long? = null,
    @SerializedName("vpn_enabled") val vpnEnabled: Boolean? = null,
    @SerializedName("router_enabled") val routerEnabled: Boolean? = null,
    @SerializedName("expires_at") val expiresAt: String? = null,
    @SerializedName("plan_name") val planName: String? = null,
    @SerializedName("device_id") val deviceId: String? = null,
    @SerializedName("device_name") val deviceName: String? = null,
    @SerializedName("devices_used") val devicesUsed: Int? = null,
    @SerializedName("devices_limit") val devicesLimit: Int? = null,
    @SerializedName("devices") val devices: List<DeviceApiResponseBody>? = null,
)

/** GET /vpn/config */
data class VpnConfigApiResponseBody(
    @SerializedName("import_text") val importText: String? = null,
    @SerializedName("error") val error: String? = null,
)

data class VpnServerItemApiResponseBody(
    @SerializedName("id") val id: Long,
    @SerializedName("city") val city: String? = null,
    @SerializedName("health_status") val healthStatus: String? = null,
    @SerializedName("is_available") val isAvailable: Boolean? = null,
)

data class VpnConnectRequestBody(
    @SerializedName("access_key") val accessKey: String,
    @SerializedName("server_id") val serverId: Long,
)

data class VpnConnectApiResponseBody(
    @SerializedName("server_id") val serverId: Long? = null,
    @SerializedName("city") val city: String? = null,
    @SerializedName("import_text") val importText: String? = null,
    @SerializedName("error") val error: String? = null,
)

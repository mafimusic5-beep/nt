package com.v2ray.ang.dto

import com.google.gson.annotations.SerializedName

data class AuthKeyRequestBody(
    @SerializedName("key") val key: String,
)

data class AuthKeyResponseBody(
    @SerializedName("valid") val valid: Boolean,
    @SerializedName("error") val error: String? = null,
    @SerializedName("vpn_enabled") val vpnEnabled: Boolean? = null,
    @SerializedName("router_enabled") val routerEnabled: Boolean? = null,
    @SerializedName("expires_at") val expiresAt: String? = null,
    @SerializedName("plan_name") val planName: String? = null,
    @SerializedName("order_id") val orderId: String? = null,
)

/** GET /profile (Bearer access key). */
data class ProfileApiResponseBody(
    @SerializedName("user_id") val userId: Long? = null,
    @SerializedName("vpn_enabled") val vpnEnabled: Boolean? = null,
    @SerializedName("router_enabled") val routerEnabled: Boolean? = null,
    @SerializedName("expires_at") val expiresAt: String? = null,
    @SerializedName("plan_name") val planName: String? = null,
)

/** GET /vpn/config */
data class VpnConfigApiResponseBody(
    @SerializedName("import_text") val importText: String? = null,
    @SerializedName("error") val error: String? = null,
)

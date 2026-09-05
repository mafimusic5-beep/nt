package com.v2ray.ang.network

import com.v2ray.ang.AppConfig
import com.v2ray.ang.BuildConfig
import com.v2ray.ang.dto.DeviceApiResponseBody
import com.v2ray.ang.dto.ProfileApiResponseBody
import com.v2ray.ang.dto.VpnConnectApiResponseBody
import com.v2ray.ang.dto.VpnConnectRequestBody
import com.v2ray.ang.dto.VpnConfigApiResponseBody
import com.v2ray.ang.dto.VpnServerItemApiResponseBody
import com.v2ray.ang.handler.EmeryAccessManager
import com.v2ray.ang.handler.EmeryAccessProfile
import com.v2ray.ang.handler.EmeryApiConfig
import com.v2ray.ang.handler.EmeryDeviceRecord
import com.v2ray.ang.handler.expectedDeviceLimitForPlan
import com.v2ray.ang.handler.validateDeviceLimit
import com.v2ray.ang.security.EmeryDeviceIdentity
import com.v2ray.ang.util.JsonUtil
import java.io.IOException
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject

/**
 * Authenticated Emery API calls after the access key is known.
 * Device registration/profile use the public HTTPS API. Region and pool calls keep
 * their configured backend URL.
 */
object EmeryBackendClient {

    private const val PUBLIC_DEVICE_API_BASE_URL = "https://skryon.ru"
    private const val PUBLIC_DEVICE_PROFILE_PATH = "/api/device/profile"

    private val client = OkHttpClient.Builder()
        .connectTimeout(20, TimeUnit.SECONDS)
        .readTimeout(30, TimeUnit.SECONDS)
        .writeTimeout(20, TimeUnit.SECONDS)
        .retryOnConnectionFailure(true)
        .build()

    private fun baseUrl(): String = EmeryApiConfig.baseUrl()

    private fun authorizedGet(
        path: String,
        accessKey: String,
        baseUrlOverride: String? = null,
    ): Request {
        val credential = accessKey.trim()
        val proof = EmeryDeviceIdentity.buildRequestProof(method = "GET", path = path, authSecret = credential)
        val resolvedBaseUrl = (baseUrlOverride ?: baseUrl()).trimEnd('/')
        return Request.Builder()
            .url("$resolvedBaseUrl$path")
            .header("Authorization", "Bearer $credential")
            .header("X-Emery-Device-Id", proof.deviceId)
            .header("X-Emery-Timestamp", proof.timestampMillis)
            .header("X-Emery-Nonce", proof.nonce)
            .header("X-Emery-Signature", proof.signatureBase64)
            .header("X-Emery-Signature-Algorithm", proof.signatureAlgorithm)
            .header(AppConfig.SKRYON_APP_VERSION_HEADER, BuildConfig.SKRYON_VERSION_CODE.toString())
            .get()
            .build()
    }

    private fun authorizedPost(path: String, accessKey: String, bodyJson: String): Request {
        val credential = accessKey.trim()
        val proof = EmeryDeviceIdentity.buildRequestProof(method = "POST", path = path, authSecret = credential)
        return Request.Builder()
            .url("${baseUrl()}$path")
            .header("Authorization", "Bearer $credential")
            .header("X-Emery-Device-Id", proof.deviceId)
            .header("X-Emery-Timestamp", proof.timestampMillis)
            .header("X-Emery-Nonce", proof.nonce)
            .header("X-Emery-Signature", proof.signatureBase64)
            .header("X-Emery-Signature-Algorithm", proof.signatureAlgorithm)
            .header(AppConfig.SKRYON_APP_VERSION_HEADER, BuildConfig.SKRYON_VERSION_CODE.toString())
            .post(bodyJson.toRequestBody("application/json; charset=utf-8".toMediaType()))
            .build()
    }

    data class BackendServer(
        val id: Long,
        val city: String,
        val healthStatus: String,
        val isAvailable: Boolean,
    )

    data class ConnectPayload(
        val serverId: Long,
        val city: String,
        val importText: String,
    )

    suspend fun fetchProfile(
        accessKey: String,
        requireDeviceInventory: Boolean = false,
    ): Result<EmeryAccessProfile> = withContext(Dispatchers.IO) {
        val key = accessKey.trim()
        if (key.isEmpty()) return@withContext Result.failure(IllegalStateException("bad_request"))
        val request = authorizedGet(
            path = PUBLIC_DEVICE_PROFILE_PATH,
            accessKey = key,
            baseUrlOverride = PUBLIC_DEVICE_API_BASE_URL,
        )
        try {
            client.newCall(request).execute().use { response ->
                val raw = response.body?.string().orEmpty()
                if (response.code == 404 || response.code == 405) {
                    return@withContext EmeryAuthClient.verifyAccessKey(key)
                }
                if (response.code == 401 || response.code == 403) {
                    val err = runCatching { JSONObject(raw).optString("error") }
                        .getOrDefault("")
                        .ifBlank {
                            JsonUtil.fromJson(raw, VpnConfigApiResponseBody::class.java)?.error.orEmpty()
                        }
                    return@withContext Result.failure(
                        IllegalStateException(err.ifBlank { "device_not_registered" })
                    )
                }
                if (!response.isSuccessful) {
                    return@withContext Result.failure(IllegalStateException("http_${response.code}"))
                }
                val parsed = JsonUtil.fromJson(raw, ProfileApiResponseBody::class.java)
                    ?: return@withContext EmeryAuthClient.verifyAccessKey(key)
                val expires = parsed.expiresAt.orEmpty()
                val local = EmeryAccessManager.loadProfile()
                val currentDeviceId = EmeryDeviceIdentity.deviceId()
                val serverDeviceId = parsed.deviceId?.trim().orEmpty()
                if (requireDeviceInventory && serverDeviceId.isBlank()) {
                    return@withContext EmeryAuthClient.verifyAccessKey(key)
                }
                if (serverDeviceId.isNotBlank() && serverDeviceId != currentDeviceId) {
                    return@withContext Result.failure(IllegalStateException("device_mismatch"))
                }

                val planName = parsed.planName.orEmpty().ifBlank { local?.planName.orEmpty() }
                val serverDevices = parsed.devices.orEmpty().mapNotNull { it.toDeviceRecord(currentDeviceId) }
                val devices = when {
                    serverDevices.isNotEmpty() -> serverDevices
                    requireDeviceInventory -> return@withContext EmeryAuthClient.verifyAccessKey(key)
                    else -> local?.devices.orEmpty()
                }

                val devicesUsed = parsed.devicesUsed
                    ?: serverDevices.count { it.active }.takeIf { it > 0 }
                    ?: local?.devicesUsed
                    ?: 0
                val devicesLimit = parsed.devicesLimit
                    ?: expectedDeviceLimitForPlan(planName)
                    ?: local?.devicesLimit
                    ?: 0

                if (requireDeviceInventory) {
                    if (!validateDeviceLimit(planName, devicesUsed, devicesLimit)) {
                        return@withContext Result.failure(IllegalStateException("plan_limit_mismatch"))
                    }
                    val ids = devices.map { it.deviceId }
                    if (ids.distinct().size != ids.size) {
                        return@withContext Result.failure(IllegalStateException("device_inventory_mismatch"))
                    }
                    if (devices.count { it.active } != devicesUsed) {
                        return@withContext Result.failure(IllegalStateException("device_counter_mismatch"))
                    }
                    val currentRow = devices.firstOrNull { it.deviceId == currentDeviceId }
                    if (currentRow == null || !currentRow.active) {
                        return@withContext Result.failure(IllegalStateException("device_inventory_mismatch"))
                    }
                }

                Result.success(
                    EmeryAccessProfile(
                        accessKey = key,
                        vpnEnabled = parsed.vpnEnabled == true,
                        routerEnabled = parsed.routerEnabled == true,
                        expiresAt = expires,
                        planName = planName,
                        deviceId = serverDeviceId.ifBlank { currentDeviceId },
                        deviceName = parsed.deviceName?.trim().orEmpty().ifBlank {
                            local?.deviceName.orEmpty().ifBlank { EmeryDeviceIdentity.deviceName() }
                        },
                        devicesUsed = devicesUsed,
                        devicesLimit = devicesLimit,
                        devices = devices,
                    )
                )
            }
        } catch (_: IOException) {
            EmeryAuthClient.verifyAccessKey(key)
        }
    }

    suspend fun confirmDeviceRegistration(
        accessKey: String,
        activationProfile: EmeryAccessProfile,
    ): Result<EmeryAccessProfile> {
        val confirmed = fetchProfile(accessKey, requireDeviceInventory = true)
        return confirmed.mapCatching { profile ->
            if (profile.deviceId != activationProfile.deviceId) {
                throw IllegalStateException("device_mismatch")
            }
            if (profile.devicesUsed != activationProfile.devicesUsed ||
                profile.devicesLimit != activationProfile.devicesLimit
            ) {
                throw IllegalStateException("device_counter_mismatch")
            }
            profile.copy(
                vpnEnabled = profile.vpnEnabled || activationProfile.vpnEnabled,
                routerEnabled = profile.routerEnabled || activationProfile.routerEnabled,
                expiresAt = profile.expiresAt.ifBlank { activationProfile.expiresAt },
                planName = profile.planName.ifBlank { activationProfile.planName },
            )
        }
    }

    suspend fun fetchVpnConfigImportText(accessKey: String): Result<String> = withContext(Dispatchers.IO) {
        val key = accessKey.trim()
        if (key.isEmpty()) return@withContext Result.failure(IllegalStateException("bad_request"))
        val request = authorizedGet("/vpn/config", key)
        try {
            client.newCall(request).execute().use { response ->
                val raw = response.body?.string().orEmpty()
                val parsed = JsonUtil.fromJson(raw, VpnConfigApiResponseBody::class.java)
                if (response.code == 401) return@withContext Result.failure(IllegalStateException(parsed?.error ?: "invalid_or_expired_key"))
                if (response.code == 403) return@withContext Result.failure(IllegalStateException(parsed?.error ?: "vpn_disabled"))
                if (response.code == 404) return@withContext Result.failure(IllegalStateException(parsed?.error ?: "no_allocation"))
                if (!response.isSuccessful) return@withContext Result.failure(IllegalStateException(parsed?.error ?: "http_${response.code}"))
                val text = parsed?.importText?.trim().orEmpty()
                if (text.isEmpty()) return@withContext Result.failure(IllegalStateException("parse_error"))
                Result.success(text)
            }
        } catch (_: IOException) {
            Result.failure(IllegalStateException("network"))
        }
    }

    suspend fun fetchVpnServers(): Result<List<BackendServer>> = withContext(Dispatchers.IO) {
        val request = Request.Builder()
            .url("${baseUrl()}/api/v1/vpn/servers")
            .header(AppConfig.SKRYON_APP_VERSION_HEADER, BuildConfig.SKRYON_VERSION_CODE.toString())
            .get()
            .build()
        try {
            client.newCall(request).execute().use { response ->
                val raw = response.body?.string().orEmpty()
                if (!response.isSuccessful) return@withContext Result.failure(IllegalStateException("http_${response.code}"))
                val parsed = JsonUtil.fromJson(raw, Array<VpnServerItemApiResponseBody>::class.java)?.toList()
                    ?: return@withContext Result.failure(IllegalStateException("parse_error"))
                Result.success(parsed.map {
                    BackendServer(
                        id = it.id,
                        city = it.city?.ifBlank { "Unknown" } ?: "Unknown",
                        healthStatus = it.healthStatus ?: "unknown",
                        isAvailable = it.isAvailable != false,
                    )
                })
            }
        } catch (_: IOException) {
            Result.failure(IllegalStateException("network"))
        }
    }

    suspend fun connectServer(
        accessKey: String,
        serverId: Long,
        trafficPolicy: String,
    ): Result<ConnectPayload> = withContext(Dispatchers.IO) {
        val key = accessKey.trim()
        val policy = trafficPolicy.trim().lowercase()
        if (key.isEmpty() || serverId <= 0L || policy !in setOf("russia", "international")) {
            return@withContext Result.failure(IllegalStateException("bad_request"))
        }
        val bodyJson = JsonUtil.toJson(
            VpnConnectRequestBody(accessKey = key, serverId = serverId, trafficPolicy = policy),
        ) ?: "{}"
        val request = authorizedPost("/api/v1/vpn/connect", key, bodyJson)
        try {
            client.newCall(request).execute().use { response ->
                val raw = response.body?.string().orEmpty()
                val parsed = JsonUtil.fromJson(raw, VpnConnectApiResponseBody::class.java)
                val detail = try { JSONObject(raw).optString("detail") } catch (_: Exception) { "" }
                if (response.code == 401) return@withContext Result.failure(IllegalStateException(parsed?.error ?: detail.ifBlank { "invalid_or_expired_key" }))
                if (response.code == 404) return@withContext Result.failure(IllegalStateException(parsed?.error ?: detail.ifBlank { "server_not_found" }))
                if (response.code == 409) return@withContext Result.failure(IllegalStateException(parsed?.error ?: detail.ifBlank { "server_config_unavailable" }))
                if (!response.isSuccessful) return@withContext Result.failure(IllegalStateException(parsed?.error ?: detail.ifBlank { "http_${response.code}" }))
                val importText = parsed?.importText?.trim().orEmpty()
                if (importText.isEmpty()) return@withContext Result.failure(IllegalStateException("server_config_unavailable"))
                Result.success(
                    ConnectPayload(
                        serverId = parsed?.serverId ?: serverId,
                        city = parsed?.city?.ifBlank { "Unknown" } ?: "Unknown",
                        importText = importText,
                    )
                )
            }
        } catch (_: IOException) {
            Result.failure(IllegalStateException("network"))
        }
    }

    private fun DeviceApiResponseBody.toDeviceRecord(currentDeviceId: String): EmeryDeviceRecord? {
        val id = deviceId?.trim().orEmpty()
        if (id.isBlank()) return null
        return EmeryDeviceRecord(
            deviceId = id,
            deviceName = deviceName?.trim().orEmpty().ifBlank { "Устройство" },
            platform = platform?.trim().orEmpty().ifBlank { "unknown" },
            appVersion = appVersion?.trim().orEmpty(),
            firstSeenAt = firstSeenAt?.trim().orEmpty(),
            lastSeenAt = lastSeenAt?.trim().orEmpty(),
            active = active != false,
            isCurrent = isCurrent == true || id == currentDeviceId,
        )
    }
}

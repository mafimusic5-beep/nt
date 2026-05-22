package com.v2ray.ang.network

import com.v2ray.ang.dto.ProfileApiResponseBody
import com.v2ray.ang.dto.VpnConnectApiResponseBody
import com.v2ray.ang.dto.VpnConnectRequestBody
import com.v2ray.ang.dto.VpnConfigApiResponseBody
import com.v2ray.ang.handler.EmeryAccessProfile
import com.v2ray.ang.handler.EmeryApiConfig
import com.v2ray.ang.security.EmeryDeviceIdentity
import com.v2ray.ang.util.JsonUtil
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Request
import okhttp3.MediaType.Companion.toMediaType
import java.io.IOException
import java.util.concurrent.TimeUnit
import org.json.JSONArray
import org.json.JSONObject

/**
 * Authenticated Emery API calls after the access key is known.
 * Uses Authorization: Bearer <access key> together with a device-bound request signature.
 */
object EmeryBackendClient {

    private val client = OkHttpClient.Builder()
        .connectTimeout(20, TimeUnit.SECONDS)
        .readTimeout(30, TimeUnit.SECONDS)
        .writeTimeout(20, TimeUnit.SECONDS)
        .build()

    private val serverListPaths = listOf(
        "/api/v1/vpn/servers",
        "/api/v1/servers",
        "/vpn/servers",
        "/servers",
    )

    private fun baseUrl(): String = EmeryApiConfig.baseUrl()

    private fun authorizedGet(path: String, accessKey: String): Request {
        val credential = accessKey.trim()
        val proof = EmeryDeviceIdentity.buildRequestProof(method = "GET", path = path, authSecret = credential)
        return Request.Builder()
            .url("${baseUrl()}$path")
            .header("Authorization", "Bearer $credential")
            .header("X-Emery-Device-Id", proof.deviceId)
            .header("X-Emery-Timestamp", proof.timestampMillis)
            .header("X-Emery-Nonce", proof.nonce)
            .header("X-Emery-Signature", proof.signatureBase64)
            .header("X-Emery-Signature-Algorithm", proof.signatureAlgorithm)
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

    suspend fun fetchProfile(accessKey: String): Result<EmeryAccessProfile> = withContext(Dispatchers.IO) {
        val key = accessKey.trim()
        if (key.isEmpty()) return@withContext Result.failure(IllegalStateException("bad_request"))
        val request = authorizedGet("/profile", key)
        try {
            client.newCall(request).execute().use { response ->
                val raw = response.body?.string().orEmpty()
                if (response.code == 401) {
                    val err = JsonUtil.fromJson(raw, VpnConfigApiResponseBody::class.java)?.error
                    return@withContext Result.failure(IllegalStateException(err ?: "invalid_or_expired_key"))
                }
                if (!response.isSuccessful) {
                    return@withContext Result.failure(IllegalStateException("http_${response.code}"))
                }
                val parsed = JsonUtil.fromJson(raw, ProfileApiResponseBody::class.java)
                    ?: return@withContext Result.failure(IllegalStateException("parse_error"))
                val expires = parsed.expiresAt.orEmpty()
                if (expires.isBlank()) {
                    return@withContext Result.failure(IllegalStateException("parse_error"))
                }
                Result.success(
                    EmeryAccessProfile(
                        accessKey = key,
                        vpnEnabled = parsed.vpnEnabled == true,
                        routerEnabled = parsed.routerEnabled == true,
                        expiresAt = expires,
                        planName = parsed.planName.orEmpty(),
                        deviceId = EmeryDeviceIdentity.deviceId(),
                        deviceName = EmeryDeviceIdentity.deviceName(),
                    )
                )
            }
        } catch (_: IOException) {
            Result.failure(IllegalStateException("network"))
        }
    }

    /**
     * Returns import blob for [com.v2ray.ang.handler.AngConfigManager.importBatchConfig], or failure.
     * Soft failures: no allocation / no config payload (orchestrator has nothing v2rayNG can import yet).
     */
    suspend fun fetchVpnConfigImportText(accessKey: String): Result<String> = withContext(Dispatchers.IO) {
        val key = accessKey.trim()
        if (key.isEmpty()) return@withContext Result.failure(IllegalStateException("bad_request"))
        val request = authorizedGet("/vpn/config", key)
        try {
            client.newCall(request).execute().use { response ->
                val raw = response.body?.string().orEmpty()
                val parsed = JsonUtil.fromJson(raw, VpnConfigApiResponseBody::class.java)
                if (response.code == 401) {
                    return@withContext Result.failure(IllegalStateException(parsed?.error ?: "invalid_or_expired_key"))
                }
                if (response.code == 403) {
                    return@withContext Result.failure(IllegalStateException(parsed?.error ?: "vpn_disabled"))
                }
                if (response.code == 404) {
                    return@withContext Result.failure(IllegalStateException(parsed?.error ?: "no_allocation"))
                }
                if (!response.isSuccessful) {
                    return@withContext Result.failure(IllegalStateException(parsed?.error ?: "http_${response.code}"))
                }
                val text = parsed?.importText?.trim().orEmpty()
                if (text.isEmpty()) {
                    return@withContext Result.failure(IllegalStateException("parse_error"))
                }
                Result.success(text)
            }
        } catch (_: IOException) {
            Result.failure(IllegalStateException("network"))
        }
    }

    suspend fun fetchVpnServers(): Result<List<BackendServer>> = withContext(Dispatchers.IO) {
        var lastError: Throwable? = null
        serverListPaths.forEach { path ->
            val result = fetchVpnServersFromPath(path)
            if (result.isSuccess) {
                return@withContext result
            }
            lastError = result.exceptionOrNull()
        }
        Result.failure(lastError ?: IllegalStateException("server_list_unavailable"))
    }

    private fun fetchVpnServersFromPath(path: String): Result<List<BackendServer>> {
        val request = Request.Builder()
            .url("${baseUrl()}$path")
            .get()
            .build()
        return try {
            client.newCall(request).execute().use { response ->
                val raw = response.body?.string().orEmpty()
                if (!response.isSuccessful) {
                    return Result.failure(IllegalStateException("$path:http_${response.code}"))
                }
                val mapped = parseVpnServers(raw)
                if (mapped.isEmpty()) {
                    return Result.failure(IllegalStateException("$path:empty_or_parse_error"))
                }
                Result.success(mapped)
            }
        } catch (_: IOException) {
            Result.failure(IllegalStateException("$path:network"))
        } catch (e: Exception) {
            Result.failure(IllegalStateException("$path:${e.message ?: "parse_error"}"))
        }
    }

    private fun parseVpnServers(raw: String): List<BackendServer> {
        val array = serverArrayFrom(raw) ?: return emptyList()
        return buildList {
            for (index in 0 until array.length()) {
                val item = array.optJSONObject(index) ?: continue
                val id = item.optLongCompat("id", "server_id", "serverId", "pk") ?: continue
                val city = item.optStringCompat("city", "location", "name", "title", "country").ifBlank { "Server #$id" }
                val healthStatus = item.optStringCompat("health_status", "healthStatus", "status", "state").ifBlank { "available" }
                val isAvailable = item.optBooleanCompat("is_available", "isAvailable", "available", "enabled")
                    ?: healthStatus.lowercase() !in setOf("down", "offline", "disabled", "unavailable", "unhealthy", "maintenance")
                add(
                    BackendServer(
                        id = id,
                        city = city,
                        healthStatus = healthStatus,
                        isAvailable = isAvailable,
                    )
                )
            }
        }
    }

    private fun serverArrayFrom(raw: String): JSONArray? {
        val trimmed = raw.trim()
        if (trimmed.isEmpty()) return null
        return try {
            if (trimmed.startsWith("[")) {
                JSONArray(trimmed)
            } else {
                val root = JSONObject(trimmed)
                root.optJSONArray("servers")
                    ?: root.optJSONArray("items")
                    ?: root.optJSONArray("data")
                    ?: root.optJSONArray("results")
                    ?: root.optJSONObject("data")?.optJSONArray("servers")
                    ?: root.optJSONObject("data")?.optJSONArray("items")
                    ?: root.optJSONObject("result")?.optJSONArray("servers")
                    ?: root.optJSONObject("result")?.optJSONArray("items")
            }
        } catch (_: Exception) {
            null
        }
    }

    private fun JSONObject.optStringCompat(vararg keys: String): String {
        keys.forEach { key ->
            if (has(key) && !isNull(key)) {
                val value = optString(key).trim()
                if (value.isNotBlank()) return value
            }
        }
        return ""
    }

    private fun JSONObject.optLongCompat(vararg keys: String): Long? {
        keys.forEach { key ->
            if (has(key) && !isNull(key)) {
                val longValue = optLong(key, Long.MIN_VALUE)
                if (longValue != Long.MIN_VALUE) return longValue
                optString(key).toLongOrNull()?.let { return it }
            }
        }
        return null
    }

    private fun JSONObject.optBooleanCompat(vararg keys: String): Boolean? {
        keys.forEach { key ->
            if (has(key) && !isNull(key)) {
                val raw = optString(key).trim().lowercase()
                when (raw) {
                    "true", "1", "yes", "online", "active", "available", "healthy", "up" -> return true
                    "false", "0", "no", "offline", "inactive", "unavailable", "unhealthy", "down", "disabled" -> return false
                }
                return optBoolean(key)
            }
        }
        return null
    }

    suspend fun connectServer(accessKey: String, serverId: Long): Result<ConnectPayload> = withContext(Dispatchers.IO) {
        val key = accessKey.trim()
        if (key.isEmpty() || serverId <= 0L) return@withContext Result.failure(IllegalStateException("bad_request"))
        val bodyJson = JsonUtil.toJson(VpnConnectRequestBody(accessKey = key, serverId = serverId)) ?: "{}"
        val request = authorizedPost("/api/v1/vpn/connect", key, bodyJson)
        try {
            client.newCall(request).execute().use { response ->
                val raw = response.body?.string().orEmpty()
                val parsed = JsonUtil.fromJson(raw, VpnConnectApiResponseBody::class.java)
                val detail = try {
                    JSONObject(raw).optString("detail")
                } catch (_: Exception) {
                    ""
                }
                if (response.code == 401) {
                    return@withContext Result.failure(IllegalStateException(parsed?.error ?: detail.ifBlank { "invalid_or_expired_key" }))
                }
                if (response.code == 404) {
                    return@withContext Result.failure(IllegalStateException(parsed?.error ?: detail.ifBlank { "server_not_found" }))
                }
                if (response.code == 409) {
                    return@withContext Result.failure(IllegalStateException(parsed?.error ?: detail.ifBlank { "server_config_unavailable" }))
                }
                if (!response.isSuccessful) {
                    return@withContext Result.failure(IllegalStateException(parsed?.error ?: detail.ifBlank { "http_${response.code}" }))
                }
                val importText = parsed?.importText?.trim().orEmpty()
                if (importText.isEmpty()) {
                    return@withContext Result.failure(IllegalStateException("server_config_unavailable"))
                }
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
}
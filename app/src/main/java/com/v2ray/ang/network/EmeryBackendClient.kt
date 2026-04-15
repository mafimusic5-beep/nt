package com.v2ray.ang.network

import android.util.Log
import com.v2ray.ang.AppConfig
import com.v2ray.ang.dto.ProfileApiResponseBody
import com.v2ray.ang.dto.VpnConnectApiResponseBody
import com.v2ray.ang.dto.VpnConfigApiResponseBody
import com.v2ray.ang.dto.VpnServerItemApiResponseBody
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
import org.json.JSONObject

object EmeryBackendClient {

    private val client = OkHttpClient.Builder()
        .connectTimeout(20, TimeUnit.SECONDS)
        .readTimeout(30, TimeUnit.SECONDS)
        .writeTimeout(20, TimeUnit.SECONDS)
        .build()

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
        val request = Request.Builder()
            .url("${baseUrl()}/api/v1/vpn/servers")
            .get()
            .build()
        try {
            client.newCall(request).execute().use { response ->
                val raw = response.body?.string().orEmpty()
                if (!response.isSuccessful) {
                    return@withContext Result.failure(IllegalStateException("http_${response.code}"))
                }
                val parsed = JsonUtil.fromJson(raw, Array<VpnServerItemApiResponseBody>::class.java)?.toList()
                    ?: return@withContext Result.failure(IllegalStateException("parse_error"))
                val mapped = parsed.map {
                    BackendServer(
                        id = it.id,
                        city = it.city?.ifBlank { "Unknown" } ?: "Unknown",
                        healthStatus = it.healthStatus ?: "unknown",
                        isAvailable = it.isAvailable != false,
                    )
                }
                Result.success(mapped)
            }
        } catch (_: IOException) {
            Result.failure(IllegalStateException("network"))
        }
    }

    suspend fun connectServer(accessKey: String, serverId: Long): Result<ConnectPayload> = withContext(Dispatchers.IO) {
        val key = accessKey.trim()
        if (key.isEmpty() || serverId <= 0L) return@withContext Result.failure(IllegalStateException("bad_request"))
        val bodyJson = JSONObject()
            .put("access_key", key)
            .put("server_id", serverId)
            .put("device_fingerprint", EmeryDeviceIdentity.deviceId())
            .put("platform", "android")
            .put("device_name", EmeryDeviceIdentity.deviceName())
            .toString()
        val request = authorizedPost("/api/v1/vpn/connect", key, bodyJson)
        try {
            client.newCall(request).execute().use { response ->
                val raw = response.body?.string().orEmpty()
                val parsed = runCatching { JsonUtil.fromJson(raw, VpnConnectApiResponseBody::class.java) }.getOrNull()
                val detail = runCatching { JSONObject(raw).optString("detail") }.getOrDefault("")
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
                    Log.e(AppConfig.TAG, "Emery connect parse failed: empty import_text, raw=$raw")
                    return@withContext Result.failure(IllegalStateException("parse_error"))
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
        } catch (t: Throwable) {
            Log.e(AppConfig.TAG, "Emery connect crashed", t)
            Result.failure(IllegalStateException(t.message ?: t.javaClass.simpleName))
        }
    }
}

package com.v2ray.ang.network

import com.v2ray.ang.BuildConfig
import com.v2ray.ang.handler.EmeryAccessProfile
import com.v2ray.ang.handler.EmeryApiConfig
import com.v2ray.ang.security.EmeryDeviceIdentity
import com.v2ray.ang.util.AgentDebugNdjsonLogger
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.IOException
import java.util.concurrent.TimeUnit
import org.json.JSONObject

object EmeryAuthClient {

    private val jsonMedia = "application/json; charset=utf-8".toMediaType()
    private val client = OkHttpClient.Builder()
        .connectTimeout(20, TimeUnit.SECONDS)
        .readTimeout(20, TimeUnit.SECONDS)
        .writeTimeout(20, TimeUnit.SECONDS)
        .build()

    private fun baseUrl(): String = EmeryApiConfig.baseUrl()

    /**
     * POST /auth/key with device-bound proof.
     * The backend should verify:
     * 1) the access key was issued by the bot/backend;
     * 2) the device public key/signature pair is valid;
     * 3) the key has not exceeded its allowed device count.
     */
    suspend fun verifyAccessKey(key: String): Result<EmeryAccessProfile> = withContext(Dispatchers.IO) {
        val trimmed = key.trim()
        if (trimmed.isEmpty()) {
            return@withContext Result.failure(IllegalStateException("bad_request"))
        }
        val path = "/auth/key"
        val proof = EmeryDeviceIdentity.buildActivationProof(path = path, accessKey = trimmed)
        AgentDebugNdjsonLogger.log(
            hypothesisId = "H2",
            location = "EmeryAuthClient.kt:verifyAccessKey",
            message = "verify_request_prepared",
            runId = "device-bound",
            data = JSONObject()
                .put("baseUrl", baseUrl())
                .put("urlPath", path)
                .put("keyLen", trimmed.length)
                .put("deviceId", proof.deviceId)
                .put("deviceName", proof.deviceName),
        )

        val bodyJson = JSONObject()
            .put("key", trimmed)
            .put("access_key", trimmed)
            .put("device_id", proof.deviceId)
            .put("device_name", proof.deviceName)
            .put("client_public_key", proof.publicKeyBase64)
            .put("timestamp", proof.timestampMillis)
            .put("nonce", proof.nonce)
            .put("signature", proof.signatureBase64)
            .put("signature_algorithm", proof.signatureAlgorithm)
            .put("client_platform", "android")
            .put("app_version", BuildConfig.VERSION_NAME)
            .toString()

        val request = Request.Builder()
            .url("${baseUrl()}$path")
            .header("X-Emery-Device-Id", proof.deviceId)
            .header("X-Emery-Timestamp", proof.timestampMillis)
            .header("X-Emery-Nonce", proof.nonce)
            .header("X-Emery-Signature", proof.signatureBase64)
            .header("X-Emery-Signature-Algorithm", proof.signatureAlgorithm)
            .post(bodyJson.toRequestBody(jsonMedia))
            .build()

        try {
            client.newCall(request).execute().use { response ->
                val raw = response.body?.string().orEmpty()
                val parsed = runCatching { JSONObject(raw) }.getOrNull()
                    ?: return@withContext Result.failure(IllegalStateException("parse_error"))

                AgentDebugNdjsonLogger.log(
                    hypothesisId = "H1",
                    location = "EmeryAuthClient.kt:verifyAccessKey",
                    message = "verify_response_parsed",
                    runId = "device-bound",
                    data = JSONObject()
                        .put("httpCode", response.code)
                        .put("valid", if (parsed.has("valid")) parsed.optBoolean("valid", true) else true)
                        .put("error", parsed.optString("error"))
                        .put("deviceId", parsed.optString("device_id"))
                        .put("devicesUsed", parsed.optInt("devices_used", parsed.optInt("devicesUsed", -1)))
                        .put("devicesLimit", parsed.optInt("devices_limit", parsed.optInt("devicesLimit", -1))),
                )

                if (response.code == 409) {
                    val err = parsed.optString("error").ifBlank { "device_limit_reached" }
                    return@withContext Result.failure(IllegalStateException(err))
                }
                if (response.code == 401 || response.code == 403) {
                    val err = parsed.optString("error").ifBlank { "invalid_or_expired_key" }
                    return@withContext Result.failure(IllegalStateException(err))
                }
                if (!response.isSuccessful) {
                    val err = parsed.optString("error").ifBlank { "http_${response.code}" }
                    return@withContext Result.failure(IllegalStateException(err))
                }

                val valid = if (parsed.has("valid")) parsed.optBoolean("valid", true) else true
                if (!valid) {
                    val err = parsed.optString("error").ifBlank { "invalid_or_expired_key" }
                    return@withContext Result.failure(IllegalStateException(err))
                }

                val expires = parsed.optString("expires_at").ifBlank { parsed.optString("expiresAt") }
                if (expires.isBlank()) {
                    return@withContext Result.failure(IllegalStateException("parse_error"))
                }

                val profile = EmeryAccessProfile(
                    accessKey = trimmed,
                    vpnEnabled = parsed.optBoolean("vpn_enabled", parsed.optBoolean("vpnEnabled", false)),
                    routerEnabled = parsed.optBoolean("router_enabled", parsed.optBoolean("routerEnabled", false)),
                    expiresAt = expires,
                    planName = parsed.optString("plan_name").ifBlank { parsed.optString("planName") },
                    deviceId = parsed.optString("device_id").ifBlank { proof.deviceId },
                    deviceName = parsed.optString("device_name").ifBlank { proof.deviceName },
                    devicesUsed = parsed.optInt("devices_used", parsed.optInt("devicesUsed", 1)),
                    devicesLimit = parsed.optInt("devices_limit", parsed.optInt("devicesLimit", 5)),
                )
                Result.success(profile)
            }
        } catch (_: IOException) {
            Result.failure(IllegalStateException("network"))
        }
    }
}

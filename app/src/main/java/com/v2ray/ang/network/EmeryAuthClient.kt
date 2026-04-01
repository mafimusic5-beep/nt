package com.v2ray.ang.network

import com.v2ray.ang.dto.AuthKeyRequestBody
import com.v2ray.ang.dto.AuthKeyResponseBody
import com.v2ray.ang.handler.EmeryAccessProfile
import com.v2ray.ang.handler.EmeryApiConfig
import com.v2ray.ang.util.AgentDebugNdjsonLogger
import com.v2ray.ang.util.JsonUtil
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
     * POST /auth/key. Returns [Result] with [EmeryAccessProfile] or failure with a stable reason string.
     */
    suspend fun verifyAccessKey(key: String): Result<EmeryAccessProfile> = withContext(Dispatchers.IO) {
        val trimmed = key.trim()
        if (trimmed.isEmpty()) {
            return@withContext Result.failure(IllegalStateException("bad_request"))
        }
        val url = "${baseUrl()}/auth/key"
        // #region agent log
        AgentDebugNdjsonLogger.log(
            hypothesisId = "H2",
            location = "EmeryAuthClient.kt:verifyAccessKey",
            message = "verify_request_prepared",
            runId = "pre-fix",
            data = JSONObject()
                .put("baseUrl", baseUrl())
                .put("urlPath", "/auth/key")
                .put("keyLen", trimmed.length),
        )
        // #endregion
        val bodyJson = JsonUtil.toJson(AuthKeyRequestBody(trimmed))
        val request = Request.Builder()
            .url(url)
            .post(bodyJson.toRequestBody(jsonMedia))
            .build()
        try {
            client.newCall(request).execute().use { response ->
                val raw = response.body?.string().orEmpty()
                val parsed = JsonUtil.fromJson(raw, AuthKeyResponseBody::class.java)
                    ?: return@withContext Result.failure(IllegalStateException("parse_error"))
                // #region agent log
                AgentDebugNdjsonLogger.log(
                    hypothesisId = "H1",
                    location = "EmeryAuthClient.kt:verifyAccessKey",
                    message = "verify_response_parsed",
                    runId = "pre-fix",
                    data = JSONObject()
                        .put("httpCode", response.code)
                        .put("valid", parsed.valid)
                        .put("error", parsed.error ?: "")
                        .put("vpnEnabled", parsed.vpnEnabled == true)
                        .put("routerEnabled", parsed.routerEnabled == true)
                        .put("hasExpiresAt", !parsed.expiresAt.isNullOrBlank())
                        .put("planNameBlank", parsed.planName.isNullOrBlank()),
                )
                // #endregion
                if (!parsed.valid) {
                    val err = parsed.error ?: "invalid_or_expired_key"
                    return@withContext Result.failure(IllegalStateException(err))
                }
                val expires = parsed.expiresAt.orEmpty()
                val plan = parsed.planName.orEmpty()
                if (expires.isBlank()) {
                    return@withContext Result.failure(IllegalStateException("parse_error"))
                }
                val profile = EmeryAccessProfile(
                    accessKey = trimmed,
                    vpnEnabled = parsed.vpnEnabled == true,
                    routerEnabled = parsed.routerEnabled == true,
                    expiresAt = expires,
                    planName = plan,
                )
                Result.success(profile)
            }
        } catch (_: IOException) {
            Result.failure(IllegalStateException("network"))
        }
    }
}

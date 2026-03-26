package com.v2ray.ang.network

import com.v2ray.ang.BuildConfig
import com.v2ray.ang.dto.ProfileApiResponseBody
import com.v2ray.ang.dto.VpnConfigApiResponseBody
import com.v2ray.ang.handler.EmeryAccessProfile
import com.v2ray.ang.util.JsonUtil
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.IOException
import java.util.concurrent.TimeUnit

/**
 * Authenticated Emery API calls after the access key is known.
 * Uses Authorization: Bearer &lt;access key&gt;.
 */
object EmeryBackendClient {

    private val client = OkHttpClient.Builder()
        .connectTimeout(20, TimeUnit.SECONDS)
        .readTimeout(30, TimeUnit.SECONDS)
        .writeTimeout(20, TimeUnit.SECONDS)
        .build()

    private fun baseUrl(): String = BuildConfig.EMERY_API_BASE_URL.trimEnd('/')

    private fun authorizedGet(path: String, accessKey: String): Request =
        Request.Builder()
            .url("${baseUrl()}$path")
            .header("Authorization", "Bearer $accessKey")
            .get()
            .build()

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
}

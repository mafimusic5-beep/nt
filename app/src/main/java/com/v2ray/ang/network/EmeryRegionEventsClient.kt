package com.v2ray.ang.network

import com.v2ray.ang.handler.EmeryApiConfig
import com.v2ray.ang.security.AppSecurity
import com.v2ray.ang.security.EmeryDeviceIdentity
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.Request
import org.json.JSONObject
import java.io.IOException
import java.net.URLEncoder
import java.nio.charset.StandardCharsets
import java.util.concurrent.TimeUnit

/**
 * Backend-driven region updates with a safe reconciliation fallback.
 *
 * The backend should return a new region revision when countries/servers are added or removed.
 * Some deployed backends may not expose the event endpoint yet, so this client deliberately
 * returns a synthetic revision on event timeout/404/network failure. The ViewModel will then
 * refresh the actual server list and remove stale regions from the UI.
 */
object EmeryRegionEventsClient {

    private const val REGION_REVISION_PATH = "/api/v1/vpn/regions/revision"
    private const val REGION_EVENTS_PATH = "/api/v1/vpn/regions/events"

    private val client = AppSecurity.hardenedOkHttpBuilder()
        .connectTimeout(12, TimeUnit.SECONDS)
        .readTimeout(18, TimeUnit.SECONDS)
        .writeTimeout(12, TimeUnit.SECONDS)
        .callTimeout(24, TimeUnit.SECONDS)
        .build()

    private fun baseUrl(): String = EmeryApiConfig.baseUrl()

    private fun premiumApiBlocked(): IllegalStateException? {
        return AppSecurity.premiumApiBlockReason()?.let(::IllegalStateException)
    }

    suspend fun fetchRegionsRevision(accessKey: String): Result<String> = withContext(Dispatchers.IO) {
        premiumApiBlocked()?.let { return@withContext Result.failure(it) }
        val key = accessKey.trim()
        if (key.isEmpty()) return@withContext Result.failure(IllegalStateException("bad_request"))
        executeRevisionRequest(REGION_REVISION_PATH, key, allowSyntheticRevision = true)
    }

    suspend fun awaitRegionsChanged(accessKey: String, sinceRevision: String): Result<String> = withContext(Dispatchers.IO) {
        premiumApiBlocked()?.let { return@withContext Result.failure(it) }
        val key = accessKey.trim()
        if (key.isEmpty()) return@withContext Result.failure(IllegalStateException("bad_request"))
        val encodedSince = URLEncoder.encode(sinceRevision.trim(), StandardCharsets.UTF_8.name())
        executeRevisionRequest("$REGION_EVENTS_PATH?since=$encodedSince", key, allowSyntheticRevision = true)
    }

    private fun executeRevisionRequest(
        pathWithOptionalQuery: String,
        accessKey: String,
        allowSyntheticRevision: Boolean,
    ): Result<String> {
        val signingPath = pathWithOptionalQuery.substringBefore('?')
        val request = authorizedGet(pathWithOptionalQuery, signingPath, accessKey)
        val isEventWait = signingPath == REGION_EVENTS_PATH
        return try {
            client.newCall(request).execute().use { response ->
                val raw = response.body?.string().orEmpty()
                if (response.code == 401 || response.code == 403) {
                    return Result.failure(IllegalStateException(errorFrom(raw, "invalid_or_expired_key")))
                }
                if (!response.isSuccessful) {
                    return if (allowSyntheticRevision) {
                        Result.success(syntheticRevision("http_${response.code}"))
                    } else {
                        Result.failure(IllegalStateException(errorFrom(raw, "http_${response.code}")))
                    }
                }
                val json = JSONObject(raw)
                val revision = json.optString("revision").trim()
                if (revision.isBlank()) {
                    return if (allowSyntheticRevision) {
                        Result.success(syntheticRevision("missing_revision"))
                    } else {
                        Result.failure(IllegalStateException("missing_region_revision"))
                    }
                }

                // Long-poll timeout normally returns changed=false with the same revision.
                // Force a lightweight reconciliation so deleted backend regions disappear
                // even when the backend event does not fire correctly.
                if (isEventWait && json.has("changed") && !json.optBoolean("changed", true)) {
                    return Result.success(syntheticRevision("unchanged_$revision"))
                }

                Result.success(revision)
            }
        } catch (_: IOException) {
            if (allowSyntheticRevision) {
                Result.success(syntheticRevision("network"))
            } else {
                Result.failure(IllegalStateException("network"))
            }
        } catch (_: Exception) {
            if (allowSyntheticRevision) {
                Result.success(syntheticRevision("parse"))
            } else {
                Result.failure(IllegalStateException("parse_error"))
            }
        }
    }

    private fun syntheticRevision(reason: String): String {
        return "sync_${reason}_${System.currentTimeMillis()}"
    }

    private fun authorizedGet(pathWithOptionalQuery: String, signingPath: String, accessKey: String): Request {
        val credential = accessKey.trim()
        val proof = EmeryDeviceIdentity.buildRequestProof(method = "GET", path = signingPath, authSecret = credential)
        return Request.Builder()
            .url("${baseUrl()}$pathWithOptionalQuery")
            .header("Authorization", "Bearer $credential")
            .header("X-Emery-Device-Id", proof.deviceId)
            .header("X-Emery-Timestamp", proof.timestampMillis)
            .header("X-Emery-Nonce", proof.nonce)
            .header("X-Emery-Signature", proof.signatureBase64)
            .header("X-Emery-Signature-Algorithm", proof.signatureAlgorithm)
            .withSkryonSecurityHeaders()
            .get()
            .build()
    }

    private fun Request.Builder.withSkryonSecurityHeaders(): Request.Builder {
        AppSecurity.securityHeaders().forEach { (name, value) ->
            header(name, value)
        }
        return this
    }

    private fun errorFrom(raw: String, fallback: String): String {
        return try {
            val json = JSONObject(raw)
            json.optString("error").ifBlank { json.optString("detail").ifBlank { fallback } }
        } catch (_: Exception) {
            fallback
        }
    }
}

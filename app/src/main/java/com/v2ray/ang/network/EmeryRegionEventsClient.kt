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
 * Event-driven region updates.
 *
 * The app does not periodically download the whole region list. It keeps a lightweight
 * long-poll channel open. Backend returns only when its region revision changes, then the
 * ViewModel refreshes the full region list once.
 */
object EmeryRegionEventsClient {

    private const val REGION_REVISION_PATH = "/api/v1/vpn/regions/revision"
    private const val REGION_EVENTS_PATH = "/api/v1/vpn/regions/events"

    private val client = AppSecurity.hardenedOkHttpBuilder()
        .connectTimeout(20, TimeUnit.SECONDS)
        .readTimeout(75, TimeUnit.SECONDS)
        .writeTimeout(20, TimeUnit.SECONDS)
        .callTimeout(85, TimeUnit.SECONDS)
        .build()

    private fun baseUrl(): String = EmeryApiConfig.baseUrl()

    private fun premiumApiBlocked(): IllegalStateException? {
        return AppSecurity.premiumApiBlockReason()?.let(::IllegalStateException)
    }

    suspend fun fetchRegionsRevision(accessKey: String): Result<String> = withContext(Dispatchers.IO) {
        premiumApiBlocked()?.let { return@withContext Result.failure(it) }
        val key = accessKey.trim()
        if (key.isEmpty()) return@withContext Result.failure(IllegalStateException("bad_request"))
        executeRevisionRequest(REGION_REVISION_PATH, key)
    }

    suspend fun awaitRegionsChanged(accessKey: String, sinceRevision: String): Result<String> = withContext(Dispatchers.IO) {
        premiumApiBlocked()?.let { return@withContext Result.failure(it) }
        val key = accessKey.trim()
        if (key.isEmpty()) return@withContext Result.failure(IllegalStateException("bad_request"))
        val encodedSince = URLEncoder.encode(sinceRevision.trim(), StandardCharsets.UTF_8.name())
        executeRevisionRequest("$REGION_EVENTS_PATH?since=$encodedSince", key)
    }

    private fun executeRevisionRequest(pathWithOptionalQuery: String, accessKey: String): Result<String> {
        val signingPath = pathWithOptionalQuery.substringBefore('?')
        val request = authorizedGet(pathWithOptionalQuery, signingPath, accessKey)
        return try {
            client.newCall(request).execute().use { response ->
                val raw = response.body?.string().orEmpty()
                if (response.code == 401 || response.code == 403) {
                    return Result.failure(IllegalStateException(errorFrom(raw, "invalid_or_expired_key")))
                }
                if (!response.isSuccessful) {
                    return Result.failure(IllegalStateException(errorFrom(raw, "http_${response.code}")))
                }
                val revision = JSONObject(raw).optString("revision").trim()
                if (revision.isBlank()) {
                    return Result.failure(IllegalStateException("missing_region_revision"))
                }
                Result.success(revision)
            }
        } catch (_: IOException) {
            Result.failure(IllegalStateException("network"))
        } catch (_: Exception) {
            Result.failure(IllegalStateException("parse_error"))
        }
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

package com.v2ray.ang.network

import com.v2ray.ang.handler.EmeryApiConfig
import com.v2ray.ang.security.EmeryDeviceIdentity
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException
import java.util.concurrent.TimeUnit

/**
 * Skryon/Emery pool client.
 *
 * The app should not wait for a personal VPS. After access-key activation it requests the
 * complete public server pool that is already online. The backend is responsible for adding
 * the user's UUID to every allowed Xray/v2rayNG server and for hiding overloaded servers.
 */
object EmeryPoolClient {

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

    suspend fun fetchPoolImportText(accessKey: String): Result<String> = withContext(Dispatchers.IO) {
        val key = accessKey.trim()
        if (key.isEmpty()) return@withContext Result.failure(IllegalStateException("bad_request"))

        val paths = listOf(
            "/api/v1/vpn/pool/config",
            "/api/v1/vpn/pool/configs",
            "/vpn/pool/config",
            "/vpn/pool/configs",
        )

        var lastError: String = "pool_endpoint_unavailable"
        for (path in paths) {
            try {
                client.newCall(authorizedGet(path, key)).execute().use { response ->
                    val raw = response.body?.string().orEmpty()
                    if (response.code == 401) {
                        return@withContext Result.failure(IllegalStateException(errorFrom(raw, "invalid_or_expired_key")))
                    }
                    if (response.code == 403) {
                        return@withContext Result.failure(IllegalStateException(errorFrom(raw, "vpn_disabled")))
                    }
                    if (response.code == 404) {
                        lastError = "pool_endpoint_unavailable"
                        return@use
                    }
                    if (!response.isSuccessful) {
                        lastError = errorFrom(raw, "http_${response.code}")
                        return@use
                    }

                    val importText = parseImportText(raw)
                    if (importText.isNotBlank()) {
                        return@withContext Result.success(importText)
                    }
                    lastError = "no_pool_import_text"
                }
            } catch (_: IOException) {
                return@withContext Result.failure(IllegalStateException("network"))
            }
        }

        Result.failure(IllegalStateException(lastError))
    }

    private fun errorFrom(raw: String, fallback: String): String {
        return try {
            val json = JSONObject(raw)
            json.optString("error").ifBlank { json.optString("detail").ifBlank { fallback } }
        } catch (_: Exception) {
            fallback
        }
    }

    private fun parseImportText(raw: String): String {
        val trimmed = raw.trim()
        if (trimmed.startsWith("vless://") || trimmed.startsWith("vmess://") || trimmed.startsWith("trojan://")) {
            return trimmed
        }

        return try {
            val root = JSONObject(trimmed)
            val direct = firstNonBlank(
                root.optString("importText"),
                root.optString("import_text"),
                root.optString("import"),
                root.optString("config"),
                root.optString("link"),
                root.optString("uri"),
            )
            if (direct.isNotBlank()) {
                direct
            } else {
                val links = mutableListOf<String>()
                collectLinks(root.optJSONArray("servers"), links)
                collectLinks(root.optJSONArray("regions"), links)
                collectLinks(root.optJSONObject("data")?.optJSONArray("servers"), links)
                collectLinks(root.optJSONObject("data")?.optJSONArray("regions"), links)
                collectLinks(root.optJSONObject("pool")?.optJSONArray("servers"), links)
                links.distinct().joinToString("\n")
            }
        } catch (_: Exception) {
            ""
        }
    }

    private fun collectLinks(items: JSONArray?, out: MutableList<String>) {
        if (items == null) return
        for (i in 0 until items.length()) {
            val item = items.optJSONObject(i) ?: continue
            val status = item.optString("status").lowercase()
            val available = item.optBoolean("available", item.optBoolean("is_available", true))
            val allowNewUsers = item.optBoolean("allow_new_users", item.optBoolean("allowNewUsers", true))
            if (!available || !allowNewUsers || status == "offline" || status == "maintenance" || status == "syncing") {
                continue
            }
            val link = firstNonBlank(
                item.optString("importText"),
                item.optString("import_text"),
                item.optString("config"),
                item.optString("link"),
                item.optString("uri"),
                item.optString("vless"),
            )
            if (link.isNotBlank()) out += link
        }
    }

    private fun firstNonBlank(vararg values: String?): String {
        return values.firstOrNull { !it.isNullOrBlank() }?.trim().orEmpty()
    }
}

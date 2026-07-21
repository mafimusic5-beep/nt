package com.v2ray.ang.network

import com.v2ray.ang.AppConfig
import com.v2ray.ang.BuildConfig
import com.v2ray.ang.handler.EmeryAccessProfile
import com.v2ray.ang.handler.EmeryDeviceRecord
import com.v2ray.ang.handler.validateDeviceLimit
import com.v2ray.ang.security.EmeryDeviceIdentity
import com.v2ray.ang.util.AgentDebugNdjsonLogger
import java.io.IOException
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject

object EmeryAuthClient {

    private const val PUBLIC_API_BASE_URL = "https://skryon.ru"
    private const val DEVICE_REGISTER_PATH = "/api/device/register"
    private const val LEGACY_ACTIVATION_PATH = "/api/activate"

    private val jsonMedia = "application/json; charset=utf-8".toMediaType()
    private val client = OkHttpClient.Builder()
        .connectTimeout(20, TimeUnit.SECONDS)
        .readTimeout(20, TimeUnit.SECONDS)
        .writeTimeout(20, TimeUnit.SECONDS)
        .retryOnConnectionFailure(true)
        .build()

    /**
     * Prefer the signed public device endpoint. During the backend rollout, fall back
     * to the existing /api/activate response, which already enforces the tariff slot
     * in the server database. The fallback never invents counters locally.
     */
    suspend fun verifyAccessKey(key: String): Result<EmeryAccessProfile> = withContext(Dispatchers.IO) {
        val trimmed = key.trim()
        if (trimmed.isEmpty()) {
            return@withContext Result.failure(IllegalStateException("bad_request"))
        }

        val secure = verifyViaDeviceEndpoint(trimmed)
        if (secure.isSuccess) {
            return@withContext secure
        }

        val reason = secure.exceptionOrNull()?.message.orEmpty()
        if (reason !in rolloutFallbackReasons) {
            return@withContext secure
        }

        verifyViaLegacyActivation(trimmed)
    }

    private fun verifyViaDeviceEndpoint(key: String): Result<EmeryAccessProfile> {
        val proof = EmeryDeviceIdentity.buildActivationProof(
            path = DEVICE_REGISTER_PATH,
            accessKey = key,
        )
        val bodyJson = JSONObject()
            .put("key", key)
            .put("access_key", key)
            .put("device_id", proof.deviceId)
            .put("device_name", proof.deviceName)
            .put("client_public_key", proof.publicKeyBase64)
            .put("timestamp", proof.timestampMillis)
            .put("nonce", proof.nonce)
            .put("signature", proof.signatureBase64)
            .put("signature_algorithm", proof.signatureAlgorithm)
            .put("client_platform", "android")
            .put("app_version", BuildConfig.VERSION_NAME)
            .put("app_version_code", BuildConfig.SKRYON_VERSION_CODE)
            .toString()

        val request = Request.Builder()
            .url(PUBLIC_API_BASE_URL + DEVICE_REGISTER_PATH)
            .header("Accept", "application/json")
            .header("X-Emery-Device-Id", proof.deviceId)
            .header("X-Emery-Timestamp", proof.timestampMillis)
            .header("X-Emery-Nonce", proof.nonce)
            .header("X-Emery-Signature", proof.signatureBase64)
            .header("X-Emery-Signature-Algorithm", proof.signatureAlgorithm)
            .header(AppConfig.SKRYON_APP_VERSION_HEADER, BuildConfig.SKRYON_VERSION_CODE.toString())
            .post(bodyJson.toRequestBody(jsonMedia))
            .build()

        return executeProfileRequest(
            request = request,
            accessKey = key,
            currentDeviceId = proof.deviceId,
            fallbackName = proof.deviceName,
            requireInventory = true,
        )
    }

    private fun verifyViaLegacyActivation(key: String): Result<EmeryAccessProfile> {
        val proof = EmeryDeviceIdentity.buildActivationProof(
            path = LEGACY_ACTIVATION_PATH,
            accessKey = key,
        )
        val bodyJson = JSONObject()
            .put("code", key)
            .put("deviceId", proof.deviceId)
            .put("deviceName", proof.deviceName)
            .put("device_id", proof.deviceId)
            .put("device_name", proof.deviceName)
            .put("client_public_key", proof.publicKeyBase64)
            .put("timestamp", proof.timestampMillis)
            .put("nonce", proof.nonce)
            .put("signature", proof.signatureBase64)
            .put("signature_algorithm", proof.signatureAlgorithm)
            .put("client_platform", "android")
            .put("appVersionCode", BuildConfig.SKRYON_VERSION_CODE)
            .toString()

        val request = Request.Builder()
            .url(PUBLIC_API_BASE_URL + LEGACY_ACTIVATION_PATH)
            .header("Accept", "application/json")
            .header("Connection", "close")
            .header("X-Emery-Device-Id", proof.deviceId)
            .header("X-Emery-Timestamp", proof.timestampMillis)
            .header("X-Emery-Nonce", proof.nonce)
            .header("X-Emery-Signature", proof.signatureBase64)
            .header("X-Emery-Signature-Algorithm", proof.signatureAlgorithm)
            .header(AppConfig.SKRYON_APP_VERSION_HEADER, BuildConfig.SKRYON_VERSION_CODE.toString())
            .post(bodyJson.toRequestBody(jsonMedia))
            .build()

        return try {
            client.newCall(request).execute().use { response ->
                val raw = response.body?.string().orEmpty()
                val parsed = runCatching { JSONObject(raw) }.getOrNull()

                if (response.code == 409) {
                    return Result.failure(
                        IllegalStateException(parsed?.serverError().orEmpty().ifBlank { "device_limit_reached" })
                    )
                }
                if (response.code == 429) {
                    return Result.failure(IllegalStateException("too_many_attempts"))
                }
                if (!response.isSuccessful || parsed == null) {
                    return Result.failure(
                        IllegalStateException(parsed?.serverError().orEmpty().ifBlank { "http_${response.code}" })
                    )
                }
                if (!parsed.optBoolean("ok", false)) {
                    return Result.failure(
                        IllegalStateException(parsed.serverError().ifBlank { "invalid_or_expired_key" })
                    )
                }

                val planName = parsed.optString("planTitle")
                    .ifBlank { parsed.optString("plan_name") }
                    .ifBlank { parsed.optString("plan") }
                val devicesUsed = parsed.optIntOrNull("devices_used", "devicesUsed", "usedDevices")
                    ?: return Result.failure(IllegalStateException("device_counter_missing"))
                val devicesLimit = parsed.optIntOrNull("devices_limit", "devicesLimit", "maxDevices")
                    ?: return Result.failure(IllegalStateException("device_counter_missing"))
                if (!validateDeviceLimit(planName, devicesUsed, devicesLimit)) {
                    return Result.failure(IllegalStateException("plan_limit_mismatch"))
                }

                val devices = parseDevices(
                    array = parsed.optJSONArray("devices"),
                    currentDeviceId = proof.deviceId,
                    fallbackName = proof.deviceName,
                ).ifEmpty {
                    legacyInventory(
                        currentDeviceId = proof.deviceId,
                        currentDeviceName = proof.deviceName,
                        devicesUsed = devicesUsed,
                    )
                }

                Result.success(
                    EmeryAccessProfile(
                        accessKey = key,
                        vpnEnabled = true,
                        routerEnabled = false,
                        expiresAt = parsed.optString("expires_at")
                            .ifBlank { parsed.optString("expiresAt") },
                        planName = planName,
                        deviceId = proof.deviceId,
                        deviceName = proof.deviceName,
                        devicesUsed = devicesUsed,
                        devicesLimit = devicesLimit,
                        devices = devices,
                    )
                )
            }
        } catch (_: IOException) {
            Result.failure(IllegalStateException("network"))
        }
    }

    private fun executeProfileRequest(
        request: Request,
        accessKey: String,
        currentDeviceId: String,
        fallbackName: String,
        requireInventory: Boolean,
    ): Result<EmeryAccessProfile> {
        AgentDebugNdjsonLogger.log(
            hypothesisId = "H2",
            location = "EmeryAuthClient.kt:executeProfileRequest",
            message = "public_device_registration_request",
            runId = "device-bound",
            data = JSONObject()
                .put("url", request.url.toString())
                .put("deviceId", currentDeviceId),
        )

        return try {
            client.newCall(request).execute().use { response ->
                val raw = response.body?.string().orEmpty()
                val parsed = runCatching { JSONObject(raw) }.getOrNull()
                    ?: return Result.failure(IllegalStateException("parse_error"))

                if (response.code == 409) {
                    return Result.failure(
                        IllegalStateException(parsed.serverError().ifBlank { "device_limit_reached" })
                    )
                }
                if (response.code == 401 || response.code == 403) {
                    return Result.failure(
                        IllegalStateException(parsed.serverError().ifBlank { "invalid_or_expired_key" })
                    )
                }
                if (!response.isSuccessful) {
                    return Result.failure(
                        IllegalStateException(parsed.serverError().ifBlank { "http_${response.code}" })
                    )
                }
                if (parsed.has("valid") && !parsed.optBoolean("valid", false)) {
                    return Result.failure(
                        IllegalStateException(parsed.serverError().ifBlank { "invalid_or_expired_key" })
                    )
                }
                if (parsed.has("device_registered") && !parsed.optBoolean("device_registered", false)) {
                    return Result.failure(IllegalStateException("device_not_registered"))
                }

                val expires = parsed.optString("expires_at").ifBlank { parsed.optString("expiresAt") }
                val serverDeviceId = parsed.optString("device_id")
                    .ifBlank { parsed.optString("deviceId") }
                    .trim()
                if (serverDeviceId.isBlank()) {
                    return Result.failure(IllegalStateException("device_confirmation_missing"))
                }
                if (serverDeviceId != currentDeviceId) {
                    return Result.failure(IllegalStateException("device_mismatch"))
                }

                val planName = parsed.optString("plan_name").ifBlank { parsed.optString("planName") }
                val devicesUsed = parsed.optIntOrNull("devices_used", "devicesUsed")
                    ?: return Result.failure(IllegalStateException("device_counter_missing"))
                val devicesLimit = parsed.optIntOrNull("devices_limit", "devicesLimit")
                    ?: return Result.failure(IllegalStateException("device_counter_missing"))
                if (!validateDeviceLimit(planName, devicesUsed, devicesLimit)) {
                    return Result.failure(IllegalStateException("plan_limit_mismatch"))
                }

                val devices = parseDevices(
                    array = parsed.optJSONArray("devices"),
                    currentDeviceId = currentDeviceId,
                    fallbackName = fallbackName,
                )
                if (requireInventory && devices.isEmpty()) {
                    return Result.failure(IllegalStateException("device_inventory_missing"))
                }
                if (devices.isNotEmpty() && devices.none { it.deviceId == currentDeviceId && it.active }) {
                    return Result.failure(IllegalStateException("device_inventory_mismatch"))
                }

                Result.success(
                    EmeryAccessProfile(
                        accessKey = accessKey,
                        vpnEnabled = parsed.optBoolean("vpn_enabled", parsed.optBoolean("vpnEnabled", true)),
                        routerEnabled = parsed.optBoolean("router_enabled", parsed.optBoolean("routerEnabled", false)),
                        expiresAt = expires,
                        planName = planName,
                        deviceId = serverDeviceId,
                        deviceName = parsed.optString("device_name").ifBlank {
                            parsed.optString("deviceName").ifBlank { fallbackName }
                        },
                        devicesUsed = devicesUsed,
                        devicesLimit = devicesLimit,
                        devices = devices,
                    )
                )
            }
        } catch (_: IOException) {
            Result.failure(IllegalStateException("network"))
        }
    }

    private fun legacyInventory(
        currentDeviceId: String,
        currentDeviceName: String,
        devicesUsed: Int,
    ): List<EmeryDeviceRecord> {
        return buildList {
            add(
                EmeryDeviceRecord(
                    deviceId = currentDeviceId,
                    deviceName = currentDeviceName,
                    platform = "android",
                    appVersion = BuildConfig.VERSION_NAME,
                    active = true,
                    isCurrent = true,
                )
            )
            for (index in 2..devicesUsed) {
                add(
                    EmeryDeviceRecord(
                        deviceId = "legacy-slot-$index",
                        deviceName = "Зарегистрированное устройство $index",
                        platform = "server",
                        active = true,
                        isCurrent = false,
                    )
                )
            }
        }
    }

    private fun JSONObject.serverError(): String {
        return optString("error")
            .ifBlank { optString("reason") }
            .ifBlank { optString("detail") }
    }

    private fun JSONObject.optIntOrNull(vararg names: String): Int? {
        names.forEach { name ->
            if (has(name) && !isNull(name)) {
                return optInt(name)
            }
        }
        return null
    }

    private fun parseDevices(
        array: JSONArray?,
        currentDeviceId: String,
        fallbackName: String,
    ): List<EmeryDeviceRecord> {
        if (array == null) return emptyList()
        return buildList {
            for (index in 0 until array.length()) {
                val item = array.optJSONObject(index) ?: continue
                val id = item.optString("device_id").ifBlank { item.optString("deviceId") }.trim()
                if (id.isBlank()) continue
                add(
                    EmeryDeviceRecord(
                        deviceId = id,
                        deviceName = item.optString("device_name").ifBlank {
                            item.optString("deviceName").ifBlank {
                                if (id == currentDeviceId) fallbackName else "Устройство"
                            }
                        },
                        platform = item.optString("platform", "android"),
                        appVersion = item.optString("app_version").ifBlank { item.optString("appVersion") },
                        firstSeenAt = item.optString("first_seen_at").ifBlank { item.optString("firstSeenAt") },
                        lastSeenAt = item.optString("last_seen_at").ifBlank { item.optString("lastSeenAt") },
                        active = if (item.has("active")) item.optBoolean("active", true) else true,
                        isCurrent = if (item.has("is_current")) {
                            item.optBoolean("is_current", id == currentDeviceId)
                        } else {
                            item.optBoolean("isCurrent", id == currentDeviceId)
                        },
                    )
                )
            }
        }
    }

    private val rolloutFallbackReasons = setOf(
        "network",
        "parse_error",
        "http_404",
        "http_405",
        "device_confirmation_missing",
        "device_inventory_missing",
    )
}

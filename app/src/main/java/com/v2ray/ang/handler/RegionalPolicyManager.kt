package com.v2ray.ang.handler

import android.content.Context
import com.v2ray.ang.AppConfig
import com.v2ray.ang.BuildConfig
import com.v2ray.ang.security.EmeryDeviceIdentity
import com.v2ray.ang.ui.premium.SKRYON_ACTIVATION_CODE_PREF
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject

internal enum class RegionalPolicyMode(val storageValue: String) {
    International("international"),
    Russia("russia"),
}

/**
 * Stores the user-selected regional policy.
 *
 * Regional restrictions are enforced on the assigned VPS. The Android client
 * deliberately does not download or maintain Russia restriction assets and it
 * does not install local RKN blackhole rules. Keeping the client routing on the
 * global preset also cleans up rules left behind by older app versions.
 */
internal object RegionalPolicyManager {
    private const val ROUTING_PRESET_GLOBAL = 2
    private const val POLICY_API_URL = "https://skryon.ru/api/policy"
    private const val POLICY_API_PATH = "/api/policy"

    private val policyClient by lazy {
        OkHttpClient.Builder()
            .connectTimeout(15, TimeUnit.SECONDS)
            .readTimeout(30, TimeUnit.SECONDS)
            .writeTimeout(15, TimeUnit.SECONDS)
            .retryOnConnectionFailure(true)
            .build()
    }

    fun readMode(): RegionalPolicyMode? {
        return when (MmkvManager.decodeSettingsString(AppConfig.PREF_REGIONAL_POLICY_MODE)) {
            RegionalPolicyMode.International.storageValue -> RegionalPolicyMode.International
            RegionalPolicyMode.Russia.storageValue -> RegionalPolicyMode.Russia
            else -> null
        }
    }

    fun isRussiaModeEnabled(): Boolean = readMode() == RegionalPolicyMode.Russia

    suspend fun apply(context: Context, mode: RegionalPolicyMode): Result<Unit> = runCatching {
        applyServerPolicyIfActivated(mode)
        configureClientRouting(context.applicationContext, mode)
    }

    suspend fun prepareForConnection(context: Context): Result<Unit> = runCatching {
        val mode = readMode() ?: RegionalPolicyMode.International
        // Re-assert the server-side mode before every connection. This keeps the
        // selected policy authoritative even after an Xray/backend restart and
        // never downloads policy assets to Android.
        applyServerPolicyIfActivated(mode)
        configureClientRouting(context.applicationContext, mode)
    }

    fun isPolicyReadyForServiceStart(context: Context): Boolean {
        // No policy assets are required on Android anymore. The mode is sent to
        // the backend and enforced on the assigned VPS.
        return true
    }

    private suspend fun applyServerPolicyIfActivated(mode: RegionalPolicyMode) {
        val accessKey = MmkvManager.decodeSettingsString(SKRYON_ACTIVATION_CODE_PREF, "")
            ?.trim()
            .orEmpty()
        if (accessKey.isBlank()) {
            return
        }

        withContext(Dispatchers.IO) {
            val proof = EmeryDeviceIdentity.buildRequestProof(
                method = "POST",
                path = POLICY_API_PATH,
                authSecret = accessKey,
            )
            val body = JSONObject()
                .put("code", accessKey)
                .put("deviceId", proof.deviceId)
                .put("trafficPolicy", mode.storageValue)
                .put("appVersionCode", BuildConfig.SKRYON_VERSION_CODE)
                .toString()
            val request = Request.Builder()
                .url(POLICY_API_URL)
                .post(body.toRequestBody("application/json; charset=utf-8".toMediaType()))
                .header("Accept", "application/json")
                .header("X-Emery-Device-Id", proof.deviceId)
                .header("X-Emery-Timestamp", proof.timestampMillis)
                .header("X-Emery-Nonce", proof.nonce)
                .header("X-Emery-Signature", proof.signatureBase64)
                .header("X-Emery-Signature-Algorithm", proof.signatureAlgorithm)
                .header(AppConfig.SKRYON_APP_VERSION_HEADER, BuildConfig.SKRYON_VERSION_CODE.toString())
                .build()

            policyClient.newCall(request).execute().use { response ->
                val raw = response.body?.string().orEmpty()
                val json = runCatching { JSONObject(raw) }.getOrNull()
                val ok = response.isSuccessful && json?.optBoolean("ok", false) == true
                if (!ok) {
                    val reason = json?.optString("reason").orEmpty()
                        .ifBlank { json?.optString("error").orEmpty() }
                        .ifBlank { json?.optString("detail").orEmpty() }
                        .ifBlank { "http_${response.code}" }
                    throw IllegalStateException(reason)
                }
            }
        }
    }

    private fun configureClientRouting(context: Context, mode: RegionalPolicyMode) {
        SettingsManager.resetRoutingRulesetsFromPresets(
            context.applicationContext,
            ROUTING_PRESET_GLOBAL,
        )
        MmkvManager.encodeSettings(AppConfig.PREF_REGIONAL_POLICY_MODE, mode.storageValue)
    }
}

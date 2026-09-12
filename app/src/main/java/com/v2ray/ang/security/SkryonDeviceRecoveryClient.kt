package com.v2ray.ang.security

import android.content.Context
import java.io.IOException
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject

object SkryonDeviceRecoveryClient {

    private const val BASE_URL = "https://skryon.ru"
    private const val CHALLENGE_PATH = "/api/device/recovery/challenge"
    private const val CONFIRM_PATH = "/api/device/recovery/confirm"

    private val client = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(25, TimeUnit.SECONDS)
        .writeTimeout(15, TimeUnit.SECONDS)
        .callTimeout(40, TimeUnit.SECONDS)
        .retryOnConnectionFailure(true)
        .build()

    /**
     * Safe to call before every explicit activation attempt.
     * New devices receive status=not_needed and continue through the normal slot flow.
     * A reinstall of a known device performs an integrity-gated key rotation, then the
     * caller can retry the normal activation request without consuming another slot.
     */
    suspend fun recoverIfNeeded(context: Context, accessKey: String): Result<Unit> = withContext(Dispatchers.IO) {
        val key = accessKey.trim()
        if (key.isBlank()) return@withContext Result.failure(IllegalArgumentException("bad_request"))

        try {
            val challengeProof = EmeryDeviceIdentity.buildRecoveryChallengeProof(key)
            val challengeBody = JSONObject()
                .put("code", key)
                .put("device_id", challengeProof.deviceId)
                .put("client_public_key", challengeProof.publicKeyBase64)
                .put("timestamp", challengeProof.timestampMillis)
                .put("nonce", challengeProof.nonce)
                .put("signature", challengeProof.signatureBase64)
                .put("signature_algorithm", challengeProof.signatureAlgorithm)
                .toString()

            val challengeRequest = Request.Builder()
                .url(BASE_URL + CHALLENGE_PATH)
                .header("Accept", "application/json")
                .post(challengeBody.toRequestBody("application/json; charset=utf-8".toMediaType()))
                .build()

            val challengeResponse = client.newCall(challengeRequest).execute().use { response ->
                val raw = response.body?.string().orEmpty()
                val json = runCatching { JSONObject(raw) }.getOrNull()
                if (!response.isSuccessful || json == null || !json.optBoolean("ok", false)) {
                    val reason = json?.optString("reason").orEmpty().ifBlank { "device_recovery_http_${response.code}" }
                    return@withContext Result.failure(IllegalStateException(reason))
                }
                json
            }

            if (!challengeResponse.optBoolean("integrity_required", false)) {
                return@withContext Result.success(Unit)
            }

            val challengeId = challengeResponse.optString("challenge_id").trim()
            val serverChallenge = challengeResponse.optString("server_challenge").trim()
            val requestHash = challengeResponse.optString("request_hash").trim()
            if (challengeId.isBlank() || serverChallenge.isBlank() || requestHash.isBlank()) {
                return@withContext Result.failure(IllegalStateException("device_recovery_challenge_invalid"))
            }

            val integrityToken = try {
                SkryonPlayIntegrity.requestRecoveryToken(context, requestHash)
            } catch (error: Exception) {
                val reason = error.message?.takeIf { it.startsWith("play_integrity_") }
                    ?: "play_integrity_failed"
                return@withContext Result.failure(IllegalStateException(reason, error))
            }

            val confirmProof = EmeryDeviceIdentity.buildRecoveryConfirmProof(
                accessKey = key,
                challengeId = challengeId,
                serverChallenge = serverChallenge,
                integrityToken = integrityToken,
            )
            val confirmBody = JSONObject()
                .put("code", key)
                .put("device_id", confirmProof.deviceId)
                .put("client_public_key", confirmProof.publicKeyBase64)
                .put("timestamp", confirmProof.timestampMillis)
                .put("nonce", confirmProof.nonce)
                .put("signature", confirmProof.signatureBase64)
                .put("signature_algorithm", confirmProof.signatureAlgorithm)
                .put("challenge_id", challengeId)
                .put("server_challenge", serverChallenge)
                .put("integrity_token", integrityToken)
                .toString()

            val confirmRequest = Request.Builder()
                .url(BASE_URL + CONFIRM_PATH)
                .header("Accept", "application/json")
                .post(confirmBody.toRequestBody("application/json; charset=utf-8".toMediaType()))
                .build()

            client.newCall(confirmRequest).execute().use { response ->
                val raw = response.body?.string().orEmpty()
                val json = runCatching { JSONObject(raw) }.getOrNull()
                if (!response.isSuccessful || json == null || !json.optBoolean("ok", false)) {
                    val reason = json?.optString("reason").orEmpty().ifBlank { "device_recovery_http_${response.code}" }
                    return@withContext Result.failure(IllegalStateException(reason))
                }
                if (!json.optBoolean("recovered", false)) {
                    return@withContext Result.failure(IllegalStateException("device_recovery_not_confirmed"))
                }
            }

            Result.success(Unit)
        } catch (_: IOException) {
            Result.failure(IllegalStateException("network"))
        } catch (error: Exception) {
            Result.failure(error)
        }
    }
}

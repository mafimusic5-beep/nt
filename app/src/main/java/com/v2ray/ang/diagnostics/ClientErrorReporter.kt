package com.v2ray.ang.diagnostics

import android.content.Context
import android.os.Build
import com.v2ray.ang.AppConfig
import com.v2ray.ang.BuildConfig
import com.v2ray.ang.handler.EmeryAccessManager
import com.v2ray.ang.handler.MmkvManager
import com.v2ray.ang.security.EmeryDeviceIdentity
import com.v2ray.ang.ui.premium.SKRYON_ACTIVATION_CODE_PREF
import java.util.Locale
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.TimeUnit
import okhttp3.Call
import okhttp3.Callback
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import org.json.JSONArray
import org.json.JSONObject

/**
 * Sends privacy-safe failure reports to the Skryon backend, which forwards them
 * to configured Telegram administrators. Reports never contain an access key,
 * device identifier, hardware model, IP address, VPN configuration or user text.
 */
object ClientErrorReporter {

    private const val REPORT_URL = "https://skryon.ru/api/client/error"
    private const val REPORT_PATH = "/api/client/error"
    private const val PREFS_NAME = "skryon_client_error_reports"
    private const val PENDING_CRASH_KEY = "pending_crash"
    private const val LOCAL_DEDUP_WINDOW_MS = 10 * 60 * 1000L

    private val jsonMediaType = "application/json; charset=utf-8".toMediaType()
    private val client = OkHttpClient.Builder()
        .connectTimeout(6, TimeUnit.SECONDS)
        .readTimeout(8, TimeUnit.SECONDS)
        .writeTimeout(6, TimeUnit.SECONDS)
        .retryOnConnectionFailure(true)
        .build()
    private val sentAt = ConcurrentHashMap<String, Long>()

    @Volatile
    private var installed = false
    private lateinit var appContext: Context

    fun install(context: Context) {
        appContext = context.applicationContext
        if (installed) return
        synchronized(this) {
            if (installed) return
            installed = true

            val previous = Thread.getDefaultUncaughtExceptionHandler()
            Thread.setDefaultUncaughtExceptionHandler { thread, throwable ->
                runCatching {
                    pendingPreferences().edit()
                        .putString(PENDING_CRASH_KEY, crashPayload(thread, throwable).toString())
                        .commit()
                }
                previous?.uncaughtException(thread, throwable)
            }
        }
        flushPendingCrash()
    }

    fun reportHandled(stage: String, code: String) {
        if (!installed) return
        val payload = basePayload(
            kind = "handled",
            stage = safeToken(stage, "unknown_stage", 80),
            code = safeToken(code, "unknown_error", 120),
        )
        send(payload.toString())
    }

    private fun flushPendingCrash() {
        val raw = pendingPreferences().getString(PENDING_CRASH_KEY, null)?.trim().orEmpty()
        if (raw.isBlank()) return
        send(raw) {
            pendingPreferences().edit().remove(PENDING_CRASH_KEY).apply()
        }
    }

    private fun crashPayload(thread: Thread, throwable: Throwable): JSONObject {
        val root = generateSequence(throwable) { it.cause }.last()
        val payload = basePayload(
            kind = "crash",
            stage = safeToken("uncaught_${thread.name}", "uncaught", 80),
            code = safeToken(root.javaClass.name, "uncaught_exception", 120),
        )
        val frames = root.stackTrace
            .asSequence()
            .filter { it.className.startsWith("com.v2ray.ang") }
            .take(8)
            .map { frame ->
                val file = frame.fileName?.take(48) ?: "unknown"
                safeStackLine("${frame.className}.${frame.methodName}($file:${frame.lineNumber})")
            }
            .toList()
        payload.put("stack", JSONArray(frames))
        return payload
    }

    private fun basePayload(kind: String, stage: String, code: String): JSONObject {
        return JSONObject()
            .put("kind", kind)
            .put("stage", stage)
            .put("code", code)
            .put("app_version", BuildConfig.VERSION_NAME)
            .put("app_version_code", BuildConfig.SKRYON_VERSION_CODE)
            .put("android_api", Build.VERSION.SDK_INT)
            .put("stack", JSONArray())
    }

    private fun send(rawPayload: String, onSuccess: (() -> Unit)? = null) {
        val key = runCatching {
            val json = JSONObject(rawPayload)
            "${json.optString("kind")}|${json.optString("stage")}|${json.optString("code")}|${json.optInt("app_version_code")}"
        }.getOrElse { return }

        val now = System.currentTimeMillis()
        val previousSentAt = sentAt[key]
        if (previousSentAt != null && now - previousSentAt < LOCAL_DEDUP_WINDOW_MS) return

        val accessKey = accessKey()
        if (accessKey.isBlank()) return

        val request = runCatching {
            val proof = EmeryDeviceIdentity.buildRequestProof(
                method = "POST",
                path = REPORT_PATH,
                authSecret = accessKey,
            )
            Request.Builder()
                .url(REPORT_URL)
                .header("Authorization", "Bearer $accessKey")
                .header("X-Emery-Device-Id", proof.deviceId)
                .header("X-Emery-Timestamp", proof.timestampMillis)
                .header("X-Emery-Nonce", proof.nonce)
                .header("X-Emery-Signature", proof.signatureBase64)
                .header("X-Emery-Signature-Algorithm", proof.signatureAlgorithm)
                .header(AppConfig.SKRYON_APP_VERSION_HEADER, BuildConfig.SKRYON_VERSION_CODE.toString())
                .post(rawPayload.toRequestBody(jsonMediaType))
                .build()
        }.getOrElse { return }

        sentAt[key] = now
        client.newCall(request).enqueue(object : Callback {
            override fun onFailure(call: Call, e: java.io.IOException) {
                sentAt.remove(key, now)
            }

            override fun onResponse(call: Call, response: Response) {
                response.use {
                    if (it.isSuccessful) {
                        onSuccess?.invoke()
                    } else {
                        sentAt.remove(key, now)
                    }
                }
            }
        })
    }

    private fun accessKey(): String {
        return EmeryAccessManager.loadProfile()?.accessKey?.trim().orEmpty()
            .ifBlank {
                MmkvManager.decodeSettingsString(AppConfig.PREF_EMERY_ACCESS_KEY)
                    ?.trim()
                    .orEmpty()
            }
            .ifBlank {
                MmkvManager.decodeSettingsString(SKRYON_ACTIVATION_CODE_PREF)
                    ?.trim()
                    .orEmpty()
            }
    }

    private fun pendingPreferences() =
        appContext.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

    private fun safeToken(value: String, fallback: String, limit: Int): String {
        val normalized = value
            .trim()
            .lowercase(Locale.US)
            .map { char ->
                when {
                    char.isLetterOrDigit() -> char
                    char in ".:_-/" -> char
                    else -> '_'
                }
            }
            .joinToString("")
            .replace(Regex("_+"), "_")
            .trim('_')
            .take(limit)
        return normalized.ifBlank { fallback }
    }

    private fun safeStackLine(value: String): String {
        return value
            .filter { char -> char.isLetterOrDigit() || char in ".:_-/()$" }
            .take(180)
    }
}

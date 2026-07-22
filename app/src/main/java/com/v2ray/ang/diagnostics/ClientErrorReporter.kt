package com.v2ray.ang.diagnostics

import android.app.Activity
import android.app.AlertDialog
import android.app.Application
import android.content.Context
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.widget.Toast
import com.v2ray.ang.AppConfig
import com.v2ray.ang.BuildConfig
import com.v2ray.ang.handler.EmeryAccessManager
import com.v2ray.ang.handler.MmkvManager
import com.v2ray.ang.security.EmeryDeviceIdentity
import com.v2ray.ang.ui.premium.SKRYON_ACTIVATION_CODE_PREF
import java.lang.ref.WeakReference
import java.util.Locale
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.TimeUnit
import kotlin.system.exitProcess
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
 * Collects a small privacy-safe diagnostic report after a handled failure or crash.
 * Nothing is transmitted until the user explicitly presses "Отправить" in the
 * consent dialog. The backend then forwards the report to configured Telegram
 * administrators.
 *
 * The report body never contains an access key, device identifier, hardware model,
 * IP address, VPN configuration or user-entered text.
 */
object ClientErrorReporter {

    private const val REPORT_URL = "https://skryon.ru/api/client/error"
    private const val REPORT_PATH = "/api/client/error"
    private const val PREFS_NAME = "skryon_client_error_reports"
    private const val PENDING_REPORT_KEY = "pending_report"
    private const val LEGACY_PENDING_CRASH_KEY = "pending_crash"
    private const val LOCAL_DEDUP_WINDOW_MS = 10 * 60 * 1000L

    private val jsonMediaType = "application/json; charset=utf-8".toMediaType()
    private val client = OkHttpClient.Builder()
        .connectTimeout(6, TimeUnit.SECONDS)
        .readTimeout(8, TimeUnit.SECONDS)
        .writeTimeout(6, TimeUnit.SECONDS)
        .retryOnConnectionFailure(true)
        .build()
    private val sentAt = ConcurrentHashMap<String, Long>()
    private val mainHandler = Handler(Looper.getMainLooper())

    @Volatile
    private var installed = false

    @Volatile
    private var dialogVisible = false

    private lateinit var appContext: Context
    private var resumedActivity: WeakReference<Activity>? = null

    private val activityCallbacks = object : Application.ActivityLifecycleCallbacks {
        override fun onActivityCreated(activity: Activity, savedInstanceState: Bundle?) = Unit

        override fun onActivityStarted(activity: Activity) = Unit

        override fun onActivityResumed(activity: Activity) {
            resumedActivity = WeakReference(activity)
            maybeShowConsent(activity)
        }

        override fun onActivityPaused(activity: Activity) = Unit

        override fun onActivityStopped(activity: Activity) = Unit

        override fun onActivitySaveInstanceState(activity: Activity, outState: Bundle) = Unit

        override fun onActivityDestroyed(activity: Activity) {
            if (resumedActivity?.get() === activity) {
                resumedActivity = null
            }
        }
    }

    fun install(context: Context) {
        val application = context.applicationContext as? Application ?: return
        appContext = application
        if (installed) return

        synchronized(this) {
            if (installed) return
            installed = true
            migrateLegacyPendingCrash()
            application.registerActivityLifecycleCallbacks(activityCallbacks)

            val previous = Thread.getDefaultUncaughtExceptionHandler()
            Thread.setDefaultUncaughtExceptionHandler { thread, throwable ->
                runCatching {
                    storePendingIfEmpty(crashPayload(thread, throwable).toString())
                }
                if (previous != null) {
                    previous.uncaughtException(thread, throwable)
                } else {
                    android.os.Process.killProcess(android.os.Process.myPid())
                    exitProcess(10)
                }
            }
        }
    }

    /**
     * Queues one handled error and asks the user for permission while an Activity is
     * visible. A second error does not replace the report the user is already reviewing.
     */
    fun reportHandled(stage: String, code: String) {
        if (!installed) return
        val payload = basePayload(
            kind = "handled",
            stage = safeToken(stage, "unknown_stage", 80),
            code = safeToken(code, "unknown_error", 120),
        )
        if (storePendingIfEmpty(payload.toString())) {
            resumedActivity?.get()?.let(::maybeShowConsent)
        }
    }

    private fun migrateLegacyPendingCrash() {
        val preferences = pendingPreferences()
        val current = preferences.getString(PENDING_REPORT_KEY, null)?.trim().orEmpty()
        val legacy = preferences.getString(LEGACY_PENDING_CRASH_KEY, null)?.trim().orEmpty()
        if (legacy.isBlank()) return

        val editor = preferences.edit().remove(LEGACY_PENDING_CRASH_KEY)
        if (current.isBlank()) {
            editor.putString(PENDING_REPORT_KEY, legacy)
        }
        editor.commit()
    }

    private fun storePendingIfEmpty(rawPayload: String): Boolean {
        synchronized(this) {
            if (pendingRaw().isNotBlank()) return false
            return pendingPreferences().edit()
                .putString(PENDING_REPORT_KEY, rawPayload)
                .commit()
        }
    }

    private fun pendingRaw(): String {
        if (!installed) return ""
        return pendingPreferences().getString(PENDING_REPORT_KEY, null)?.trim().orEmpty()
    }

    private fun maybeShowConsent(activity: Activity) {
        if (!installed || pendingRaw().isBlank() || accessKey().isBlank()) return
        mainHandler.post {
            if (
                dialogVisible ||
                activity.isFinishing ||
                (Build.VERSION.SDK_INT >= Build.VERSION_CODES.JELLY_BEAN_MR1 && activity.isDestroyed) ||
                pendingRaw().isBlank() ||
                accessKey().isBlank()
            ) {
                return@post
            }
            showConsentDialog(activity)
        }
    }

    private fun showConsentDialog(activity: Activity) {
        val rawPayload = pendingRaw()
        if (rawPayload.isBlank()) return

        dialogVisible = true
        val dialog = AlertDialog.Builder(activity)
            .setTitle("Сообщить о неполадке?")
            .setMessage(
                "В приложении произошла ошибка. Отправить технический отчёт " +
                    "разработчику через Telegram-бота?\n\n" +
                    "В отчёте будут только тип ошибки, версия приложения, версия Android " +
                    "и обезличенный стек сбоя. Код доступа, IP-адрес, идентификатор и " +
                    "модель устройства, VPN-конфигурация и введённые данные не отправляются."
            )
            .setPositiveButton("Отправить") { _, _ ->
                Toast.makeText(activity, "Отправляем отчёт…", Toast.LENGTH_SHORT).show()
                send(rawPayload) { success ->
                    if (success) {
                        clearPendingIfMatches(rawPayload)
                        Toast.makeText(activity.applicationContext, "Отчёт отправлен", Toast.LENGTH_SHORT).show()
                    } else {
                        Toast.makeText(
                            activity.applicationContext,
                            "Не удалось отправить отчёт. Можно повторить позже.",
                            Toast.LENGTH_LONG,
                        ).show()
                    }
                }
            }
            .setNegativeButton("Не отправлять") { _, _ ->
                clearPendingIfMatches(rawPayload)
            }
            .setCancelable(false)
            .create()

        dialog.setOnDismissListener {
            dialogVisible = false
        }
        dialog.show()
    }

    private fun clearPendingIfMatches(rawPayload: String) {
        synchronized(this) {
            if (pendingRaw() == rawPayload) {
                pendingPreferences().edit().remove(PENDING_REPORT_KEY).apply()
            }
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

    private fun send(rawPayload: String, onComplete: (Boolean) -> Unit) {
        val key = runCatching {
            val json = JSONObject(rawPayload)
            "${json.optString("kind")}|${json.optString("stage")}|${json.optString("code")}|${json.optInt("app_version_code")}"
        }.getOrElse {
            completeOnMain(onComplete, false)
            return
        }

        val now = System.currentTimeMillis()
        val previousSentAt = sentAt[key]
        if (previousSentAt != null && now - previousSentAt < LOCAL_DEDUP_WINDOW_MS) {
            completeOnMain(onComplete, true)
            return
        }

        val accessKey = accessKey()
        if (accessKey.isBlank()) {
            completeOnMain(onComplete, false)
            return
        }

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
        }.getOrElse {
            completeOnMain(onComplete, false)
            return
        }

        sentAt[key] = now
        client.newCall(request).enqueue(object : Callback {
            override fun onFailure(call: Call, e: java.io.IOException) {
                sentAt.remove(key, now)
                completeOnMain(onComplete, false)
            }

            override fun onResponse(call: Call, response: Response) {
                response.use {
                    if (it.isSuccessful) {
                        completeOnMain(onComplete, true)
                    } else {
                        sentAt.remove(key, now)
                        completeOnMain(onComplete, false)
                    }
                }
            }
        })
    }

    private fun completeOnMain(callback: (Boolean) -> Unit, success: Boolean) {
        mainHandler.post { callback(success) }
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
            .filter { char -> char.isLetterOrDigit() || char in ".:_-/()\$" }
            .take(180)
    }
}

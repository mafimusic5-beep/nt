package com.v2ray.ang.ui.premium

import android.content.Context
import com.v2ray.ang.AppConfig
import com.v2ray.ang.BuildConfig
import com.v2ray.ang.fmt.VlessFmt
import com.v2ray.ang.handler.MmkvManager
import com.v2ray.ang.security.EmeryDeviceIdentity
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject

internal const val SKRYON_ACTIVATION_CODE_PREF = "SKRYON_ACTIVATION_CODE"
internal const val SKRYON_ACTIVATION_CONFIG_PREF = "SKRYON_ACTIVATION_CONFIG"
internal const val SKRYON_SERVER_GUID_PREF = "SKRYON_SERVER_GUID"
internal const val SKRYON_SERVER_ID_PREF = "SKRYON_SERVER_ID"
internal const val SKRYON_CONFIG_REVISION_PREF = "SKRYON_CONFIG_REVISION"

private const val SKRYON_WEBSITE_API_BASE_URL = "https://skryon.ru"
private const val SYNC_DISABLED_RECHECK_DELAY_MS = 60_000L

internal data class SkryonActivationResult(
    val ok: Boolean,
    val code: String = "",
    val config: String = "",
    val serverId: Long = -1L,
    val revision: Long = -1L,
    val error: String = "",
)

internal data class SkryonConfigSyncResult(
    val ok: Boolean,
    val changed: Boolean = false,
    val config: String = "",
    val serverId: Long = -1L,
    val revision: Long = -1L,
    val reason: String = "",
    val error: String = "",
)

private val client by lazy {
    OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(12, TimeUnit.SECONDS)
        .writeTimeout(10, TimeUnit.SECONDS)
        .build()
}

private val configSyncClient by lazy {
    client.newBuilder()
        .readTimeout(35, TimeUnit.SECONDS)
        .build()
}

internal fun isSkryonSyncEnabled(): Boolean =
    MmkvManager.decodeSettingsBool(AppConfig.SUBSCRIPTION_AUTO_UPDATE, false)

@Suppress("UNUSED_PARAMETER")
internal suspend fun activateSkryonCode(
    context: Context,
    code: String,
    formattedCode: String,
): SkryonActivationResult = withContext(Dispatchers.IO) {
    try {
        val requestJson = JSONObject()
            .put("code", formattedCode)
            .put("deviceId", EmeryDeviceIdentity.deviceId())
            .put("appVersionCode", BuildConfig.SKRYON_VERSION_CODE)
            .toString()
        val request = Request.Builder()
            .url(SKRYON_WEBSITE_API_BASE_URL + "/api/activate")
            .post(requestJson.toRequestBody("application/json; charset=utf-8".toMediaType()))
            .header("Accept", "application/json")
            .header(AppConfig.SKRYON_APP_VERSION_HEADER, BuildConfig.SKRYON_VERSION_CODE.toString())
            .build()

        client.newCall(request).execute().use { response ->
            val text = response.body?.string().orEmpty()
            if (response.code == 429) {
                return@withContext SkryonActivationResult(ok = false, error = "Слишком много попыток. Попробуйте позже")
            }
            if (!response.isSuccessful || text.isBlank()) {
                return@withContext SkryonActivationResult(ok = false, error = "Сервер активации недоступен")
            }
            val json = JSONObject(text)
            if (!json.optBoolean("ok", false)) {
                return@withContext SkryonActivationResult(
                    ok = false,
                    error = json.optString("message").ifBlank { activationReasonText(json.optString("reason")) },
                )
            }
            val config = json.optString("config").trim()
            if (!config.startsWith("vless://")) {
                return@withContext SkryonActivationResult(ok = false, error = "Конфиг сервера повреждён")
            }
            SkryonActivationResult(
                ok = true,
                code = json.optString("code", formattedCode),
                config = config,
                serverId = json.optLong("serverId", -1L),
                revision = json.optLong("revision", -1L),
            )
        }
    } catch (_: Exception) {
        SkryonActivationResult(ok = false, error = "Нет соединения с сервером")
    }
}

@Suppress("UNUSED_PARAMETER")
internal suspend fun syncSkryonConfig(
    context: Context,
    code: String,
    revision: Long,
): SkryonConfigSyncResult = withContext(Dispatchers.IO) {
    if (!isSkryonSyncEnabled()) {
        // Synchronization is opt-in. Avoid network activity and keep the existing polling loop cheap.
        delay(SYNC_DISABLED_RECHECK_DELAY_MS)
        return@withContext SkryonConfigSyncResult(
            ok = false,
            revision = revision,
            reason = "sync_disabled",
        )
    }

    try {
        val requestJson = JSONObject()
            .put("code", code)
            .put("deviceId", EmeryDeviceIdentity.deviceId())
            .put("revision", revision)
            .put("appVersionCode", BuildConfig.SKRYON_VERSION_CODE)
            .toString()
        val request = Request.Builder()
            .url(SKRYON_WEBSITE_API_BASE_URL + "/api/config/sync")
            .post(requestJson.toRequestBody("application/json; charset=utf-8".toMediaType()))
            .header("Accept", "application/json")
            .header(AppConfig.SKRYON_APP_VERSION_HEADER, BuildConfig.SKRYON_VERSION_CODE.toString())
            .build()

        configSyncClient.newCall(request).execute().use { response ->
            val text = response.body?.string().orEmpty()
            if (!response.isSuccessful || text.isBlank()) {
                return@withContext SkryonConfigSyncResult(
                    ok = false,
                    error = "Синхронизация сервера недоступна",
                )
            }

            val json = JSONObject(text)
            if (!json.optBoolean("ok", false)) {
                val reason = json.optString("reason")
                return@withContext SkryonConfigSyncResult(
                    ok = false,
                    reason = reason,
                    error = json.optString("message").ifBlank { activationReasonText(reason) },
                )
            }

            val server = json.optJSONObject("server")
            val config = server?.optString("config")?.trim().orEmpty()
            if (config.isNotBlank() && !config.startsWith("vless://")) {
                return@withContext SkryonConfigSyncResult(
                    ok = false,
                    error = "Конфиг сервера повреждён",
                )
            }

            SkryonConfigSyncResult(
                ok = true,
                changed = json.optBoolean("changed", false),
                config = config,
                serverId = server?.optLong("id", -1L) ?: -1L,
                revision = json.optLong("revision", revision),
                reason = if (server == null) "no_server" else "",
            )
        }
    } catch (_: Exception) {
        SkryonConfigSyncResult(ok = false, error = "Нет соединения с сервером")
    }
}

internal fun saveActivatedSkryonConfig(config: String): String {
    val oldGuid = MmkvManager.decodeSettingsString(SKRYON_SERVER_GUID_PREF, "")?.trim().orEmpty()
    if (oldGuid.isNotBlank()) {
        MmkvManager.removeServer(oldGuid)
    }
    val profile = requireNotNull(VlessFmt.parse(config)) { "Invalid VLESS config" }
    val guid = MmkvManager.encodeServerConfig("", profile)
    MmkvManager.encodeServerRaw(guid, config)
    MmkvManager.setSelectServer(guid)
    return guid
}

internal fun clearActivatedSkryonConfig() {
    val guid = MmkvManager.decodeSettingsString(SKRYON_SERVER_GUID_PREF, "")?.trim().orEmpty()
    if (guid.isNotBlank()) {
        MmkvManager.removeServer(guid)
    }
    MmkvManager.removeServerViaSubid(AppConfig.EMERY_BACKEND_SUBSCRIPTION_ID)
    MmkvManager.encodeSettings(SKRYON_ACTIVATION_CONFIG_PREF, "")
    MmkvManager.encodeSettings(SKRYON_SERVER_GUID_PREF, "")
    MmkvManager.encodeSettings(SKRYON_SERVER_ID_PREF, -1L)
}

private fun activationReasonText(reason: String): String {
    return when (reason) {
        "not_found" -> "Код не найден"
        "expired" -> "Срок кода истёк"
        "banned" -> "Код отключён"
        "not_bound" -> "Код не привязан к этому устройству"
        "already_bound" -> "Код уже активирован на другом устройстве"
        "device_limit" -> "Лимит устройств для этого кода исчерпан"
        "no_server" -> "Сервер ещё не добавлен"
        "too_many_attempts" -> "Слишком много попыток. Попробуйте позже"
        "upgrade_required" -> "Версия приложения устарела. Обновите приложение."
        else -> "Ошибка активации"
    }
}

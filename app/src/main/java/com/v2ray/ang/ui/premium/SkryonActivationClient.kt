package com.v2ray.ang.ui.premium

import android.content.Context
import com.v2ray.ang.AppConfig
import com.v2ray.ang.BuildConfig
import com.v2ray.ang.fmt.VlessFmt
import com.v2ray.ang.handler.EmeryAccessManager
import com.v2ray.ang.handler.EmeryAccessProfile
import com.v2ray.ang.handler.MmkvManager
import com.v2ray.ang.network.EmeryAuthClient
import com.v2ray.ang.network.EmeryBackendClient
import com.v2ray.ang.security.EmeryDeviceIdentity
import java.io.IOException
import java.net.Inet4Address
import java.net.InetAddress
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.Dns
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

internal data class SkryonActivationResult(
    val ok: Boolean,
    val code: String = "",
    val config: String = "",
    val serverId: Long = -1L,
    val revision: Long = -1L,
    val accessProfile: EmeryAccessProfile? = null,
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

private object Ipv4FirstDns : Dns {
    override fun lookup(hostname: String): List<InetAddress> {
        return Dns.SYSTEM.lookup(hostname)
            .sortedBy { address -> if (address is Inet4Address) 0 else 1 }
    }
}

private val client by lazy {
    OkHttpClient.Builder()
        .dns(Ipv4FirstDns)
        .retryOnConnectionFailure(true)
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(20, TimeUnit.SECONDS)
        .writeTimeout(15, TimeUnit.SECONDS)
        .callTimeout(30, TimeUnit.SECONDS)
        .build()
}

private val configSyncClient by lazy {
    client.newBuilder()
        .readTimeout(35, TimeUnit.SECONDS)
        .build()
}

private data class ActivationHttpResponse(
    val code: Int,
    val successful: Boolean,
    val body: String,
)

private fun executeActivationRequest(request: Request): ActivationHttpResponse {
    var lastError: IOException? = null

    repeat(2) {
        try {
            client.newCall(request).execute().use { response ->
                return ActivationHttpResponse(
                    code = response.code,
                    successful = response.isSuccessful,
                    body = response.body?.string().orEmpty(),
                )
            }
        } catch (e: IOException) {
            lastError = e
            client.connectionPool.evictAll()
        }
    }

    throw lastError ?: IOException("Activation request failed")
}

internal suspend fun activateSkryonCode(
    context: Context,
    code: String,
    formattedCode: String,
): SkryonActivationResult = withContext(Dispatchers.IO) {
    try {
        val submittedCode = formattedCode.ifBlank { code.trim() }

        // Register first. The official client does not ask for a VPN configuration
        // until the backend has atomically reserved a tariff slot for this device.
        val registration = EmeryAuthClient.verifyAccessKey(submittedCode)
        if (registration.isFailure) {
            val reason = registration.exceptionOrNull()?.message.orEmpty()
            return@withContext SkryonActivationResult(
                ok = false,
                error = activationReasonText(reason),
            )
        }

        // A second independently signed request must observe the committed device
        // row, exact counters, tariff limit and complete inventory.
        val registeredProfile = registration.getOrThrow()
        val confirmation = EmeryBackendClient.confirmDeviceRegistration(
            accessKey = submittedCode,
            activationProfile = registeredProfile,
        )
        if (confirmation.isFailure) {
            val reason = confirmation.exceptionOrNull()?.message.orEmpty()
            return@withContext SkryonActivationResult(
                ok = false,
                error = activationReasonText(reason),
            )
        }
        val confirmedProfile = confirmation.getOrThrow()

        // Only a confirmed registered device may request its VLESS configuration.
        val activationPath = "/api/activate"
        val proof = EmeryDeviceIdentity.buildActivationProof(
            path = activationPath,
            accessKey = submittedCode,
        )
        val requestJson = JSONObject()
            .put("code", submittedCode)
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
            .url(SKRYON_WEBSITE_API_BASE_URL + activationPath)
            .post(requestJson.toRequestBody("application/json; charset=utf-8".toMediaType()))
            .header("Accept", "application/json")
            .header("Connection", "close")
            .header("X-Emery-Device-Id", proof.deviceId)
            .header("X-Emery-Timestamp", proof.timestampMillis)
            .header("X-Emery-Nonce", proof.nonce)
            .header("X-Emery-Signature", proof.signatureBase64)
            .header("X-Emery-Signature-Algorithm", proof.signatureAlgorithm)
            .header(AppConfig.SKRYON_APP_VERSION_HEADER, BuildConfig.SKRYON_VERSION_CODE.toString())
            .build()

        val response = executeActivationRequest(request)
        val text = response.body
        if (response.code == 409) {
            val reason = runCatching { JSONObject(text).optString("reason") }.getOrDefault("")
            return@withContext SkryonActivationResult(
                ok = false,
                error = activationReasonText(reason.ifBlank { "device_limit_reached" }),
            )
        }
        if (response.code == 429) {
            return@withContext SkryonActivationResult(ok = false, error = "Слишком много попыток. Попробуйте позже")
        }
        if (!response.successful || text.isBlank()) {
            return@withContext SkryonActivationResult(ok = false, error = "Сервер активации недоступен")
        }
        val json = JSONObject(text)
        if (!json.optBoolean("ok", false)) {
            return@withContext SkryonActivationResult(
                ok = false,
                error = json.optString("message").ifBlank { activationReasonText(json.optString("reason")) },
            )
        }
        val confirmedCode = json.optString("code", submittedCode).trim().ifBlank { submittedCode }
        if (confirmedCode != submittedCode) {
            return@withContext SkryonActivationResult(
                ok = false,
                error = activationReasonText("activation_code_mismatch"),
            )
        }
        val config = json.optString("config").trim()
        if (!config.startsWith("vless://")) {
            return@withContext SkryonActivationResult(ok = false, error = "Конфиг сервера повреждён")
        }

        EmeryAccessManager.saveProfile(confirmedProfile)
        SkryonActivationResult(
            ok = true,
            code = confirmedCode,
            config = config,
            serverId = json.optLong("serverId", -1L),
            revision = json.optLong("revision", -1L),
            accessProfile = confirmedProfile,
        )
    } catch (_: Exception) {
        SkryonActivationResult(ok = false, error = "Нет соединения с сервером")
    }
}

internal suspend fun syncSkryonConfig(
    context: Context,
    code: String,
    revision: Long,
): SkryonConfigSyncResult = withContext(Dispatchers.IO) {
    try {
        val path = "/api/config/sync"
        val proof = EmeryDeviceIdentity.buildRequestProof(
            method = "POST",
            path = path,
            authSecret = code,
        )
        val requestJson = JSONObject()
            .put("code", code)
            .put("deviceId", proof.deviceId)
            .put("device_id", proof.deviceId)
            .put("revision", revision)
            .put("appVersionCode", BuildConfig.SKRYON_VERSION_CODE)
            .toString()
        val request = Request.Builder()
            .url(SKRYON_WEBSITE_API_BASE_URL + path)
            .post(requestJson.toRequestBody("application/json; charset=utf-8".toMediaType()))
            .header("Accept", "application/json")
            .header("X-Emery-Device-Id", proof.deviceId)
            .header("X-Emery-Timestamp", proof.timestampMillis)
            .header("X-Emery-Nonce", proof.nonce)
            .header("X-Emery-Signature", proof.signatureBase64)
            .header("X-Emery-Signature-Algorithm", proof.signatureAlgorithm)
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
    EmeryAccessManager.clearSession()
}

private fun activationReasonText(reason: String): String {
    return when (reason) {
        "not_found" -> "Код не найден"
        "expired", "invalid_or_expired_key" -> "Срок кода истёк или код недействителен"
        "banned" -> "Код отключён"
        "not_bound" -> "Код не привязан к этому устройству"
        "already_bound" -> "Код уже активирован на другом устройстве"
        "device_limit", "device_limit_reached" -> "Лимит устройств для этого тарифа исчерпан"
        "device_signature_invalid" -> "Не удалось подтвердить подлинность устройства"
        "device_not_registered", "device_confirmation_missing" -> "Сервер не подтвердил регистрацию устройства"
        "device_mismatch", "device_inventory_mismatch" -> "Сервер вернул другое устройство"
        "device_inventory_missing" -> "Сервер не вернул таблицу зарегистрированных устройств"
        "device_counter_missing", "device_counter_mismatch" -> "Сервер не подтвердил количество устройств"
        "plan_limit_mismatch" -> "Лимит устройств не соответствует выбранному тарифу"
        "activation_code_mismatch" -> "Сервер вернул конфигурацию для другого кода"
        "no_server" -> "Сервер ещё не добавлен"
        "too_many_attempts" -> "Слишком много попыток. Попробуйте позже"
        "upgrade_required" -> "Версия приложения устарела. Обновите приложение."
        "network" -> "Нет соединения с сервером регистрации"
        else -> "Ошибка регистрации устройства"
    }
}

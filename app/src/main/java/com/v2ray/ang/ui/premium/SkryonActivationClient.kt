package com.v2ray.ang.ui.premium

import android.content.Context
import com.v2ray.ang.AppConfig
import com.v2ray.ang.BuildConfig
import com.v2ray.ang.fmt.VlessFmt
import com.v2ray.ang.security.EmeryDeviceGateConfig
import com.v2ray.ang.handler.EmeryAccessManager
import com.v2ray.ang.handler.EmeryAccessProfile
import com.v2ray.ang.handler.EmeryDeviceRecord
import com.v2ray.ang.handler.MmkvManager
import com.v2ray.ang.handler.validateDeviceLimit
import com.v2ray.ang.security.EmeryDeviceIdentity
import java.io.IOException
import java.net.Inet4Address
import java.net.InetAddress
import java.util.Locale
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.Dns
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject

internal const val SKRYON_ACTIVATION_CODE_PREF = "SKRYON_ACTIVATION_CODE"
internal const val SKRYON_ACTIVATION_CONFIG_PREF = "SKRYON_ACTIVATION_CONFIG"
internal const val SKRYON_SERVER_GUID_PREF = "SKRYON_SERVER_GUID"
internal const val SKRYON_SERVER_ID_PREF = "SKRYON_SERVER_ID"
internal const val SKRYON_CONFIG_REVISION_PREF = "SKRYON_CONFIG_REVISION"
internal const val SKRYON_ACTIVATION_CODE_LENGTH = 11

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

/**
 * One signed POST atomically reserves the tariff slot, records the device and returns
 * the VPN configuration. Existing production already enforces code/device counters;
 * the upgraded backend additionally verifies the Keystore signature and returns the
 * complete device inventory in the same response.
 */
internal suspend fun activateSkryonCode(
    context: Context,
    code: String,
    formattedCode: String,
): SkryonActivationResult = withContext(Dispatchers.IO) {
    try {
        val submittedCode = formattedCode.ifBlank { code.trim() }
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
        val json = runCatching { JSONObject(text) }.getOrNull()

        if (response.code == 409) {
            val reason = json?.serverReason().orEmpty().ifBlank { "device_limit_reached" }
            return@withContext SkryonActivationResult(
                ok = false,
                error = activationReasonText(reason),
            )
        }
        if (response.code == 429) {
            return@withContext SkryonActivationResult(
                ok = false,
                error = "Слишком много попыток. Попробуйте позже",
            )
        }
        if (!response.successful || json == null) {
            val reason = json?.serverReason().orEmpty()
            return@withContext SkryonActivationResult(
                ok = false,
                error = if (reason.isBlank()) {
                    "Сервер активации недоступен"
                } else {
                    activationReasonText(reason)
                },
            )
        }
        if (!json.optBoolean("ok", false)) {
            val reason = json.serverReason()
            return@withContext SkryonActivationResult(
                ok = false,
                error = json.optString("message").ifBlank { activationReasonText(reason) },
            )
        }

        val confirmedCode = json.optString("code", submittedCode).trim().ifBlank { submittedCode }
        if (normalizeActivationCode(confirmedCode) != normalizeActivationCode(submittedCode)) {
            return@withContext SkryonActivationResult(
                ok = false,
                error = activationReasonText("activation_code_mismatch"),
            )
        }

        val config = json.optString("config").trim()
        if (!config.startsWith("vless://")) {
            return@withContext SkryonActivationResult(
                ok = false,
                error = "Конфиг сервера повреждён",
            )
        }

        val devicesUsed = json.optIntOrNull("devices_used", "devicesUsed", "usedDevices")
            ?: return@withContext SkryonActivationResult(
                ok = false,
                error = activationReasonText("device_counter_missing"),
            )
        val devicesLimit = json.optIntOrNull("devices_limit", "devicesLimit", "maxDevices")
            ?: return@withContext SkryonActivationResult(
                ok = false,
                error = activationReasonText("device_counter_missing"),
            )
        val rawPlanName = json.optString("planTitle")
            .ifBlank { json.optString("plan_name") }
            .ifBlank { json.optString("plan") }
        val planName = canonicalPlanName(rawPlanName, devicesLimit)
        if (!validateDeviceLimit(planName, devicesUsed, devicesLimit)) {
            return@withContext SkryonActivationResult(
                ok = false,
                error = activationReasonText("plan_limit_mismatch"),
            )
        }

        val devices = parseActivationDevices(
            array = json.optJSONArray("devices"),
            currentDeviceId = proof.deviceId,
            currentDeviceName = proof.deviceName,
        ).ifEmpty {
            provisionalDeviceInventory(
                currentDeviceId = proof.deviceId,
                currentDeviceName = proof.deviceName,
                devicesUsed = devicesUsed,
            )
        }
        if (devices.count { it.active } != devicesUsed ||
            devices.none { it.deviceId == proof.deviceId && it.active }
        ) {
            return@withContext SkryonActivationResult(
                ok = false,
                error = activationReasonText("device_inventory_mismatch"),
            )
        }

        val confirmedProfile = EmeryAccessProfile(
            accessKey = confirmedCode,
            vpnEnabled = true,
            routerEnabled = false,
            expiresAt = json.optString("expires_at").ifBlank { json.optString("expiresAt") },
            planName = planName,
            deviceId = proof.deviceId,
            deviceName = proof.deviceName,
            devicesUsed = devicesUsed,
            devicesLimit = devicesLimit,
            devices = devices,
        )
        EmeryAccessManager.saveProfile(confirmedProfile)

        SkryonActivationResult(
            ok = true,
            code = confirmedCode,
            config = config,
            serverId = json.optLong("serverId", -1L),
            revision = json.optLong("revision", -1L),
            accessProfile = confirmedProfile,
        )
    } catch (_: IOException) {
        SkryonActivationResult(ok = false, error = "Нет соединения с сервером")
    } catch (_: Exception) {
        SkryonActivationResult(ok = false, error = "Ошибка регистрации устройства")
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
                val reason = json.serverReason()
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
    // Build and select the replacement before deleting the previous profile.
    // A malformed link or a failed local write must leave the working profile
    // available for the caller to retry.
    val preparedConfig = EmeryDeviceGateConfig.prepareVlessUri(config)
    val profile = requireNotNull(VlessFmt.parse(preparedConfig)) { "Invalid VLESS config" }
    val oldGuid = MmkvManager.decodeSettingsString(SKRYON_SERVER_GUID_PREF, "")?.trim().orEmpty()
    val guid = MmkvManager.encodeServerConfig("", profile)
    MmkvManager.encodeServerRaw(guid, preparedConfig)
    MmkvManager.setSelectServer(guid)
    if (oldGuid.isNotBlank() && oldGuid != guid) {
        MmkvManager.removeServer(oldGuid)
    }
    return guid
}

internal fun sanitizeSkryonActivationCode(value: String): String {
    return value
        .uppercase(Locale.ROOT)
        .filter { it.isLetterOrDigit() }
        .take(SKRYON_ACTIVATION_CODE_LENGTH)
}

internal fun formatSkryonActivationCode(rawCode: String): String {
    val normalized = sanitizeSkryonActivationCode(rawCode)
    val groups = listOf(1, 3, 2, 2, 2, 1)
    var index = 0
    return groups.mapNotNull { size ->
        val part = normalized.drop(index).take(size)
        index += size
        part.takeIf { it.isNotBlank() }
    }.joinToString("-")
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

private fun normalizeActivationCode(value: String): String {
    return value.filter { it.isLetterOrDigit() }.uppercase()
}

private fun canonicalPlanName(rawPlanName: String, devicesLimit: Int): String {
    val normalized = rawPlanName
        .trim()
        .lowercase()
        .replace('ё', 'е')
        .replace("_", "")
        .replace("-", "")
        .replace(" ", "")
    return when {
        normalized == "personal" || normalized == "личный" -> "Личный"
        normalized == "personalplus" || normalized == "plus" ||
            normalized == "личный+" || normalized == "личныйплюс" -> "Личный+"
        normalized == "family" || normalized.contains("семейн") -> "Семейный"
        normalized.isBlank() || normalized == "manual" -> when (devicesLimit) {
            1 -> "Личный"
            2 -> "Личный+"
            5 -> "Семейный"
            else -> rawPlanName
        }
        else -> rawPlanName
    }
}

private fun parseActivationDevices(
    array: JSONArray?,
    currentDeviceId: String,
    currentDeviceName: String,
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
                            if (id == currentDeviceId) currentDeviceName else "Устройство"
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

private fun provisionalDeviceInventory(
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

private fun JSONObject.optIntOrNull(vararg names: String): Int? {
    names.forEach { name ->
        if (has(name) && !isNull(name)) {
            return optInt(name)
        }
    }
    return null
}

private fun JSONObject.serverReason(): String {
    return optString("reason")
        .ifBlank { optString("error") }
        .ifBlank { optString("detail") }
}

private fun activationReasonText(reason: String): String {
    return when (reason) {
        "not_found" -> "Код не найден"
        "expired", "invalid_or_expired_key" -> "Срок кода истёк или код недействителен"
        "banned" -> "Код отключён"
        "not_bound" -> "Код не привязан к этому устройству"
        "already_bound" -> "Код уже активирован на другом устройстве"
        "device_limit", "device_limit_reached" -> "Лимит устройств для этого тарифа исчерпан"
        "device_signature_invalid", "device_signature_missing" -> "Не удалось подтвердить подлинность устройства"
        "device_key_rotation_requires_reset" ->
            "Ключ этого устройства уже зарегистрирован. После переустановки обратитесь в поддержку для безопасного сброса"
        "device_revoked" -> "Доступ этого устройства отозван"
        "device_not_registered", "device_confirmation_missing" -> "Сервер не подтвердил регистрацию устройства"
        "device_mismatch", "device_inventory_mismatch" -> "Сервер вернул другое устройство"
        "device_inventory_missing" -> "Сервер не вернул таблицу зарегистрированных устройств"
        "device_counter_missing", "device_counter_mismatch" -> "Сервер не подтвердил количество устройств"
        "plan_limit_mismatch" -> "Лимит устройств не соответствует выбранному тарифу"
        "activation_code_mismatch" -> "Сервер вернул конфигурацию для другого кода"
        "no_server" -> "Сервер ещё не добавлен"
        "server_capacity_unavailable" -> "Свободных мест сейчас нет. Новый сервер уже подготавливается — попробуйте немного позже"
        "credential_install_failed" -> "Сервер подготавливает персональный доступ. Попробуйте ещё раз позже"
        "pool_backend_unreachable", "pool_confirmation_failed", "pool_assignment_unconfirmed" ->
            "Не удалось подтвердить место на VPN-сервере. Попробуйте ещё раз"
        "assignment_install_in_progress", "assignment_maintenance_in_progress", "assignment_state_changed_retry" ->
            "Персональный доступ обновляется. Повторите через несколько секунд"
        "too_many_attempts" -> "Слишком много попыток. Попробуйте позже"
        "upgrade_required" -> "Версия приложения устарела. Обновите приложение."
        "network" -> "Нет соединения с сервером регистрации"
        else -> "Ошибка регистрации устройства"
    }
}

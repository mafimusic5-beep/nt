package com.v2ray.ang.ui.premium

import android.content.Context
import android.net.Uri
import android.provider.Settings
import com.v2ray.ang.BuildConfig
import com.v2ray.ang.dto.ProfileItem
import com.v2ray.ang.enums.EConfigType
import com.v2ray.ang.handler.MmkvManager
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject

internal const val SKRYON_ACTIVATION_CONFIG_PREF = "SKRYON_ACTIVATION_CONFIG"
internal const val SKRYON_SERVER_GUID_PREF = "SKRYON_SERVER_GUID"

internal data class SkryonActivationResult(
    val ok: Boolean,
    val code: String = "",
    val config: String = "",
    val error: String = "",
)

private val client by lazy {
    OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(12, TimeUnit.SECONDS)
        .writeTimeout(10, TimeUnit.SECONDS)
        .build()
}

internal suspend fun activateSkryonCode(
    context: Context,
    code: String,
    formattedCode: String,
): SkryonActivationResult = withContext(Dispatchers.IO) {
    try {
        val requestJson = JSONObject()
            .put("code", formattedCode)
            .put("deviceId", stableDeviceId(context))
            .toString()
        val request = Request.Builder()
            .url(BuildConfig.EMERY_API_BASE_URL.trimEnd('/') + "/api/activate")
            .post(requestJson.toRequestBody("application/json; charset=utf-8".toMediaType()))
            .header("Accept", "application/json")
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
                return@withContext SkryonActivationResult(ok = false, error = activationReasonText(json.optString("reason")))
            }
            val config = json.optString("config").trim()
            if (!config.startsWith("vless://")) {
                return@withContext SkryonActivationResult(ok = false, error = "Конфиг сервера повреждён")
            }
            SkryonActivationResult(
                ok = true,
                code = json.optString("code", formattedCode),
                config = config,
            )
        }
    } catch (_: Exception) {
        SkryonActivationResult(ok = false, error = "Нет соединения с сервером")
    }
}

internal fun saveActivatedSkryonConfig(config: String): String {
    val oldGuid = MmkvManager.decodeSettingsString(SKRYON_SERVER_GUID_PREF, "")?.trim().orEmpty()
    if (oldGuid.isNotBlank()) {
        MmkvManager.removeServer(oldGuid)
    }
    val guid = MmkvManager.encodeServerConfig("", parseVlessProfile(config))
    MmkvManager.setSelectServer(guid)
    return guid
}

private fun stableDeviceId(context: Context): String {
    val androidId = Settings.Secure.getString(context.contentResolver, Settings.Secure.ANDROID_ID)
    return androidId?.takeIf { it.isNotBlank() } ?: "android-device"
}

private fun activationReasonText(reason: String): String {
    return when (reason) {
        "not_found" -> "Код не найден"
        "expired" -> "Срок кода истёк"
        "banned" -> "Код отключён"
        "already_bound" -> "Код уже активирован на другом устройстве"
        "no_server" -> "Сервер ещё не добавлен"
        "too_many_attempts" -> "Слишком много попыток. Попробуйте позже"
        else -> "Ошибка активации"
    }
}

private fun parseVlessProfile(config: String): ProfileItem {
    val uri = Uri.parse(config.trim())
    require(uri.scheme == "vless")
    val encodedAuthority = uri.encodedAuthority.orEmpty()
    val uuid = Uri.decode(encodedAuthority.substringBefore('@', "")).trim()
    val host = uri.host.orEmpty()
    val port = uri.port.takeIf { it > 0 }?.toString().orEmpty()
    require(uuid.isNotBlank() && host.isNotBlank() && port.isNotBlank())

    return ProfileItem.create(EConfigType.VLESS).apply {
        remarks = uri.fragment?.let { Uri.decode(it) }?.takeIf { it.isNotBlank() } ?: "Skryon"
        server = host
        serverPort = port
        password = uuid
        security = uri.getQueryParameter("security") ?: "reality"
        sni = uri.getQueryParameter("sni")
        fingerPrint = uri.getQueryParameter("fp")
        publicKey = uri.getQueryParameter("pbk")
        shortId = uri.getQueryParameter("sid")
        flow = uri.getQueryParameter("flow")
        network = uri.getQueryParameter("type") ?: "tcp"
        headerType = "none"
    }
}

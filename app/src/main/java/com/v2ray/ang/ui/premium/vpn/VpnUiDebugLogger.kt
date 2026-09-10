package com.v2ray.ang.ui.premium.vpn

import android.content.Context
import android.util.Log
import com.v2ray.ang.AngApplication
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import org.json.JSONObject

object VpnUiDebugLogger {

    private const val LOG_TAG = "SkryonVpn"
    private const val LOG_FILE_NAME = "vpn-connection.log"
    private const val MAX_MEMORY_LINES = 80
    private const val MAX_FILE_BYTES = 1_000_000L

    private val safeDataKeys = setOf(
        "state",
        "error",
        "serverId",
        "city",
        "location",
        "title",
        "selectedServerId",
        "runtimeRunning",
        "key",
        "event",
        "quality",
        "reason",
        "attempt",
        "stage",
        "length",
    )

    private val _events = MutableStateFlow<List<String>>(emptyList())
    val events: StateFlow<List<String>> = _events.asStateFlow()

    @Volatile
    private var initialized = false
    private var logFile: File? = null

    @Synchronized
    fun initialize(context: Context) {
        if (initialized) return

        val appContext = context.applicationContext
        val directory = appContext.getExternalFilesDir(null) ?: appContext.filesDir
        val file = File(directory, LOG_FILE_NAME)
        runCatching {
            file.parentFile?.mkdirs()
            if (!file.exists()) {
                file.createNewFile()
            }
            trimIfNeeded(file)
        }
        logFile = file
        initialized = true

        appendVisibleLine("APP", "Диагностика VPN запущена")
        Log.i(LOG_TAG, "VPN diagnostics file: ${file.absolutePath}")
    }

    fun logFilePath(): String {
        ensureInitialized()
        return logFile?.absolutePath ?: LOG_FILE_NAME
    }

    fun log(
        hypothesisId: String,
        location: String,
        message: String,
        runId: String = "run1",
        data: JSONObject = JSONObject(),
    ) {
        ensureInitialized()

        val safeData = sanitizeData(data)
        val category = categoryFor(message)
        val readableMessage = readableMessage(message, safeData)
        val details = safeDetails(safeData)
        val text = buildString {
            append(readableMessage)
            if (details.isNotBlank()) {
                append(" | ")
                append(details)
            }
        }

        appendVisibleLine(category, text)

        Log.i(
            LOG_TAG,
            buildString {
                append(category)
                append(" | ")
                append(message)
                append(" | location=")
                append(location)
                append(" | run=")
                append(runId)
                append(" | hypothesis=")
                append(hypothesisId)
                if (safeData.length() > 0) {
                    append(" | data=")
                    append(safeData)
                }
            },
        )
    }

    @Synchronized
    private fun appendVisibleLine(category: String, message: String) {
        val line = "${timestamp()} | $category | $message"
        _events.update { current ->
            (current + line).takeLast(MAX_MEMORY_LINES)
        }

        val file = logFile ?: return
        runCatching {
            trimIfNeeded(file)
            file.appendText(line + "\n")
        }
    }

    private fun ensureInitialized() {
        if (initialized) return
        runCatching { initialize(AngApplication.application) }
    }

    private fun sanitizeData(data: JSONObject): JSONObject {
        val safe = JSONObject()
        val keys = data.keys()
        while (keys.hasNext()) {
            val key = keys.next()
            if (key !in safeDataKeys) continue
            val value = data.opt(key)
            when (value) {
                is Number, is Boolean -> safe.put(key, value)
                is String -> safe.put(key, value.replace('\n', ' ').replace('\r', ' ').take(180))
            }
        }
        return safe
    }

    private fun safeDetails(data: JSONObject): String {
        val preferredOrder = listOf(
            "event",
            "state",
            "runtimeRunning",
            "serverId",
            "city",
            "quality",
            "reason",
            "error",
        )
        return preferredOrder
            .mapNotNull { key ->
                if (!data.has(key)) return@mapNotNull null
                val value = data.opt(key)?.toString()?.takeIf { it.isNotBlank() } ?: return@mapNotNull null
                "$key=$value"
            }
            .joinToString("; ")
    }

    private fun categoryFor(message: String): String {
        val normalized = message.lowercase(Locale.ROOT)
        return when {
            normalized.contains("fail") || normalized.contains("error") || normalized.contains("denied") || normalized.contains("did not stop") -> "ERROR"
            normalized.contains("disconnect") || normalized.contains("stop") -> "STOP"
            normalized.contains("service") || normalized.contains("runtime") || normalized.contains("broadcast") -> "SERVICE"
            normalized.contains("connect") -> "CONNECT"
            else -> "INFO"
        }
    }

    private fun readableMessage(message: String, data: JSONObject): String {
        return when (message) {
            "home route switched to vpn compose screen" -> "Открыт экран VPN"
            "state moved to connecting" -> "Запрос подключения принят"
            "connect ignored due to state" -> "Повторное подключение отклонено текущим состоянием"
            "access denied before VPN start" -> "Сервер запретил запуск VPN для текущего доступа"
            "optional access refresh unavailable; continuing with activated configuration" ->
                "Проверка профиля недоступна, продолжаем с сохранённой конфигурацией"
            "regional policy data refresh failed" -> "Не удалось подготовить региональную политику"
            "stopping stale VPN runtime before selected profile start" ->
                "Обнаружен предыдущий VPN-сеанс, отправлена команда остановки"
            "stale VPN runtime did not stop before profile start" ->
                "Предыдущий VPN-сеанс не остановился вовремя"
            "vpn service start threw" -> "Исключение при запуске VPN-сервиса"
            "vpn service start request failed" -> "VPN-сервис не принял запрос запуска"
            "VPN service start requested; waiting for runtime confirmation" ->
                "VPN-сервис запускается, ждём подтверждение"
            "premium UI synchronized with running VPN service" -> "VPN-сервис сообщил, что ядро запущено"
            "connect failed" -> "Не удалось подготовить подключение"
            "state moved to disconnected" -> "VPN переведён в состояние отключено"
            "service broadcast" -> {
                val event = data.optString("event").ifBlank { "неизвестное событие" }
                "Событие VPN-сервиса: $event"
            }
            "service diagnostics observer started" -> "Прослушивание событий VPN-сервиса включено"
            "premium service-state receiver registration failed" -> "Не удалось подписаться на состояние VPN-сервиса"
            else -> message
        }
    }

    private fun timestamp(): String {
        return SimpleDateFormat("HH:mm:ss.SSS", Locale.US).format(Date())
    }

    private fun trimIfNeeded(file: File) {
        if (!file.exists() || file.length() <= MAX_FILE_BYTES) return
        val tail = runCatching { file.readLines().takeLast(400) }.getOrDefault(emptyList())
        file.writeText(tail.joinToString(separator = "\n", postfix = if (tail.isEmpty()) "" else "\n"))
    }
}

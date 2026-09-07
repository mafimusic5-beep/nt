package com.v2ray.ang.handler

import com.v2ray.ang.BuildConfig
import java.util.ArrayDeque

/**
 * Debug-only, in-memory VPN connection trace.
 *
 * No credentials, IP addresses, hostnames, activation codes or device ids are
 * accepted here. Calls are compiled into release builds but become no-ops when
 * BuildConfig.DEBUG is false, so customer builds neither collect nor expose the
 * trace.
 */
object DeveloperConnectionDiagnostics {
    private const val MAX_EVENTS = 160

    private data class Event(
        val elapsedMillis: Long,
        val stage: String,
        val detail: String,
    )

    private val lock = Any()
    private val events = ArrayDeque<Event>()
    private var startedAtMillis: Long = 0L

    fun begin() {
        if (!BuildConfig.DEBUG) return
        synchronized(lock) {
            events.clear()
            startedAtMillis = System.currentTimeMillis()
            addLocked("session_started", "ok")
        }
    }

    fun event(stage: String, detail: String = "ok") {
        if (!BuildConfig.DEBUG) return
        synchronized(lock) {
            if (startedAtMillis == 0L) {
                startedAtMillis = System.currentTimeMillis()
            }
            addLocked(stage, detail)
        }
    }

    fun failure(stage: String, error: Throwable) {
        event(stage, "error=${error.javaClass.simpleName}")
    }

    fun clear() {
        if (!BuildConfig.DEBUG) return
        synchronized(lock) {
            events.clear()
            startedAtMillis = 0L
        }
    }

    fun snapshot(): String {
        if (!BuildConfig.DEBUG) return ""
        synchronized(lock) {
            return buildString {
                appendLine("developer_connection_logs")
                appendLine("app_version_code=${BuildConfig.SKRYON_VERSION_CODE}")
                appendLine("storage=memory_only")
                appendLine("events=${events.size}")
                if (events.isEmpty()) {
                    append("no_connection_events")
                    return@buildString
                }
                events.forEach { item ->
                    append('+')
                    append(item.elapsedMillis)
                    append("ms ")
                    append(item.stage)
                    append(" | ")
                    appendLine(item.detail)
                }
            }.trimEnd()
        }
    }

    private fun addLocked(stage: String, detail: String) {
        val safeStage = sanitize(stage)
        val safeDetail = sanitize(detail)
        val elapsed = if (startedAtMillis == 0L) 0L else System.currentTimeMillis() - startedAtMillis
        if (events.size >= MAX_EVENTS) {
            events.removeFirst()
        }
        events.addLast(Event(elapsed, safeStage, safeDetail))
    }

    private fun sanitize(value: String): String {
        return value
            .replace(Regex("[^A-Za-z0-9_+=:., -]"), "_")
            .take(120)
    }
}

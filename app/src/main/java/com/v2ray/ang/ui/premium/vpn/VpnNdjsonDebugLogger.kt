package com.v2ray.ang.ui.premium.vpn

import org.json.JSONObject
import java.io.File

/**
 * Debug-mode runtime evidence logger.
 * Writes NDJSON lines into workspace log file: debug-a6ff47.log
 *
 * Important: do not log activation keys or other secrets.
 */
internal object VpnNdjsonDebugLogger {
    private const val SessionId = "a6ff47"
    private const val LogPath = "debug-a6ff47.log"

    fun log(
        location: String,
        message: String,
        hypothesisId: String,
        runId: String,
        data: Map<String, Any?> = emptyMap(),
    ) {
        try {
            val payload = JSONObject()
            payload.put("sessionId", SessionId)
            payload.put("id", "vpn_${System.currentTimeMillis()}_${hypothesisId}")
            payload.put("timestamp", System.currentTimeMillis())
            payload.put("location", location)
            payload.put("message", message)
            payload.put("runId", runId)
            payload.put("hypothesisId", hypothesisId)
            payload.put("data", JSONObject(data))
            File(LogPath).appendText(payload.toString() + "\n")
        } catch (_: Throwable) {
            // Swallow to avoid impacting UI.
        }
    }
}


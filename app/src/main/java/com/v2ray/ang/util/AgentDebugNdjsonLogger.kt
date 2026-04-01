package com.v2ray.ang.util

import org.json.JSONObject
import java.io.File
import java.util.UUID

/**
 * Runtime evidence logger for Cursor Debug mode (session cb906a).
 *
 * IMPORTANT: Do not log secrets (activation keys, access keys, tokens, etc).
 */
object AgentDebugNdjsonLogger {
    private const val SESSION_ID = "cb906a"
    private const val LOG_PATH = "debug-cb906a.log"

    fun log(
        hypothesisId: String,
        location: String,
        message: String,
        runId: String,
        data: JSONObject = JSONObject(),
    ) {
        try {
            val payload = JSONObject()
                .put("sessionId", SESSION_ID)
                .put("id", "log_${UUID.randomUUID()}")
                .put("timestamp", System.currentTimeMillis())
                .put("location", location)
                .put("message", message)
                .put("data", data)
                .put("runId", runId)
                .put("hypothesisId", hypothesisId)
                .toString()
            File(LOG_PATH).appendText(payload + "\n")
        } catch (_: Throwable) {
            // Swallow to avoid impacting app behavior.
        }
    }
}


package com.v2ray.ang.ui.premium.vpn

import org.json.JSONObject
import java.io.File
import java.util.UUID

object VpnUiDebugLogger {

    // Keep in sync with current debug-mode session configuration.
    private const val SESSION_ID = "e77b78"
    private const val LOG_PATH = "debug-e77b78.log"

    fun log(
        hypothesisId: String,
        location: String,
        message: String,
        runId: String = "run1",
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
        } catch (_: Exception) {
        }
    }
}

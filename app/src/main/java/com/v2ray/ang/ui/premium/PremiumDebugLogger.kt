package com.v2ray.ang.ui.premium

import android.content.Context
import org.json.JSONObject
import java.io.File
import java.util.UUID

object PremiumDebugLogger {

    private fun payload(hypothesisId: String, location: String, message: String, data: JSONObject): String {
        val json = JSONObject()
        json.put("sessionId", "f0540f")
        json.put("id", "log_${UUID.randomUUID()}")
        json.put("runId", "run1")
        json.put("hypothesisId", hypothesisId)
        json.put("location", location)
        json.put("message", message)
        json.put("data", data)
        json.put("timestamp", System.currentTimeMillis())
        return json.toString()
    }

    fun log(context: Context, hypothesisId: String, location: String, message: String, data: JSONObject = JSONObject()) {
        try {
            // #region agent log
            val line = payload(hypothesisId, location, message, data) + "\n"
            File("debug-f0540f.log").appendText(line)
            // #endregion
        } catch (_: Exception) {
            // ignore logging failures in production flow
        }
    }
}

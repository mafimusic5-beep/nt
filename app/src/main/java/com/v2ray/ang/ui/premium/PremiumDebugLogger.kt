package com.v2ray.ang.ui.premium

import android.content.Context
import android.util.Log
import org.json.JSONObject
import java.io.File
import java.util.UUID

object PremiumDebugLogger {

    private const val SESSION_ID = "baacbb"
    private const val LOG_FILE = "debug-baacbb.log"
    private const val TAG = "EmeryDebug"

    private fun payload(hypothesisId: String, location: String, message: String, data: JSONObject): String {
        val json = JSONObject()
        json.put("sessionId", SESSION_ID)
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
            val line = payload(hypothesisId, location, message, data)
            Log.i(TAG, line)
            val dir = context.filesDir
            File(dir, LOG_FILE).appendText(line + "\n")
            // #endregion
        } catch (_: Exception) {
        }
    }
}

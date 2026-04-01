package com.v2ray.ang.handler

import android.util.Log
import org.json.JSONObject
import java.io.File
import java.util.UUID

object ManualModeDebugLogger {
    private const val TAG = "ManualModeDebug"
    private const val LOG_FILE = "debug-baacbb.log"
    private const val SESSION_ID = "baacbb"

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
            Log.i(TAG, payload)
            File(LOG_FILE).appendText(payload + "\n")
        } catch (_: Exception) {
        }
    }
}

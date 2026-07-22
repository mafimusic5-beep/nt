package com.v2ray.ang.handler

import org.json.JSONObject

/** Runtime analytics/evidence logging is intentionally disabled. */
object ManualModeDebugLogger {
    @Suppress("UNUSED_PARAMETER")
    fun log(
        hypothesisId: String,
        location: String,
        message: String,
        runId: String = "run1",
        data: JSONObject = JSONObject(),
    ) = Unit
}

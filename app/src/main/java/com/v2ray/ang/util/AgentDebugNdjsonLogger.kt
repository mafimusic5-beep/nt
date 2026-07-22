package com.v2ray.ang.util

import org.json.JSONObject

/**
 * Deliberately disabled in production and debug builds.
 * Runtime analytics/evidence files are not created or retained.
 */
object AgentDebugNdjsonLogger {
    @Suppress("UNUSED_PARAMETER")
    fun log(
        hypothesisId: String,
        location: String,
        message: String,
        runId: String,
        data: JSONObject = JSONObject(),
    ) = Unit
}

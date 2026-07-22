package com.v2ray.ang.ui.premium.vpn

import org.json.JSONObject

/** Runtime analytics/evidence logging is intentionally disabled. */
object VpnUiDebugLogger {
    @Suppress("UNUSED_PARAMETER")
    fun log(
        hypothesisId: String,
        location: String,
        message: String,
        runId: String = "run1",
        data: JSONObject = JSONObject(),
    ) = Unit
}

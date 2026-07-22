package com.v2ray.ang.ui.premium

import android.content.Context
import org.json.JSONObject

/** Runtime analytics/evidence logging is intentionally disabled. */
object PremiumDebugLogger {
    @Suppress("UNUSED_PARAMETER")
    fun log(
        context: Context,
        hypothesisId: String,
        location: String,
        message: String,
        data: JSONObject = JSONObject(),
    ) = Unit
}

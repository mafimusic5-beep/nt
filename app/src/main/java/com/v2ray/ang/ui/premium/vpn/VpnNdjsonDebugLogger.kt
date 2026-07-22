package com.v2ray.ang.ui.premium.vpn

/** Runtime analytics/evidence logging is intentionally disabled. */
internal object VpnNdjsonDebugLogger {
    @Suppress("UNUSED_PARAMETER")
    fun log(
        location: String,
        message: String,
        hypothesisId: String,
        runId: String,
        data: Map<String, Any?> = emptyMap(),
    ) = Unit
}

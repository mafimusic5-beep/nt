package com.v2ray.ang.ui.premium.vpn

import android.util.Log
import com.v2ray.ang.BuildConfig
import com.v2ray.ang.diagnostics.ClientErrorReporter
import org.json.JSONObject

object VpnUiDebugLogger {

    private const val TAG = "SkryonVpn"

    fun log(
        hypothesisId: String,
        location: String,
        message: String,
        runId: String = "run1",
        data: JSONObject = JSONObject(),
    ) {
        if (BuildConfig.DEBUG) {
            Log.d(TAG, "$location: $message")
        }

        if (shouldReport(message)) {
            val code = data.optString("error")
                .ifBlank { data.optString("reason") }
                .ifBlank { message }
            ClientErrorReporter.reportHandled(
                stage = location.substringBefore(':').ifBlank { hypothesisId },
                code = code,
            )
        }
    }

    private fun shouldReport(message: String): Boolean {
        val normalized = message.trim().lowercase()
        return listOf(
            "failed",
            "failure",
            "error",
            "denied",
            "unavailable",
            "timed out",
            "timeout",
        ).any(normalized::contains)
    }
}

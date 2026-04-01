package com.v2ray.ang.handler

import com.v2ray.ang.AppConfig
import com.v2ray.ang.util.JsonUtil

data class ManualDiagnosticError(
    val code: String,
    val message: String,
    val source: String,
    val details: String,
    val timestamp: Long = System.currentTimeMillis(),
)

object ManualDiagnosticCodes {
    const val MANUAL_IMPORT_EMPTY = "MANUAL_IMPORT_EMPTY"
    const val MANUAL_IMPORT_INVALID_SCHEME = "MANUAL_IMPORT_INVALID_SCHEME"
    const val MANUAL_IMPORT_PARSE_FAILED = "MANUAL_IMPORT_PARSE_FAILED"
    const val MANUAL_IMPORT_ZERO_PROFILES = "MANUAL_IMPORT_ZERO_PROFILES"
    const val SELECTED_SERVER_MISSING_AFTER_IMPORT = "SELECTED_SERVER_MISSING_AFTER_IMPORT"
    const val SELECTED_SERVER_MISSING = "SELECTED_SERVER_MISSING"
    const val SERVER_LIST_FETCH_FAILED = "SERVER_LIST_FETCH_FAILED"
    const val SERVER_CONFIG_UNAVAILABLE = "SERVER_CONFIG_UNAVAILABLE"
    const val VPN_IMPORT_FAILED = "VPN_IMPORT_FAILED"
    const val SERVER_CONFIG_DECODE_FAILED = "SERVER_CONFIG_DECODE_FAILED"
    const val VPN_PERMISSION_DENIED = "VPN_PERMISSION_DENIED"
    const val VPN_START_FAILED = "VPN_START_FAILED"
    const val VPN_SERVICE_START_FAILED = "VPN_SERVICE_START_FAILED"
    const val CORE_START_FAILED = "CORE_START_FAILED"
    const val TUN_NATIVE_LIB_MISSING = "TUN_NATIVE_LIB_MISSING"
    const val TUN_NATIVE_SYMBOL_MISSING = "TUN_NATIVE_SYMBOL_MISSING"
    const val RECEIVER_CLEANUP_ERROR = "RECEIVER_CLEANUP_ERROR"
    const val VPN_FD_ALREADY_CLOSED = "VPN_FD_ALREADY_CLOSED"
    const val UNKNOWN_EXCEPTION = "UNKNOWN_EXCEPTION"
}

object ManualModeDiagnostics {
    fun recordSuccessStep(step: String) {
        MmkvManager.encodeSettings(AppConfig.PREF_MANUAL_LAST_SUCCESS_STEP, step)
    }

    fun reportError(
        code: String,
        message: String,
        source: String,
        details: String,
    ) {
        val error = ManualDiagnosticError(
            code = code,
            message = message,
            source = source,
            details = details,
        )
        MmkvManager.encodeSettings(AppConfig.PREF_MANUAL_LAST_ERROR_JSON, JsonUtil.toJson(error))
    }

    fun clearError() {
        MmkvManager.encodeSettings(AppConfig.PREF_MANUAL_LAST_ERROR_JSON, "")
    }

    fun getLastError(): ManualDiagnosticError? {
        val json = MmkvManager.decodeSettingsString(AppConfig.PREF_MANUAL_LAST_ERROR_JSON) ?: return null
        if (json.isBlank()) return null
        return JsonUtil.fromJson(json, ManualDiagnosticError::class.java)
    }

    fun getLastSuccessStep(): String {
        return MmkvManager.decodeSettingsString(AppConfig.PREF_MANUAL_LAST_SUCCESS_STEP, "").orEmpty()
    }
}

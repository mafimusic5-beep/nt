package com.v2ray.ang.service

/**
 * A missed heartbeat is not proof that a customer's VPN subscription was revoked.
 *
 * A short Wi-Fi/mobile-data interruption must NOT tear down VpnService. Only an
 * authoritative server rejection can invalidate a running session. An expired
 * session can be reacquired after the device regains connectivity.
 */
internal enum class VpnHeartbeatFailureAction {
    RETRY,
    REACQUIRE_SESSION,
    STOP_REVOKED_SESSION,
}

internal fun heartbeatFailureAction(reason: String?): VpnHeartbeatFailureAction {
    val normalized = reason.orEmpty().trim().lowercase()
    if (normalized == "vpn_session_expired") {
        return VpnHeartbeatFailureAction.REACQUIRE_SESSION
    }
    if (
        normalized == "network" ||
        normalized == "timeout" ||
        normalized == "no_server" ||
        normalized == "http_408" ||
        normalized == "http_429" ||
        normalized == "http_500" ||
        normalized == "http_502" ||
        normalized == "http_503" ||
        normalized == "http_504"
    ) {
        return VpnHeartbeatFailureAction.RETRY
    }
    // Invalid credentials, a banned/expired subscription, a revoked device,
    // session mismatch or an unknown authorization response must not bypass
    // backend access control by keeping a stale tunnel forever.
    return VpnHeartbeatFailureAction.STOP_REVOKED_SESSION
}

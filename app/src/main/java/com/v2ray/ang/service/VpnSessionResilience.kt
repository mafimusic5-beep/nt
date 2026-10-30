package com.v2ray.ang.service

import java.security.MessageDigest

/** A missing internet route is a temporary transport failure, not a request to disconnect the VPN. */
internal enum class VpnHeartbeatAction {
    RETRY,
    REACQUIRE,
    STOP,
}

/**
 * Only a confirmed access/session failure may terminate an active VPN.
 * A 90-second server lease may expire during an outage, so recover through the
 * authenticated acquire endpoint rather than accepting the expired lease.
 */
internal fun vpnHeartbeatAction(reason: String?): VpnHeartbeatAction {
    val code = reason.orEmpty().trim().lowercase()
    return when {
        code == "vpn_session_expired" || code == "vpn_session_mismatch" -> VpnHeartbeatAction.REACQUIRE
        code in setOf("network", "timeout", "temporarily_unavailable", "http_408", "http_425", "http_429") ->
            VpnHeartbeatAction.RETRY
        code.startsWith("http_5") && code.length == 8 && code.substring(5).all(Char::isDigit) ->
            VpnHeartbeatAction.RETRY
        // Other failures (banned, expired subscription, device revocation, 401,
        // invalid request) are NOT transient and must not be silently bypassed.
        else -> VpnHeartbeatAction.STOP
    }
}

/** Compare a recovered lease's server profile with the running tunnel without persisting credentials. */
internal fun vpnImportFingerprint(preparedImportText: String): String =
    MessageDigest.getInstance("SHA-256")
        .digest(preparedImportText.trim().toByteArray(Charsets.UTF_8))
        .joinToString("") { byte -> "%02x".format(byte.toInt() and 0xff) }

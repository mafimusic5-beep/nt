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
        code in setOf(
            "network", "timeout", "temporarily_unavailable",
            "http_408", "http_425", "http_429",
            // A signed lease reacquire may temporarily fail while the
            // physical internet returns but node-health data catches up.
            // Keep the protected tunnel and retry; never treat this as a
            // confirmed user/device revocation.
            "vpn_session_acquire_rate_limited",
            "pool_no_healthy_nodes", "no_healthy_nodes",
            "server_capacity_unavailable", "pool_backend_unreachable",
            "assignment_install_in_progress", "assignment_maintenance_in_progress",
            "assignment_state_changed_retry", "credential_install_failed",
        ) -> VpnHeartbeatAction.RETRY
        code.startsWith("http_5") && code.length == 8 && code.substring(5).all(Char::isDigit) ->
            VpnHeartbeatAction.RETRY
        // Other failures (banned, expired subscription, device revocation, 401,
        // invalid request) are NOT transient and must not be silently bypassed.
        else -> VpnHeartbeatAction.STOP
    }
}

/**
 * Avoid polling floods from overlapping Android network callbacks, but never
 * delay the first signed heartbeat after a confirmed physical-network outage.
 * No additional clock-based grace period is applied when connectivity returns.
 */
internal fun vpnHeartbeatGapDelayMs(
    lastHeartbeatAtMs: Long,
    nowElapsedMs: Long,
    minimumGapMs: Long,
    resumedAfterOffline: Boolean,
): Long {
    if (resumedAfterOffline || lastHeartbeatAtMs <= 0L) return 0L
    return (minimumGapMs - (nowElapsedMs - lastHeartbeatAtMs)).coerceIn(0L, minimumGapMs)
}

/** Compare a recovered lease's server profile with the running tunnel without persisting credentials. */
internal fun vpnImportFingerprint(preparedImportText: String): String =
    MessageDigest.getInstance("SHA-256")
        .digest(preparedImportText.trim().toByteArray(Charsets.UTF_8))
        .joinToString("") { byte -> "%02x".format(byte.toInt() and 0xff) }

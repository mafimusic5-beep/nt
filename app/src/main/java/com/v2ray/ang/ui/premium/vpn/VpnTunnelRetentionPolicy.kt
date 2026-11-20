package com.v2ray.ang.ui.premium.vpn

/** A failed traffic probe is not permission to tear down an already running Android TUN. */
internal fun shouldRetainVpnAfterProbeFailure(
    daemonRunning: Boolean,
    serviceHeartbeatAlive: Boolean,
    userWantsVpn: Boolean,
    stopRequested: Boolean,
): Boolean = daemonRunning && serviceHeartbeatAlive && userWantsVpn && !stopRequested

/**
 * Only a missing service heartbeat AND a failed direct core-state query prove
 * that a previously verified VPN is no longer alive. Never guess from a
 * failed HTTPS probe or stop a functioning tunnel to check it.
 */
internal fun shouldReportUnexpectedVpnStop(
    wasConnected: Boolean,
    serviceHeartbeatAlive: Boolean,
    coreRespondedRunning: Boolean,
    userWantsVpn: Boolean,
    stopRequested: Boolean,
): Boolean = wasConnected && !serviceHeartbeatAlive && !coreRespondedRunning &&
    userWantsVpn && !stopRequested

/** UI recreation must reuse a surviving Android VPN rather than acquiring another lease. */
internal fun shouldResumeExistingVpnTunnel(
    serviceAlive: Boolean,
    sessionPresent: Boolean,
    stopRequested: Boolean,
): Boolean = serviceAlive && sessionPresent && !stopRequested

/** Retry indefinitely but at a bounded rate until traffic is actually verified or the user stops. */
internal fun vpnProbeRetryDelayMs(failedChecks: Int): Long {
    val exponent = (failedChecks.coerceAtLeast(1) - 1).coerceAtMost(4)
    return (2_000L shl exponent).coerceAtMost(30_000L)
}
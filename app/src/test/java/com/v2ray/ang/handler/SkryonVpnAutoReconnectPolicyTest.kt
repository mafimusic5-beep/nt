package com.v2ray.ang.handler

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class SkryonVpnAutoReconnectPolicyTest {
    @Test
    fun `recover orphaned session after app process dies`() {
        assertTrue(shouldRecoverStaleVpnLease(
            hasSession = true, vpnNetworkActive = false,
            connectionStarting = false, serviceAlive = false,
        ))
    }

    @Test
    fun `never release lease owned by a live VPN tunnel or another startup`() {
        assertFalse(shouldRecoverStaleVpnLease(
            hasSession = true, vpnNetworkActive = true,
            connectionStarting = false, serviceAlive = false,
        ))
        assertFalse(shouldRecoverStaleVpnLease(
            hasSession = true, vpnNetworkActive = false,
            connectionStarting = false, serviceAlive = true,
        ))
        assertFalse(shouldRecoverStaleVpnLease(
            hasSession = true, vpnNetworkActive = false,
            connectionStarting = true, serviceAlive = false,
        ))
        assertFalse(shouldRecoverStaleVpnLease(
            hasSession = false, vpnNetworkActive = false,
            connectionStarting = false, serviceAlive = false,
        ))
    }

    @Test
    fun `transient backend and network errors can retry automatically`() {
        for (error in listOf(
            "network", "timeout", "server_capacity_unavailable",
            "pool_backend_unreachable", "assignment_install_in_progress",
            "assignment_maintenance_in_progress", "assignment_state_changed_retry",
            "credential_install_failed", "http_502", "http_503",
        )) {
            assertTrue(error, isRetryableSkryonReconnectFailure(error))
        }
    }

    @Test
    fun `invalid device credentials and revoked access are not bypassed`() {
        for (error in listOf(
            "expired", "banned", "blocked", "invalid_or_expired_key",
            "device_not_registered", "device_mismatch", "http_401", "http_403",
            null,
        )) {
            assertFalse(error, isRetryableSkryonReconnectFailure(error))
        }
    }

    @Test
    fun `watchdog never restarts during transient outage manual OFF or a pending startup`() {
        assertFalse(shouldRestartUnhealthyVpnTunnel(
            userRequestedConnection = true,
            physicalNetworkAvailable = false,
            connectionStarting = false,
            consecutiveFailedChecks = 99,
        ))
        assertFalse(shouldRestartUnhealthyVpnTunnel(
            userRequestedConnection = false,
            physicalNetworkAvailable = true,
            connectionStarting = false,
            consecutiveFailedChecks = 99,
        ))
        assertFalse(shouldRestartUnhealthyVpnTunnel(
            userRequestedConnection = true,
            physicalNetworkAvailable = true,
            connectionStarting = true,
            consecutiveFailedChecks = 99,
        ))
        assertFalse(shouldRestartUnhealthyVpnTunnel(
            userRequestedConnection = true,
            physicalNetworkAvailable = true,
            connectionStarting = false,
            consecutiveFailedChecks = 2,
        ))
        assertTrue(shouldRestartUnhealthyVpnTunnel(
            userRequestedConnection = true,
            physicalNetworkAvailable = true,
            connectionStarting = false,
            consecutiveFailedChecks = 3,
        ))
    }

    @Test
    fun `daemon heartbeat has a bounded crash recovery window`() {
        assertTrue(isRecentSkryonServiceHeartbeat(lastSeenAt = 10_000L, now = 15_000L))
        assertTrue(isRecentSkryonServiceHeartbeat(lastSeenAt = 10_000L, now = 30_000L))
        assertFalse(isRecentSkryonServiceHeartbeat(lastSeenAt = 10_000L, now = 30_001L))
        assertFalse(isRecentSkryonServiceHeartbeat(lastSeenAt = 0L, now = 1L))
        assertFalse(isRecentSkryonServiceHeartbeat(lastSeenAt = 12_000L, now = 11_000L))
    }
}

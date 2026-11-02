package com.v2ray.ang.service

import org.junit.Assert.assertEquals
import org.junit.Test

class VpnSessionFastRecoveryTest {
    @Test
    fun firstLeaseCheckIsNotDelayed() {
        assertEquals(
            0L,
            vpnHeartbeatGapDelayMs(0L, 15_000L, 2_000L, resumedAfterOffline = false),
        )
    }

    @Test
    fun firstCheckImmediatelyAfterNetworkIsRestoredSkipsDebounce() {
        assertEquals(
            0L,
            vpnHeartbeatGapDelayMs(10_000L, 10_100L, 2_000L, resumedAfterOffline = true),
        )
    }

    @Test
    fun multipleNormalNetworkCallbacksDoNotFloodSignedBackend() {
        assertEquals(
            1_900L,
            vpnHeartbeatGapDelayMs(10_000L, 10_100L, 2_000L, resumedAfterOffline = false),
        )
        assertEquals(
            0L,
            vpnHeartbeatGapDelayMs(10_000L, 13_000L, 2_000L, resumedAfterOffline = false),
        )
    }

    @Test
    fun temporarilyUnhealthyPoolKeepsCurrentVpnWhileRetryingSignedSession() {
        for (reason in listOf(
            "pool_no_healthy_nodes",
            "no_healthy_nodes",
            "server_capacity_unavailable",
            "pool_backend_unreachable",
            "vpn_session_acquire_rate_limited",
            "credential_install_failed",
        )) {
            assertEquals(reason, VpnHeartbeatAction.RETRY, vpnHeartbeatAction(reason))
        }
    }

    @Test
    fun revokedAccessNeverBecomesAnAutomaticRecovery() {
        for (reason in listOf(
            "device_signature_invalid",
            "device_timestamp_invalid",
            "device_not_registered",
            "http_403",
            "banned",
            "reacquire_profile_changed",
        )) {
            assertEquals(reason, VpnHeartbeatAction.STOP, vpnHeartbeatAction(reason))
        }
        assertEquals(VpnHeartbeatAction.REACQUIRE, vpnHeartbeatAction("vpn_session_expired"))
    }

    @Test
    fun negativeElapsedClockMovementNeverProducesAWaitLongerThanDebounce() {
        assertEquals(
            2_000L,
            vpnHeartbeatGapDelayMs(10_000L, 9_900L, 2_000L, resumedAfterOffline = false),
        )
    }
}

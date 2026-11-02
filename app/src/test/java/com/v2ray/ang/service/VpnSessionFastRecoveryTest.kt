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
    fun negativeElapsedClockMovementNeverProducesAWaitLongerThanDebounce() {
        assertEquals(
            2_000L,
            vpnHeartbeatGapDelayMs(10_000L, 9_900L, 2_000L, resumedAfterOffline = false),
        )
    }
}

package com.v2ray.ang.ui.premium.vpn

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class VpnTunnelRetentionPolicyTest {
    @Test
    fun temporaryInternetLossKeepsTheEstablishedVpnAndAllowsVerificationRetry() {
        assertTrue(shouldRetainVpnAfterProbeFailure(
            daemonRunning = true, userWantsVpn = true, stopRequested = false,
        ))
    }

    @Test
    fun manualOffOrActualServiceDeathStopsRetrying() {
        assertFalse(shouldRetainVpnAfterProbeFailure(true, false, false))
        assertFalse(shouldRetainVpnAfterProbeFailure(true, true, true))
        assertFalse(shouldRetainVpnAfterProbeFailure(false, true, false))
    }

    @Test
    fun repeatedVerificationIsBackedOffAndNeverSpins() {
        assertEquals(2_000L, vpnProbeRetryDelayMs(1))
        assertEquals(4_000L, vpnProbeRetryDelayMs(2))
        assertEquals(8_000L, vpnProbeRetryDelayMs(3))
        assertEquals(16_000L, vpnProbeRetryDelayMs(4))
        assertEquals(30_000L, vpnProbeRetryDelayMs(5))
        assertEquals(30_000L, vpnProbeRetryDelayMs(999))
    }
}
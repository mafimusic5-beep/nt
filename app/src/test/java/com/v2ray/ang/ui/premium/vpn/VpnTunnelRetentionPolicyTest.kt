package com.v2ray.ang.ui.premium.vpn

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class VpnTunnelRetentionPolicyTest {
    @Test
    fun temporaryInternetLossKeepsTheEstablishedVpnAndAllowsVerificationRetry() {
        assertTrue(shouldRetainVpnAfterProbeFailure(
            daemonRunning = true, serviceHeartbeatAlive = true,
            userWantsVpn = true, stopRequested = false,
        ))
    }

    @Test
    fun staleUiRunningFlagCannotKeepRetryingAfterTheVpnProcessDied() {
        assertFalse(shouldRetainVpnAfterProbeFailure(
            daemonRunning = true, serviceHeartbeatAlive = false,
            userWantsVpn = true, stopRequested = false,
        ))
        assertFalse(shouldRetainVpnAfterProbeFailure(
            daemonRunning = false, serviceHeartbeatAlive = true,
            userWantsVpn = true, stopRequested = false,
        ))
    }

    @Test
    fun manualOffOrActualServiceDeathStopsRetrying() {
        assertFalse(shouldRetainVpnAfterProbeFailure(true, true, false, false))
        assertFalse(shouldRetainVpnAfterProbeFailure(true, true, true, true))
        assertFalse(shouldRetainVpnAfterProbeFailure(false, false, true, false))
    }

    @Test
    fun silentCoreDeathIsDetectedButAnUnresponsiveWebsiteIsNotProofOfDeath() {
        assertTrue(shouldReportUnexpectedVpnStop(
            wasConnected = true, serviceHeartbeatAlive = false,
            coreRespondedRunning = false, userWantsVpn = true, stopRequested = false,
        ))
        assertFalse(shouldReportUnexpectedVpnStop(true, true, false, true, false))
        assertFalse(shouldReportUnexpectedVpnStop(true, false, true, true, false))
        assertFalse(shouldReportUnexpectedVpnStop(true, false, false, false, false))
        assertFalse(shouldReportUnexpectedVpnStop(true, false, false, true, true))
        assertFalse(shouldReportUnexpectedVpnStop(false, false, false, true, false))
    }

    @Test
    fun uiRecreationKeepsLiveTunInsteadOfAcquiringAnotherSession() {
        assertTrue(shouldResumeExistingVpnTunnel(true, true, false))
        assertFalse(shouldResumeExistingVpnTunnel(false, true, false))
        assertFalse(shouldResumeExistingVpnTunnel(true, false, false))
        assertFalse(shouldResumeExistingVpnTunnel(true, true, true))
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
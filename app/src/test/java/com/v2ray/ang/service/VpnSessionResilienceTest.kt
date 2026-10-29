package com.v2ray.ang.service

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Test

class VpnSessionResilienceTest {
    @Test
    fun `network interruptions never revoke an active VPN`() {
        for (reason in listOf("network", "timeout", "http_408", "http_429", "http_500", "http_502", "http_503", "http_504")) {
            assertEquals(reason, VpnHeartbeatAction.RETRY, vpnHeartbeatAction(reason))
        }
    }

    @Test
    fun `an expired or replaced server lease requires authenticated reacquisition`() {
        assertEquals(VpnHeartbeatAction.REACQUIRE, vpnHeartbeatAction("vpn_session_expired"))
        assertEquals(VpnHeartbeatAction.REACQUIRE, vpnHeartbeatAction("vpn_session_mismatch"))
    }

    @Test
    fun `revoked access and device security failures remain fatal`() {
        for (reason in listOf("banned", "expired", "invalid_or_expired_key", "device_not_registered",
            "device_mismatch", "http_401", "http_403", "bad_request", "", "reacquire_profile_changed")) {
            assertEquals(reason, VpnHeartbeatAction.STOP, vpnHeartbeatAction(reason))
        }
    }

    @Test
    fun `profile fingerprint is stable but rejects changed credentials`() {
        val stable = vpnImportFingerprint("vless://test-connection")
        assertEquals(stable, vpnImportFingerprint("vless://test-connection"))
        assertEquals(stable, vpnImportFingerprint("  vless://test-connection  "))
        assertNotEquals(stable, vpnImportFingerprint("vless://different-connection"))
        assertEquals(64, stable.length)
    }
}

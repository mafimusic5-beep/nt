package com.v2ray.ang.handler

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class SkryonVpnConnectionIntentTest {
    @Test fun `explicit manual OFF blocks same boot reconnect`() {
        // Boot preference does not enter the same-boot recovery policy.
        assertFalse(shouldAutoReconnectVpn(
                userRequestedConnection = false,
                networkAvailable = true, vpnAlreadyRunning = false,
            ))
        assertFalse(isPremiumVpnServiceStartAllowed(userRequestedConnection = false))
        assertTrue(shouldAutoReconnectVpn(
                userRequestedConnection = true,
                networkAvailable = true, vpnAlreadyRunning = false,
            ))
        assertTrue(isPremiumVpnServiceStartAllowed(userRequestedConnection = true))
    }

    @Test fun `auto boot OFF rejects previous boot intent while retaining same boot connection`() {
        assertFalse(shouldKeepSkryonConnectionIntent(
            startOnBootEnabled = false,
            armedBootCount = 42L,
            currentBootCount = 43L,
        ))
        assertTrue(shouldKeepSkryonConnectionIntent(
            startOnBootEnabled = false,
            armedBootCount = 43L,
            currentBootCount = 43L,
        ))
        assertTrue(shouldKeepSkryonConnectionIntent(
            startOnBootEnabled = true,
            armedBootCount = 42L,
            currentBootCount = 43L,
        ))
        // Older installations with no saved boot count are disarmed by the
        // BOOT_COMPLETED receiver when autostart is switched off.
        assertTrue(shouldKeepSkryonConnectionIntent(
            startOnBootEnabled = false,
            armedBootCount = -1L,
            currentBootCount = 43L,
        ))
    }

    @Test fun `involuntary disconnect always retries after a user has connected even with boot disabled`() {
        assertTrue(shouldAutoReconnectVpn(
            userRequestedConnection = true,
            networkAvailable = true, vpnAlreadyRunning = false,
        ))
    }

    @Test fun `temporary network outage waits for Internet instead of clearing user intent`() {
        assertFalse(shouldAutoReconnectVpn(
            userRequestedConnection = true,
            networkAvailable = false, vpnAlreadyRunning = false,
        ))
        assertTrue(shouldAutoReconnectVpn(
            userRequestedConnection = true,
            networkAvailable = true, vpnAlreadyRunning = false,
        ))
    }

    @Test fun `running VPN stays connected without racing another acquire`() {
        assertFalse(shouldAutoReconnectVpn(
            userRequestedConnection = true,
            networkAvailable = true, vpnAlreadyRunning = true,
        ))
    }
}

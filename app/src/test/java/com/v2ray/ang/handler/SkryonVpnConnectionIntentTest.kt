package com.v2ray.ang.handler

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class SkryonVpnConnectionIntentTest {
    @Test fun `explicit manual OFF is the only user decision that prevents automatic restart`() {
        // Boot preference does not enter the recovery policy.
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

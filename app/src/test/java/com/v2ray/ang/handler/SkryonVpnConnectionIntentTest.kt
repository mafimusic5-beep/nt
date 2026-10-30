package com.v2ray.ang.handler

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class SkryonVpnConnectionIntentTest {
    @Test
    fun `user manually stopped VPN never auto reconnects after wifi returns`() {
        assertFalse(shouldAutoReconnectVpn(
            enabled = true, userRequestedConnection = false,
            networkAvailable = true, vpnAlreadyRunning = false,
        ))
    }

    @Test
    fun `saved intent plus enabled auto connect reconnects when network returns`() {
        assertTrue(shouldAutoReconnectVpn(
            enabled = true, userRequestedConnection = true,
            networkAvailable = true, vpnAlreadyRunning = false,
        ))
    }

    @Test
    fun `offline network cannot trigger a new VPN session`() {
        assertFalse(shouldAutoReconnectVpn(
            enabled = true, userRequestedConnection = true,
            networkAvailable = false, vpnAlreadyRunning = false,
        ))
    }

    @Test
    fun `already running VPN must not be restarted by network callback`() {
        assertFalse(shouldAutoReconnectVpn(
            enabled = true, userRequestedConnection = true,
            networkAvailable = true, vpnAlreadyRunning = true,
        ))
    }

    @Test
    fun `system must not restart after a manual disconnect`() {
        assertFalse(isPremiumVpnServiceStartAllowed(
            userRequestedConnection = false,
            autoConnectEnabled = true,
            explicitUserStart = false,
        ))
    }

    @Test
    fun `OS restart requires auto-connect setting enabled`() {
        assertFalse(isPremiumVpnServiceStartAllowed(
            userRequestedConnection = true,
            autoConnectEnabled = false,
            explicitUserStart = false,
        ))
        assertTrue(isPremiumVpnServiceStartAllowed(
            userRequestedConnection = true,
            autoConnectEnabled = true,
            explicitUserStart = false,
        ))
    }

    @Test
    fun `manual connect works with auto-connect setting off`() {
        assertTrue(isPremiumVpnServiceStartAllowed(
            userRequestedConnection = true,
            autoConnectEnabled = false,
            explicitUserStart = true,
        ))
    }

    @Test
    fun `auto-connect disabled never restarts after reconnecting wifi`() {
        assertFalse(shouldAutoReconnectVpn(
            enabled = false, userRequestedConnection = true,
            networkAvailable = true, vpnAlreadyRunning = false,
        ))
    }
}

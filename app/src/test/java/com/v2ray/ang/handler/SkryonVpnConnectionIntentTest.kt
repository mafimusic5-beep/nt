package com.v2ray.ang.handler

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class SkryonVpnConnectionIntentTest {
    @Test
    fun `manual OFF always prevents reconnect`() {
        assertFalse(shouldAutoReconnectVpn(
            userRequestedConnection = false, networkAvailable = true,
            vpnAlreadyRunning = false,
        ))
        assertFalse(isPremiumVpnServiceStartAllowed(userRequestedConnection = false))
    }

    @Test
    fun `connection recovers after failure even if boot autostart is off`() {
        assertTrue(shouldAutoReconnectVpn(
            userRequestedConnection = true, networkAvailable = true,
            vpnAlreadyRunning = false,
        ))
        assertTrue(isPremiumVpnServiceStartAllowed(userRequestedConnection = true))
    }

    @Test
    fun `no network and already running VPN do not trigger another connection`() {
        assertFalse(shouldAutoReconnectVpn(
            userRequestedConnection = true, networkAvailable = false,
            vpnAlreadyRunning = false,
        ))
        assertFalse(shouldAutoReconnectVpn(
            userRequestedConnection = true, networkAvailable = true,
            vpnAlreadyRunning = true,
        ))
    }

    @Test
    fun `old boot cannot resume last session before BootReceiver`() {
        assertFalse(isArmedForBoot(true, requestedBootCount = 11, currentBootCount = 12))
        assertTrue(isArmedForBoot(true, requestedBootCount = 12, currentBootCount = 12))
        assertFalse(isArmedForBoot(false, requestedBootCount = 12, currentBootCount = 12))
    }

    @Test
    fun `boot counter unavailable retains active intent for compatibility`() {
        assertTrue(isArmedForBoot(true, requestedBootCount = null, currentBootCount = 12))
        assertTrue(isArmedForBoot(true, requestedBootCount = 12, currentBootCount = null))
    }
}

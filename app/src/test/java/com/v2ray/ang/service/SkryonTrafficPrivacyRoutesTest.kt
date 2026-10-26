package com.v2ray.ang.service

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class SkryonTrafficPrivacyRoutesTest {
    @Test
    fun fullTunnelCapturesAllIpv6EvenWhenNoIpv6ExitExists() {
        // The route must exist independently of any "prefer IPv6" user toggle.
        assertEquals(listOf("::" to 0), SkryonTrafficPrivacyRoutes.ipv6Routes(false))
    }

    @Test
    fun explicitlyBypassedLanRetainsPriorGlobalAndFakeIpv6Routes() {
        val routes = SkryonTrafficPrivacyRoutes.ipv6Routes(true)
        assertEquals(listOf("2000::" to 3, "fc00::" to 18), routes)
        assertFalse(routes.contains("::" to 0))
    }

    @Test
    fun routingPolicyIsDeterministicAndNeverEmpty() {
        for (bypassLan in listOf(true, false)) {
            val routes = SkryonTrafficPrivacyRoutes.ipv6Routes(bypassLan)
            assertTrue(routes.isNotEmpty())
            assertEquals(routes, SkryonTrafficPrivacyRoutes.ipv6Routes(bypassLan))
        }
    }
}

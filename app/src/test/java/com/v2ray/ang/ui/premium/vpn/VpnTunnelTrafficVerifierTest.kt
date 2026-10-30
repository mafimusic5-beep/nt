package com.v2ray.ang.ui.premium.vpn

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class VpnTunnelTrafficVerifierTest {
    @Test
    fun `missing health route falls back to site through the same proxy`() {
        val visited = mutableListOf<String>()
        val observed = mutableListOf<VpnTunnelProbe>()
        val result = probeSkryonEndpoints(1, probe = { url, attempt, stage ->
            visited += url
            if (url.endsWith("/health")) {
                VpnTunnelProbe(false, attempt, stage, "http_404")
            } else {
                VpnTunnelProbe(true, attempt, stage, "http_200")
            }
        }, onProbe = { observed += it })

        assertTrue(result.ok)
        assertEquals(SKRYON_PROBE_URLS, visited)
        assertEquals(2, observed.size)
        assertEquals("vpn_https_verified_via_socks", result.reason)
    }

    @Test
    fun `healthy first route skips the fallback`() {
        var requests = 0
        val result = probeSkryonEndpoints(2, probe = { _, attempt, stage ->
            requests++
            VpnTunnelProbe(true, attempt, stage, "http_204")
        })
        assertTrue(result.ok)
        assertEquals(1, requests)
    }

    @Test
    fun `two failed routes cannot report VPN connected`() {
        val result = probeSkryonEndpoints(3, probe = { _, attempt, stage ->
            VpnTunnelProbe(false, attempt, stage, "request_failed")
        })
        assertFalse(result.ok)
        assertEquals("request_failed", result.reason)
    }

    @Test
    fun `diagnostics stay on first party HTTPS destinations`() {
        assertEquals(listOf("https://skryon.ru/health", "https://skryon.ru/"), SKRYON_PROBE_URLS)
    }
}

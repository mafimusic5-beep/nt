package com.v2ray.ang.ui.premium.vpn

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class PreVpnConnectionQualityTest {
    @Test
    fun `warning is shown only for critical or offline states`() {
        assertFalse(PreVpnConnectionQuality.Good.shouldWarn)
        assertFalse(PreVpnConnectionQuality.Unknown.shouldWarn)
        assertFalse(PreVpnConnectionQuality.Unstable.shouldWarn)
        assertTrue(PreVpnConnectionQuality.Critical.shouldWarn)
        assertTrue(PreVpnConnectionQuality.Offline.shouldWarn)
    }

    @Test
    fun `offline when there is no active internet network`() {
        assertEquals(
            PreVpnConnectionQuality.Offline,
            classifyPreVpnConnection(
                snapshot(
                    hasActiveNetwork = false,
                    hasInternetCapability = false,
                    isValidated = false,
                    probes = emptyList(),
                ),
            ),
        )
    }

    @Test
    fun `critical when Android cannot validate the internet connection`() {
        assertEquals(
            PreVpnConnectionQuality.Critical,
            classifyPreVpnConnection(
                snapshot(
                    isValidated = false,
                    probes = emptyList(),
                ),
            ),
        )
    }

    @Test
    fun `unknown probe endpoint does not create a false warning`() {
        assertEquals(
            PreVpnConnectionQuality.Unknown,
            classifyPreVpnConnection(snapshot(probes = listOf(null, null, null))),
        )
    }

    @Test
    fun `good connection passes without a warning`() {
        assertEquals(
            PreVpnConnectionQuality.Good,
            classifyPreVpnConnection(snapshot(probes = listOf(90L, 115L, 130L))),
        )
    }

    @Test
    fun `one failed probe marks the connection unstable`() {
        assertEquals(
            PreVpnConnectionQuality.Unstable,
            classifyPreVpnConnection(snapshot(probes = listOf(140L, null, 180L))),
        )
    }

    @Test
    fun `high latency marks the connection critical`() {
        assertEquals(
            PreVpnConnectionQuality.Critical,
            classifyPreVpnConnection(snapshot(probes = listOf(1_320L, 1_410L, 1_500L))),
        )
    }

    @Test
    fun `very low reported bandwidth marks the connection critical`() {
        assertEquals(
            PreVpnConnectionQuality.Critical,
            classifyPreVpnConnection(
                snapshot(
                    downstreamBandwidthKbps = 96,
                    probes = listOf(120L, 140L, 150L),
                ),
            ),
        )
    }

    @Test
    fun `very low bandwidth still warns when probe hosts are unavailable`() {
        assertEquals(
            PreVpnConnectionQuality.Critical,
            classifyPreVpnConnection(
                snapshot(
                    downstreamBandwidthKbps = 96,
                    probes = listOf(null, null, null),
                ),
            ),
        )
    }

    private fun snapshot(
        hasActiveNetwork: Boolean = true,
        hasInternetCapability: Boolean = true,
        isValidated: Boolean = true,
        downstreamBandwidthKbps: Int? = 10_000,
        probes: List<Long?>,
    ) = PreVpnConnectionSnapshot(
        hasActiveNetwork = hasActiveNetwork,
        hasInternetCapability = hasInternetCapability,
        isValidated = isValidated,
        downstreamBandwidthKbps = downstreamBandwidthKbps,
        probeLatenciesMs = probes,
    )
}

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
                ),
            ),
        )
    }

    @Test
    fun `offline when Android validation and real download both fail`() {
        assertEquals(
            PreVpnConnectionQuality.Offline,
            classifyPreVpnConnection(
                snapshot(
                    isValidated = false,
                    downstreamBandwidthKbps = null,
                ),
            ),
        )
    }

    @Test
    fun `measured working connection overrides temporary Android validation failure`() {
        assertEquals(
            PreVpnConnectionQuality.Good,
            classifyPreVpnConnection(
                snapshot(
                    isValidated = false,
                    downstreamBandwidthKbps = 5_790,
                ),
            ),
        )
    }

    @Test
    fun `failed speed endpoint on validated network does not create a false warning`() {
        assertEquals(
            PreVpnConnectionQuality.Unknown,
            classifyPreVpnConnection(snapshot(downstreamBandwidthKbps = null)),
        )
    }

    @Test
    fun `reported bandwidth below half a megabit marks the connection critical`() {
        assertEquals(
            PreVpnConnectionQuality.Critical,
            classifyPreVpnConnection(
                snapshot(
                    downstreamBandwidthKbps = 499,
                ),
            ),
        )
    }

    @Test
    fun `half a megabit triggers a critical warning`() {
        assertEquals(
            PreVpnConnectionQuality.Critical,
            classifyPreVpnConnection(
                snapshot(
                    downstreamBandwidthKbps = 500,
                ),
            ),
        )
    }

    @Test
    fun `bandwidth above half a megabit does not trigger a critical warning`() {
        val quality = classifyPreVpnConnection(
            snapshot(
                downstreamBandwidthKbps = 501,
            ),
        )
        assertFalse(quality.shouldWarn)
    }

    @Test
    fun `downloaded bytes are converted to kilobits per second`() {
        assertEquals(500, calculateDownloadedBandwidthKbps(downloadedBytes = 125_000L, elapsedMs = 2_000L))
    }

    @Test
    fun `bandwidth below half a megabit warns independently of Android validation`() {
        assertEquals(
            PreVpnConnectionQuality.Critical,
            classifyPreVpnConnection(
                snapshot(
                    isValidated = false,
                    downstreamBandwidthKbps = 499,
                ),
            ),
        )
    }

    private fun snapshot(
        hasActiveNetwork: Boolean = true,
        hasInternetCapability: Boolean = true,
        isValidated: Boolean = true,
        downstreamBandwidthKbps: Int? = 10_000,
    ) = PreVpnConnectionSnapshot(
        hasActiveNetwork = hasActiveNetwork,
        hasInternetCapability = hasInternetCapability,
        isValidated = isValidated,
        downstreamBandwidthKbps = downstreamBandwidthKbps,
    )
}

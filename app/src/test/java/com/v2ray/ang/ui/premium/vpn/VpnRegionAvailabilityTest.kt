package com.v2ray.ang.ui.premium.vpn

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class VpnRegionAvailabilityTest {
    private val frankfurt = VpnLocationOption(
        id = "5",
        title = "Frankfurt",
        availability = VpnRegionAvailability.AVAILABLE,
    )
    private val losAngeles = VpnLocationOption(
        id = "4",
        title = "Los Angeles",
        availability = VpnRegionAvailability.UNAVAILABLE,
    )
    private val cachedFrankfurt = frankfurt.copy(
        availability = VpnRegionAvailability.CHECKING,
    )

    @Test
    fun `online region is selectable only with fresh authorization state`() {
        val state = VpnMainUiState(
            activationKey = "test",
            locations = listOf(frankfurt, losAngeles),
            selectedLocation = frankfurt,
            randomRegion = false,
        )
        assertTrue(state.connectButtonEnabled)
        assertFalse(state.copy(
            selectedLocation = cachedFrankfurt,
            locations = listOf(cachedFrankfurt, losAngeles),
        ).connectButtonEnabled)
    }

    @Test
    fun `offline region remains visible but cannot be connected`() {
        val state = VpnMainUiState(
            activationKey = "test",
            locations = listOf(frankfurt, losAngeles),
            selectedLocation = losAngeles,
            randomRegion = false,
        )
        assertTrue(state.locations.contains(losAngeles))
        assertFalse(state.connectButtonEnabled)
    }

    @Test
    fun `random region requires at least one live available pool`() {
        val state = VpnMainUiState(
            activationKey = "test",
            locations = listOf(losAngeles, cachedFrankfurt),
            selectedLocation = losAngeles,
            randomRegion = true,
        )
        assertFalse(state.connectButtonEnabled)
        assertTrue(state.copy(locations = listOf(losAngeles, frankfurt)).connectButtonEnabled)
    }

    @Test
    fun `manual pin cannot borrow another regions availability`() {
        val state = VpnMainUiState(
            activationKey = "test",
            locations = listOf(frankfurt, losAngeles),
            selectedLocation = losAngeles,
            randomRegion = false,
        )
        assertFalse(state.connectButtonEnabled)
    }
}

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
    fun `cached region can request a lease after directory failure`() {
        val state = VpnMainUiState(
            activationKey = "test",
            locations = listOf(frankfurt, losAngeles),
            selectedLocation = frankfurt,
            randomRegion = false,
        )
        assertTrue(state.connectButtonEnabled)
        assertTrue(state.copy(
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
    fun `random region can request cached pool when directory is unavailable`() {
        val state = VpnMainUiState(
            activationKey = "test",
            locations = listOf(losAngeles, cachedFrankfurt),
            selectedLocation = losAngeles,
            randomRegion = true,
        )
        assertTrue(state.connectButtonEnabled)
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

    @Test
    fun `random prefers live pools over unverified cached pools`() {
        val state = VpnMainUiState(activationKey = "test",
            locations = listOf(cachedFrankfurt, losAngeles, frankfurt.copy(id = "6")))
        org.junit.Assert.assertEquals(listOf("6"), state.connectionCandidates.map { it.id })
    }

    @Test
    fun `missing or deleted pinned region cannot borrow cached candidates`() {
        val state = VpnMainUiState(activationKey = "test", randomRegion = false,
            locations = listOf(losAngeles), selectedLocation = cachedFrankfurt)
        assertFalse(state.connectButtonEnabled)
        assertTrue(state.connectionCandidates.isEmpty())
    }

    @Test
    fun `empty directory invalid IDs and absent key do not enable connect`() {
        assertFalse(VpnMainUiState(activationKey = "test").connectButtonEnabled)
        val state = VpnMainUiState(activationKey = "test",
            locations = listOf(cachedFrankfurt.copy(id = "0")))
        assertFalse(state.connectButtonEnabled)
        assertFalse(state.copy(activationKey = "", locations = listOf(cachedFrankfurt)).connectButtonEnabled)
    }

    @Test
    fun `directory failure preserves disconnect and does not imply verified tunnel`() {
        val state = VpnMainUiState(activationKey = "test", locations = listOf(cachedFrankfurt))
        assertFalse(state.timerVisible)
        assertFalse(state.copy(connectionState = VpnConnectionState.Connecting).connectButtonEnabled)
        assertTrue(state.copy(connectionState = VpnConnectionState.Connected).connectButtonEnabled)
    }
}

package com.v2ray.ang.ui.premium.vpn

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class VpnRegionCatalogCacheTest {
    private val frankfurt = VpnLocationOption("5", "Регион DE", isAvailable = true)
    private val losAngeles = VpnLocationOption("4", "Лос-Анджелес, США", isAvailable = false)

    @Test
    fun savedRegionNamesAppearImmediatelyButNeverClaimStaleAvailability() {
        val snapshot = serializeRegionCatalog(listOf(frankfurt, losAngeles))
        val restored = parseSavedRegionCatalog(snapshot)
        assertEquals(listOf("5", "4"), restored.map { it.id })
        assertEquals(listOf("Регион DE", "Лос-Анджелес, США"), restored.map { it.title })
        assertTrue(restored.all { it.isAvailable == null })
        // Stale status is visibly "unknown", but a signed connect attempt is
        // still permitted: the session API is the final access authority.
        assertTrue(VpnMainUiState(activationKey = "DEV", locations = restored).connectButtonEnabled)
    }

    @Test
    fun expiredOrMalformedCatalogRowsCannotCreateConnectableRegions() {
        val restored = parseSavedRegionCatalog("invalid\tWrong\n-1\tBad\n5\tRegion DE\n5\tDuplicate")
        assertEquals(1, restored.size)
        assertEquals("5", restored.single().id)
        assertNull(restored.single().isAvailable)
    }

    @Test
    fun onlyCurrentlyAvailableServersAreEligibleForRandomAssignment() {
        assertEquals(listOf(5L), availableRegionIds(listOf(frankfurt, losAngeles)))
        val state = VpnMainUiState(
            activationKey = "DEV",
            locations = listOf(frankfurt, losAngeles),
            randomRegion = true,
        )
        assertTrue(state.connectButtonEnabled)
        assertFalse(state.copy(locations = listOf(losAngeles)).connectButtonEnabled)
        assertTrue(state.copy(locations = listOf(frankfurt.copy(isAvailable = null))).connectButtonEnabled)
    }

    @Test
    fun pinnedOfflineServerIsVisibleButNotConnectable() {
        val state = VpnMainUiState(
            activationKey = "DEV",
            locations = listOf(frankfurt, losAngeles),
            selectedLocation = losAngeles,
            randomRegion = false,
        )
        assertFalse(state.connectButtonEnabled)
        assertTrue(state.copy(selectedLocation = frankfurt).connectButtonEnabled)
        assertTrue(state.copy(connectionState = VpnConnectionState.Connected).connectButtonEnabled)
    }
}

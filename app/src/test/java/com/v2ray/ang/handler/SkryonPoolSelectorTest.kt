package com.v2ray.ang.handler

import com.v2ray.ang.network.EmeryBackendClient
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class SkryonPoolSelectorTest {
    private fun server(
        id: Long,
        active: Int? = null,
        capacity: Int? = null,
        available: Boolean = true,
        health: String = "healthy",
    ) = EmeryBackendClient.BackendServer(id, "Pool #$id", health, available, active, capacity)

    @Test
    fun picksLowestOccupancyAndNeverPinsPreviousPool() {
        val pools = listOf(server(7, 8, 10), server(11, 1, 10), server(9, 4, 10))
        assertEquals(11L, chooseFreestVpnPool(pools)?.id)
    }

    @Test
    fun comparesByPercentageNotAbsoluteCount() {
        val pools = listOf(server(1, 10, 100), server(2, 4, 5))
        assertEquals(1L, chooseFreestVpnPool(pools)?.id)
    }

    @Test
    fun ignoresOfflineAndUnavailablePools() {
        val pools = listOf(server(1, 0, 10, health = "offline"),
            server(2, 0, 10, available = false), server(3, 4, 10))
        assertEquals(3L, chooseFreestVpnPool(pools)?.id)
    }

    @Test
    fun unknownCapacityKeepsBackendPriorityRatherThanInventingLoad() {
        assertEquals(15L, chooseFreestVpnPool(listOf(server(15), server(13)))?.id)
    }

    @Test
    fun refusesToForceConnectionWhenNoPoolAvailable() {
        assertNull(chooseFreestVpnPool(listOf(server(1, available = false), server(2, health = "offline"))))
    }
}

package com.v2ray.ang.handler

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class SkryonRegionPreferenceTest {
    @Test
    fun `random mode chooses a newly drawn available pool and does not use last assignment`() {
        assertEquals(17L, chooseRegionPoolId(null, listOf(11L, 17L, 19L)) { 1 })
        assertEquals(19L, chooseRegionPoolId(null, listOf(11L, 17L, 19L)) { 2 })
    }

    @Test
    fun `user pinned region always wins even if other regions are available`() {
        assertEquals(42L, chooseRegionPoolId(42L, listOf(11L, 17L, 19L)) { 0 })
    }

    @Test
    fun `manual unavailable region does not silently switch to random server`() {
        assertEquals(42L, chooseRegionPoolId(42L, listOf(11L, 17L, 19L)) { 0 })
    }

    @Test
    fun `random chooses only positive distinct server IDs`() {
        assertEquals(11L, chooseRegionPoolId(null, listOf(-1L, 11L, 11L, 17L)) { 0 })
        assertEquals(17L, chooseRegionPoolId(null, listOf(-1L, 11L, 11L, 17L)) { 1 })
    }

    @Test
    fun `empty pool list does not fabricate a region`() {
        assertNull(chooseRegionPoolId(null, emptyList()) { 0 })
        assertNull(chooseRegionPoolId(null, listOf(-1L, 0L)) { 0 })
        assertNull(chooseRegionPoolId(-1L, listOf(5L)) { 0 })
    }
}

package com.v2ray.ang.handler

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class EmeryDeviceLimitTest {

    @Test
    fun mapsSupportedTariffsToExactLimits() {
        assertEquals(1, expectedDeviceLimitForPlan("Личный"))
        assertEquals(2, expectedDeviceLimitForPlan("Личный+"))
        assertEquals(2, expectedDeviceLimitForPlan("Личный +"))
        assertEquals(5, expectedDeviceLimitForPlan("Семейный"))
        assertEquals(1, expectedDeviceLimitForPlan("Personal"))
        assertEquals(2, expectedDeviceLimitForPlan("Personal Plus"))
        assertEquals(5, expectedDeviceLimitForPlan("Family"))
    }

    @Test
    fun rejectsUnknownTariffInsteadOfGuessing() {
        assertNull(expectedDeviceLimitForPlan("Пробный"))
        assertNull(expectedDeviceLimitForPlan("Unlimited"))
        assertFalse(validateDeviceLimit("Пробный", devicesUsed = 1, devicesLimit = 1))
    }

    @Test
    fun validatesOnlyExactTariffLimitAndOccupiedRange() {
        assertTrue(validateDeviceLimit("Личный", devicesUsed = 1, devicesLimit = 1))
        assertTrue(validateDeviceLimit("Личный+", devicesUsed = 2, devicesLimit = 2))
        assertTrue(validateDeviceLimit("Семейный", devicesUsed = 5, devicesLimit = 5))

        assertFalse(validateDeviceLimit("Личный", devicesUsed = 1, devicesLimit = 2))
        assertFalse(validateDeviceLimit("Личный+", devicesUsed = 1, devicesLimit = 5))
        assertFalse(validateDeviceLimit("Семейный", devicesUsed = 1, devicesLimit = 2))
        assertFalse(validateDeviceLimit("Личный", devicesUsed = 0, devicesLimit = 1))
        assertFalse(validateDeviceLimit("Личный+", devicesUsed = 3, devicesLimit = 2))
    }
}

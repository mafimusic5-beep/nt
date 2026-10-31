package com.v2ray.ang

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Test

class SkryonWorkManagerConfigurationTest {
    @Test
    fun `main process schedules WorkManager jobs after reboot and on app launch`() {
        val appId = "com.skryon.shield"
        assertEquals(appId, skryonWorkManagerPrimaryProcess(appId))
        assertNotEquals(appId + ":bg", skryonWorkManagerPrimaryProcess(appId))
        assertNotEquals(appId + ":RunSoLibV2RayDaemon", skryonWorkManagerPrimaryProcess(appId))
    }
}

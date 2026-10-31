package com.v2ray.ang.receiver

import android.content.Intent
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class BootRecoveryPolicyTest {
    @Test
    fun `recovery is scheduled at boot unlock and after installation update`() {
        assertTrue(shouldScheduleSkryonRecoveryOnBootEvent(Intent.ACTION_BOOT_COMPLETED))
        assertTrue(shouldScheduleSkryonRecoveryOnBootEvent(Intent.ACTION_USER_UNLOCKED))
        assertTrue(shouldScheduleSkryonRecoveryOnBootEvent(Intent.ACTION_MY_PACKAGE_REPLACED))
    }

    @Test
    fun `unrelated intents do not start background VPN`() {
        assertFalse(shouldScheduleSkryonRecoveryOnBootEvent(null))
        assertFalse(shouldScheduleSkryonRecoveryOnBootEvent(Intent.ACTION_SCREEN_OFF))
        assertFalse(shouldScheduleSkryonRecoveryOnBootEvent(Intent.ACTION_POWER_CONNECTED))
    }
}

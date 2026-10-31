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
    fun `disabled autostart blocks reboot even when VPN was previously connected`() {
        assertFalse(shouldResumePremiumVpnAtBoot(
            startOnBootEnabled = false,
            userRequestedConnection = true,
        ))
        assertFalse(shouldResumePremiumVpnAtBoot(
            startOnBootEnabled = true,
            userRequestedConnection = false,
        ))
        assertTrue(shouldResumePremiumVpnAtBoot(
            startOnBootEnabled = true,
            userRequestedConnection = true,
        ))
    }

    @Test
    fun `only boot and app update can directly start the foreground recovery service`() {
        assertTrue(shouldStartForegroundRecoveryFromBoot(Intent.ACTION_BOOT_COMPLETED))
        assertTrue(shouldStartForegroundRecoveryFromBoot(Intent.ACTION_MY_PACKAGE_REPLACED))
        // USER_UNLOCKED is not an Android 12+ FGS launch exemption.
        assertFalse(shouldStartForegroundRecoveryFromBoot(Intent.ACTION_USER_UNLOCKED))
        assertFalse(shouldStartForegroundRecoveryFromBoot(Intent.ACTION_SCREEN_OFF))
        assertFalse(shouldStartForegroundRecoveryFromBoot(null))
    }

    @Test
    fun `unrelated intents do not start background VPN`() {
        assertFalse(shouldScheduleSkryonRecoveryOnBootEvent(null))
        assertFalse(shouldScheduleSkryonRecoveryOnBootEvent(Intent.ACTION_SCREEN_OFF))
        assertFalse(shouldScheduleSkryonRecoveryOnBootEvent(Intent.ACTION_POWER_CONNECTED))
    }
}

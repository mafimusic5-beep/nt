package com.v2ray.ang.service

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class SkryonHeadlessBootRecoveryPolicyTest {
    @Test fun `boot broadcast launches no activity and selects in-service recovery`() {
        assertTrue(shouldEnterHeadlessBootRecovery(
            bootBroadcastRequested = true,
            systemAlwaysOnRestart = false,
        ))
    }

    @Test fun `system Always on may start VPN directly without explicit app extra`() {
        assertTrue(shouldEnterHeadlessBootRecovery(
            bootBroadcastRequested = false,
            systemAlwaysOnRestart = true,
        ))
    }

    @Test fun `normal manual connection continues original flow`() {
        assertFalse(shouldEnterHeadlessBootRecovery(
            bootBroadcastRequested = false,
            systemAlwaysOnRestart = false,
        ))
    }
}

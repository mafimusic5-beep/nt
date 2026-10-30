package com.v2ray.ang.handler

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class SkryonVpnAcquireRetryTest {
    @Test
    fun `in progress assignment can complete without a second tap`() {
        for (reason in listOf(
            "assignment_install_in_progress",
            "assignment_maintenance_in_progress",
            "assignment_state_changed_retry",
        )) {
            assertTrue(shouldRetrySkryonAcquire(reason, 0))
            assertTrue(shouldRetrySkryonAcquire(reason, 1))
            assertFalse(shouldRetrySkryonAcquire(reason, 2))
        }
    }

    @Test
    fun `ambiguous failures must not duplicate a possibly acquired lease`() {
        for (reason in listOf(
            "network", "timeout", "http_500", "http_503",
            "pool_backend_unreachable", "credential_install_failed",
            "invalid_or_expired_key", "device_mismatch",
            null,
        )) {
            assertFalse(shouldRetrySkryonAcquire(reason, 0))
        }
    }
}

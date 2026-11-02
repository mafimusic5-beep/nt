package com.v2ray.ang.handler

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class SkryonVpnBootRetryPolicyTest {
    @Test
    fun transientNodeHealthFailuresRetryAfterBoot() {
        for (reason in listOf(
            "pool_no_healthy_nodes",
            "no_healthy_nodes",
            "no_server",
            "assigned_node_missing",
            "server_capacity_unavailable",
            "pool_backend_unreachable",
            "vpn_session_acquire_rate_limited",
        )) {
            assertTrue(reason, isRetryableSkryonReconnectFailure(reason))
        }
    }

    @Test
    fun revokedDeviceAndExpiredSubscriptionNeverRetry() {
        for (reason in listOf(
            "device_not_registered",
            "device_timestamp_invalid",
            "device_signature_invalid",
            "expired",
            "revoked",
            "vpn_disabled",
            "device_mismatch",
        )) {
            assertFalse(reason, isRetryableSkryonReconnectFailure(reason))
        }
    }
}

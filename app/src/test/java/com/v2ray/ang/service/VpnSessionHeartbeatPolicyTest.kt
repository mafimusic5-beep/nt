package com.v2ray.ang.service

import org.junit.Assert.assertEquals
import org.junit.Test

class VpnSessionHeartbeatPolicyTest {
    @Test
    fun `temporary loss of internet never stops VPN`() {
        listOf(
            "network",
            " NETWORK ",
            "timeout",
            "http_408",
            "http_429",
            "http_500",
            "http_502",
            "http_503",
            "http_504",
            "no_server",
        ).forEach { reason ->
            assertEquals(reason, VpnHeartbeatFailureAction.RETRY, heartbeatFailureAction(reason))
        }
    }

    @Test
    fun `lease expiry requests reauthentication rather than immediate disconnect`() {
        assertEquals(
            VpnHeartbeatFailureAction.REACQUIRE_SESSION,
            heartbeatFailureAction("vpn_session_expired"),
        )
    }

    @Test
    fun `revoked subscription device or credentials still stops VPN`() {
        listOf(
            "banned",
            "expired",
            "device_not_registered",
            "vpn_session_mismatch",
            "device_proof_mismatch",
            "invalid_or_expired_key",
            "http_401",
            "http_403",
            "",
        ).forEach { reason ->
            assertEquals(reason, VpnHeartbeatFailureAction.STOP_REVOKED_SESSION, heartbeatFailureAction(reason))
        }
    }
}

package com.v2ray.ang.security

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Test

class DeviceGateProtocolTest {
    private val fields = listOf(
        "assignment_id=17", "node_id=4", "gate_server_name=gate.example.com",
        "gate_spki_sha256=${"a".repeat(64)}", "device_id=registered-device",
        "server_issued_at=1787500000000", "timestamp=1787500000001",
        "server_nonce=server-0123456789abcdef", "client_nonce=client-0123456789abcdef",
    )

    @Test
    fun keepsInternationalV1WireFormat() {
        assertEquals("protocol=emery-device-gate-v1\n" + fields.joinToString("\n"), gatewayCanonical(fields))
    }

    @Test
    fun signsBothPolicyAndOperation() {
        val connect = gatewayCanonical(fields, "russia", "connect")
        assertEquals(
            "protocol=emery-device-gate-v2\n" + fields.joinToString("\n") +
                "\nregional_policy=russia\noperation=connect",
            connect,
        )
        assertNotEquals(connect, gatewayCanonical(fields))
        assertNotEquals(connect, gatewayCanonical(fields, "russia", "check"))
    }

    @Test(expected = IllegalArgumentException::class)
    fun rejectsUnknownPolicy() {
        gatewayCanonical(fields, "unknown")
    }

    @Test(expected = IllegalArgumentException::class)
    fun cannotRequestUnsignedLegacyPreflight() {
        gatewayCanonical(fields, "international", "check")
    }
}

package com.v2ray.ang.ui.premium

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class TvQrRenewalPolicyTest {
    @Test fun qrRemainsValidForFiveMinutesAndRenewsAtExpiry() {
        val issuedAt = 1_700_000_000_000L
        val expiresAt = issuedAt + 300_000L
        assertFalse(tvQrTicketExpired(expiresAt, issuedAt))
        assertFalse(tvQrTicketExpired(expiresAt, expiresAt - 1))
        assertTrue(tvQrTicketExpired(expiresAt, expiresAt))
        assertTrue(tvQrTicketExpired(expiresAt, expiresAt + 1))
    }

    @Test fun countdownNeverShowsNegativeTime() {
        val expiresAt = 301_000L
        assertEquals(300, tvQrSecondsRemaining(expiresAt, 1_000L))
        assertEquals(1, tvQrSecondsRemaining(expiresAt, 300_000L))
        assertEquals(0, tvQrSecondsRemaining(expiresAt, 301_000L))
        assertEquals(0, tvQrSecondsRemaining(expiresAt, 500_000L))
    }
}

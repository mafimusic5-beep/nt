package com.v2ray.ang.ui.premium.vpn

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class SkryonVpnFailurePolicyTest {
    @Test
    fun clockSkewIsBlockingBeforeAnyVpnSessionAcquire() {
        assertTrue(isBlockingSkryonAccessFailure("device_timestamp_invalid"))
        assertTrue(skryonAccessFailureMessage("device_timestamp_invalid").contains("время устройства"))
        assertTrue(skryonConnectFailureMessage("device_timestamp_invalid").contains("время устройства"))
    }

    @Test
    fun signedDeviceFailuresMustNeverFallBackToUncheckedAccess() {
        for (reason in listOf(
            "device_signature_invalid",
            "device_signature_algorithm_invalid",
            "device_replay_detected",
            "device_nonce_invalid",
            "device_not_registered",
            "device_mismatch",
            "device_proof_mismatch",
            "device_key_revoked",
            "vpn_disabled",
            "expired",
        )) {
            assertTrue("Must block untrusted device reason: $reason", isBlockingSkryonAccessFailure(reason))
        }
    }

    @Test
    fun transientNetworkFailureCanContinueLegacyPrecheckButSignedAcquireStillRequired() {
        for (reason in listOf("network", "timeout", "http_503", "server_list_unavailable")) {
            assertFalse(isBlockingSkryonAccessFailure(reason))
        }
    }

    @Test
    fun sessionAcquireErrorsReportActionableSafeMessages() {
        val throttled = skryonConnectFailureMessage("vpn_session_acquire_rate_limited")
        assertTrue(throttled.contains("Слишком много"))
        assertTrue(skryonConnectFailureMessage("concurrent_limit_reached").contains("лимит"))
        assertTrue(skryonConnectFailureMessage("server_selection_refresh_required").contains("регионе"))
        assertTrue(skryonConnectFailureMessage("credential_install_failed").contains("туннель"))
    }

    @Test
    fun arbitraryServerErrorTextIsNeverReflectedToUser() {
        val secret = "unexpected_error:Bearer ACTIVATE-SECRET"
        val visible = skryonConnectFailureMessage(secret)
        assertFalse(visible.contains("ACTIVATE-SECRET"))
        assertFalse(visible.contains("Bearer"))
        assertTrue(visible.contains("диагностику"))
    }
}

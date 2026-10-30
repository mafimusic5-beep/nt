package com.v2ray.ang.ui.premium

import java.security.KeyStoreException
import org.junit.Assert.assertEquals
import org.junit.Test

class SkryonActivationFailureDiagnosticsTest {
    @Test
    fun `safe backend reason code is preserved for support`() {
        assertEquals("device_signature_algorithm_invalid",
            activationSupportCode("device_signature_algorithm_invalid"))
        assertEquals("activation_http_403", activationSupportCode(" Activation_HTTP_403 "))
    }

    @Test
    fun `untrusted diagnostics cannot leak response bodies or activation codes`() {
        assertEquals("unknown", activationSupportCode("code=SECRET-123\nserver error"))
        assertEquals("unknown", activationSupportCode("a".repeat(80)))
        assertEquals("unknown", activationSupportCode(""))
    }

    @Test
    fun `keystore and generic client failures are distinguished without messages`() {
        assertEquals("device_keystore_error",
            classifyActivationClientFailure(KeyStoreException("key material is protected")))
        assertEquals("device_keystore_error",
            classifyActivationClientFailure(IllegalStateException("outer",
                KeyStoreException("key material is protected"))))
        assertEquals("client_activation_exception",
            classifyActivationClientFailure(IllegalArgumentException("unknown value")))
    }
}

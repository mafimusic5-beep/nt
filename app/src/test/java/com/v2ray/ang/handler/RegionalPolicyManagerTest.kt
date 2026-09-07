package com.v2ray.ang.handler

import org.junit.Assert.assertEquals
import org.junit.Test

class RegionalPolicyManagerTest {

    @Test
    fun policyModesMatchBackendContract() {
        assertEquals("international", RegionalPolicyMode.International.storageValue)
        assertEquals("russia", RegionalPolicyMode.Russia.storageValue)
    }
}

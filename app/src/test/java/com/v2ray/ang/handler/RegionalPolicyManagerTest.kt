package com.v2ray.ang.handler

import com.v2ray.ang.AppConfig
import com.v2ray.ang.dto.RulesetItem
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class RegionalPolicyManagerTest {
    @Test
    fun forwardsAllTrafficToServerWithoutLocalDatasets() {
        val rules = serverRegionalPolicyRules()
        assertTrue(areServerPolicyRulesReady(rules))
        assertTrue(rules.all { it.outboundTag == AppConfig.TAG_PROXY && it.enabled })
        assertTrue(rules.all { it.domain == null && it.ip == null && it.network == "tcp,udp" })
    }

    @Test
    fun rejectsDisabledRulesAndDirectExceptions() {
        val rules = serverRegionalPolicyRules()
        assertFalse(areServerPolicyRulesReady(emptyList()))
        assertFalse(areServerPolicyRulesReady(rules.map { it.copy(enabled = false) }))
        assertFalse(areServerPolicyRulesReady(rules.map { it.copy(outboundTag = AppConfig.TAG_DIRECT) }))
        assertFalse(areServerPolicyRulesReady(listOf(RulesetItem(outboundTag = AppConfig.TAG_DIRECT)) + rules))
    }
}

package com.v2ray.ang.handler

import com.v2ray.ang.AppConfig
import com.v2ray.ang.dto.RulesetItem
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class RegionalPolicyManagerTest {

    @Test
    fun requiresEveryEnabledRussiaRestrictionSource() {
        assertTrue(areRussiaRestrictionRulesReady(completeRussiaRestrictionRules()))

        val missingReFilter = completeRussiaRestrictionRules().map { rule ->
            if (rule.remarks == "RKN restricted IP ranges") {
                rule.copy(ip = rule.ip?.filterNot { it == "geoip:re-filter" })
            } else {
                rule
            }
        }
        assertFalse(areRussiaRestrictionRulesReady(missingReFilter))
    }

    @Test
    fun rejectsDisabledRussiaRestrictionRule() {
        val disabledDomainRule = completeRussiaRestrictionRules().map { rule ->
            if (rule.remarks == "RKN restricted domains") {
                rule.copy(enabled = false)
            } else {
                rule
            }
        }

        assertFalse(areRussiaRestrictionRulesReady(disabledDomainRule))
    }

    @Test
    fun parsesChecksumWithPublishedFilePath() {
        val hash = "3cdbe88e7561c4a63ae242dfb0e3d6c1cab9000ffc370f584d840f2874fa4707"

        assertEquals(hash, parseSha256Checksum("$hash  ./publish/geosite.dat\n"))
    }

    @Test
    fun rejectsMalformedChecksum() {
        assertNull(parseSha256Checksum("not-a-sha256 geosite.dat"))
    }

    @Test
    fun freshnessRequiresFilesAndRecentMonotonicTimestamp() {
        val now = 10L * 60L * 60L * 1000L

        assertTrue(
            isRegionalPolicyAssetFresh(
                lastUpdated = now - (5L * 60L * 60L * 1000L),
                now = now,
                filesReady = true,
            ),
        )
        assertFalse(
            isRegionalPolicyAssetFresh(
                lastUpdated = now - (6L * 60L * 60L * 1000L),
                now = now,
                filesReady = true,
            ),
        )
        assertFalse(isRegionalPolicyAssetFresh(now, now, filesReady = false))
        assertFalse(isRegionalPolicyAssetFresh(now + 1L, now, filesReady = true))
    }

    private fun completeRussiaRestrictionRules(): List<RulesetItem> = listOf(
        RulesetItem(
            remarks = "RKN restricted domains",
            outboundTag = AppConfig.TAG_BLOCKED,
            domain = listOf("geosite:ru-blocked-all"),
        ),
        RulesetItem(
            remarks = "RKN restricted DNS safeguard",
            outboundTag = AppConfig.TAG_PROXY,
            port = "53",
        ),
        RulesetItem(
            remarks = "RKN restricted IP ranges",
            outboundTag = AppConfig.TAG_BLOCKED,
            ip = listOf(
                "geoip:ru-blocked",
                "geoip:ru-blocked-community",
                "geoip:re-filter",
            ),
        ),
    )
}

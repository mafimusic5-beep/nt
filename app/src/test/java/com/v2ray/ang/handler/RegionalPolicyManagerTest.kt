package com.v2ray.ang.handler

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class RegionalPolicyManagerTest {

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
}

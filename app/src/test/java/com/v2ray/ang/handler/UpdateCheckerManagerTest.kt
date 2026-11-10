package com.v2ray.ang.handler

import com.v2ray.ang.dto.GitHubRelease
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class UpdateCheckerManagerTest {
    private fun asset(name: String, origin: String = "mafimusic5-beep/nt") =
        GitHubRelease.Asset(
            name = name,
            browserDownloadUrl = "https://github.com/$origin/releases/download/v2.0.19/$name",
        )

    @Test
    fun matchingDeviceAbiBeatsUniversalAndOtherArchitectures() {
        val arm = asset("skryon-playstore-arm64-v8a.apk")
        val universal = asset("skryon-playstore-universal.apk")
        val x86 = asset("skryon-playstore-x86.apk")

        assertEquals(
            arm.browserDownloadUrl,
            UpdateCheckerManager.selectApkUrl(
                assets = listOf(x86, universal, arm),
                supportedAbis = listOf("arm64-v8a", "armeabi-v7a"),
                isFdroid = false,
            ),
        )
    }

    @Test
    fun universalApkIsFallbackForUnlistedTvAbi() {
        val universal = asset("skryon-playstore-universal.apk")
        assertEquals(
            universal.browserDownloadUrl,
            UpdateCheckerManager.selectApkUrl(
                assets = listOf(asset("skryon-playstore-x86.apk"), universal),
                supportedAbis = listOf("arm64-v8a"),
                isFdroid = false,
            ),
        )
    }

    @Test
    fun fdroidVariantNeverInstallsOverPlaystorePackage() {
        val fdroid = asset("skryon-fdroid-arm64-v8a.apk")
        assertNull(
            UpdateCheckerManager.selectApkUrl(
                assets = listOf(fdroid),
                supportedAbis = listOf("arm64-v8a"),
                isFdroid = false,
            ),
        )
        assertEquals(
            fdroid.browserDownloadUrl,
            UpdateCheckerManager.selectApkUrl(
                assets = listOf(fdroid),
                supportedAbis = listOf("arm64-v8a"),
                isFdroid = true,
            ),
        )
    }

    @Test
    fun excludesUntrustedHostsAndNonApkAssets() {
        val external = asset("skryon-playstore-arm64-v8a.apk", "other-user/other-repo")
        val archive = asset("skryon-playstore-arm64-v8a.zip")
        assertNull(
            UpdateCheckerManager.selectApkUrl(
                assets = listOf(external, archive),
                supportedAbis = listOf("arm64-v8a"),
                isFdroid = false,
            ),
        )
    }
}

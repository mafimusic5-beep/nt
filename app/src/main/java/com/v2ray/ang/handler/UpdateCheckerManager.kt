package com.v2ray.ang.handler

import android.content.Context
import android.os.Build
import android.util.Log
import com.v2ray.ang.AppConfig
import com.v2ray.ang.BuildConfig
import com.v2ray.ang.dto.CheckUpdateResult
import com.v2ray.ang.dto.GitHubRelease
import com.v2ray.ang.extension.concatUrl
import com.v2ray.ang.util.HttpUtil
import com.v2ray.ang.util.JsonUtil
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.File
import java.io.FileOutputStream
import java.net.URI

object UpdateCheckerManager {
    suspend fun checkForUpdate(includePreRelease: Boolean = false): CheckUpdateResult = withContext(Dispatchers.IO) {
        val url = if (includePreRelease) {
            AppConfig.APP_API_URL
        } else {
            AppConfig.APP_API_URL.concatUrl("latest")
        }

        var response = HttpUtil.getUrlContent(url, 5000)
        if (response.isNullOrEmpty()) {
            val httpPort = SettingsManager.getHttpPort()
            response = HttpUtil.getUrlContent(url, 5000, httpPort)
                ?: throw IllegalStateException("Failed to get response")
        }

        val latestRelease = if (includePreRelease) {
            JsonUtil.fromJson(response, Array<GitHubRelease>::class.java)
                ?.firstOrNull()
                ?: throw IllegalStateException("No pre-release found")
        } else {
            JsonUtil.fromJson(response, GitHubRelease::class.java)
        }
        if (latestRelease == null) {
            return@withContext CheckUpdateResult(hasUpdate = false)
        }

        val latestVersion = latestRelease.tagName.removePrefix("v")
        Log.i(
            AppConfig.TAG,
            "Found new version: $latestVersion (current: ${BuildConfig.VERSION_NAME})"
        )

        return@withContext if (compareVersions(latestVersion, BuildConfig.VERSION_NAME) > 0) {
            val downloadUrl = getDownloadUrl(latestRelease, Build.SUPPORTED_ABIS.toList())
            CheckUpdateResult(
                hasUpdate = true,
                latestVersion = latestVersion,
                releaseNotes = latestRelease.body,
                downloadUrl = downloadUrl,
                isPreRelease = latestRelease.prerelease
            )
        } else {
            CheckUpdateResult(hasUpdate = false)
        }
    }

    /**
     * Download only an APK advertised by our own GitHub release. The Android
     * package installer performs the final signing-certificate check.
     * A partial download must never replace the previously completed APK.
     */
    suspend fun downloadApk(context: Context, downloadUrl: String): File? = withContext(Dispatchers.IO) {
        if (!isOfficialReleaseApkUrl(downloadUrl)) {
            Log.e(AppConfig.TAG, "Rejected non-SKRYON APK URL")
            return@withContext null
        }

        val output = File(context.cacheDir, "skryon-update.apk")
        val partial = File(context.cacheDir, "skryon-update.apk.part")
        val ports = listOf(0, SettingsManager.getHttpPort()).distinct()

        for (port in ports) {
            val connection = HttpUtil.createProxyConnection(downloadUrl, port, 15_000, 30_000, true)
                ?: continue
            try {
                if (connection.responseCode != 200) {
                    throw IllegalStateException("Update download HTTP ${connection.responseCode}")
                }

                FileOutputStream(partial).use { destination ->
                    connection.inputStream.use { source -> source.copyTo(destination) }
                }
                val isZip = partial.inputStream().use { it.read() == 0x50 && it.read() == 0x4b }
                if (partial.length() < 500_000L || !isZip) {
                    throw IllegalStateException("Downloaded APK is incomplete or invalid")
                }
                if (output.exists() && !output.delete()) {
                    throw IllegalStateException("Could not replace cached APK")
                }
                if (!partial.renameTo(output)) {
                    throw IllegalStateException("Could not finalize APK download")
                }
                return@withContext output
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                Log.w(AppConfig.TAG, "Update download failed (proxy port $port): ${e.message}")
            } finally {
                partial.delete()
                connection.disconnect()
            }
        }
        null
    }

    private fun compareVersions(version1: String, version2: String): Int {
        val v1 = version1.split(".")
        val v2 = version2.split(".")

        for (i in 0 until maxOf(v1.size, v2.size)) {
            val num1 = if (i < v1.size) v1[i].toIntOrNull() ?: 0 else 0
            val num2 = if (i < v2.size) v2[i].toIntOrNull() ?: 0 else 0
            if (num1 != num2) return num1.compareTo(num2)
        }
        return 0
    }

    private fun getDownloadUrl(release: GitHubRelease, supportedAbis: List<String>): String {
        return selectApkUrl(
            release.assets,
            supportedAbis,
            isFdroid = BuildConfig.APPLICATION_ID.contains("fdroid", ignoreCase = true),
        ) ?: throw IllegalStateException("No compatible SKRYON APK in release")
    }

    internal fun selectApkUrl(
        assets: List<GitHubRelease.Asset>,
        supportedAbis: List<String>,
        isFdroid: Boolean,
    ): String? {
        val eligible = assets.filter { asset ->
            asset.name.endsWith(".apk", ignoreCase = true) &&
                asset.name.contains("fdroid", ignoreCase = true) == isFdroid &&
                isOfficialReleaseApkUrl(asset.browserDownloadUrl)
        }
        for (abi in supportedAbis) {
            eligible.firstOrNull { it.name.contains(abi, ignoreCase = true) }?.let {
                return it.browserDownloadUrl
            }
        }
        return eligible.firstOrNull { it.name.contains("universal", ignoreCase = true) }
            ?.browserDownloadUrl
    }

    private fun isOfficialReleaseApkUrl(url: String): Boolean = try {
        val uri = URI(url)
        uri.scheme.equals("https", ignoreCase = true) &&
            uri.host.equals("github.com", ignoreCase = true) &&
            uri.path.startsWith("/mafimusic5-beep/nt/releases/download/") &&
            uri.userInfo == null &&
            uri.query == null &&
            uri.fragment == null
    } catch (_: Exception) {
        false
    }
}

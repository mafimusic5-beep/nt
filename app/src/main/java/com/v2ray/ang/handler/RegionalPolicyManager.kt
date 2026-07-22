package com.v2ray.ang.handler

import android.content.Context
import android.util.Log
import com.v2ray.ang.AppConfig
import com.v2ray.ang.util.HttpUtil
import com.v2ray.ang.util.Utils
import java.io.File
import java.io.FileOutputStream
import java.io.IOException
import java.net.HttpURLConnection
import java.security.MessageDigest
import java.util.Locale
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext

internal enum class RegionalPolicyMode(val storageValue: String) {
    International("international"),
    Russia("russia"),
}

/**
 * Applies the user-selected regional policy and maintains the data files used by
 * the Russia restrictions ruleset.
 *
 * Restricted destinations are routed to the existing Xray `blackhole` outbound.
 * The source files are downloaded over HTTPS and verified against the SHA-256
 * checksums published by the same upstream release branch before replacement.
 */
internal object RegionalPolicyManager {
    private const val ROUTING_PRESET_GLOBAL = 2
    private const val ROUTING_PRESET_RUSSIA = 4
    private const val RUNET_SOURCE_REPOSITORY = "runetfreedom/russia-v2ray-rules-dat"
    private const val CONNECT_TIMEOUT_MS = 15_000
    private const val READ_TIMEOUT_MS = 120_000
    private const val MIN_DATA_FILE_BYTES = 16L * 1024L
    private const val MAX_DATA_FILE_BYTES = 128L * 1024L * 1024L
    private const val MAX_CHECKSUM_BYTES = 4L * 1024L
    private const val RKN_RULE_REMARKS_PREFIX = "RKN restricted"

    private val updateMutex = Mutex()

    private data class GeoAsset(
        val fileName: String,
        val checksumFileName: String = "$fileName.sha256sum",
    )

    private val requiredAssets = listOf(
        GeoAsset(AppConfig.GEOSITE_DAT),
        GeoAsset(AppConfig.GEOIP_DAT),
    )
    private val sourceBaseUrls = listOf(
        "https://raw.githubusercontent.com/runetfreedom/russia-v2ray-rules-dat/release",
        "https://github.com/runetfreedom/russia-v2ray-rules-dat/releases/latest/download",
    )

    fun readMode(): RegionalPolicyMode? {
        return when (MmkvManager.decodeSettingsString(AppConfig.PREF_REGIONAL_POLICY_MODE)) {
            RegionalPolicyMode.International.storageValue -> RegionalPolicyMode.International
            RegionalPolicyMode.Russia.storageValue -> RegionalPolicyMode.Russia
            else -> null
        }
    }

    fun isRussiaModeEnabled(): Boolean = readMode() == RegionalPolicyMode.Russia

    suspend fun apply(context: Context, mode: RegionalPolicyMode): Result<Unit> {
        if (mode == RegionalPolicyMode.Russia) {
            val assetResult = ensureRussiaAssetsFresh(context.applicationContext, force = true)
            if (assetResult.isFailure) {
                return Result.failure(
                    assetResult.exceptionOrNull()
                        ?: IOException("Unable to prepare Russia policy data"),
                )
            }
        }

        return runCatching { configureRoutingPolicy(context.applicationContext, mode) }
    }

    suspend fun prepareForConnection(context: Context): Result<Unit> {
        if (!isRussiaModeEnabled()) {
            return Result.success(Unit)
        }
        val assetResult = ensureRussiaAssetsFresh(context.applicationContext)
        if (assetResult.isFailure) {
            return assetResult
        }
        return runCatching {
            // Re-apply the policy before every connection so manually edited or
            // legacy rules cannot place an allow rule before the restrictions.
            configureRoutingPolicy(context.applicationContext, RegionalPolicyMode.Russia)
        }
    }

    fun isPolicyReadyForServiceStart(context: Context): Boolean {
        if (!isRussiaModeEnabled()) return true

        val assetDirectory = File(Utils.userAssetPath(context.applicationContext))
        val filesReady = requiredAssets.all { asset ->
            val file = File(assetDirectory, asset.fileName)
            file.isFile && file.length() >= MIN_DATA_FILE_BYTES
        }
        val lastUpdated = MmkvManager.decodeSettingsLong(
            AppConfig.PREF_RF_POLICY_ASSETS_UPDATED_AT,
            0L,
        )
        if (!isRegionalPolicyAssetFresh(lastUpdated, System.currentTimeMillis(), filesReady)) {
            return false
        }

        val firstRules = MmkvManager.decodeRoutingRulesets()?.take(3).orEmpty()
        val domainRuleReady = firstRules.any { rule ->
            rule.outboundTag == AppConfig.TAG_BLOCKED &&
                rule.domain?.contains("geosite:ru-blocked-all") == true
        }
        val dnsRuleReady = firstRules.any { rule ->
            rule.outboundTag == AppConfig.TAG_PROXY && rule.port == "53"
        }
        val ipRuleReady = firstRules.any { rule ->
            rule.outboundTag == AppConfig.TAG_BLOCKED &&
                rule.ip?.containsAll(
                    listOf(
                        "geoip:ru-blocked",
                        "geoip:ru-blocked-community",
                    ),
                ) == true
        }
        return domainRuleReady && dnsRuleReady && ipRuleReady &&
            MmkvManager.decodeSettingsString(AppConfig.PREF_ROUTING_DOMAIN_STRATEGY) == "IPIfNonMatch" &&
            MmkvManager.decodeSettingsBool(AppConfig.PREF_LOCAL_DNS_ENABLED, false) &&
            MmkvManager.decodeSettingsBool(AppConfig.PREF_SNIFFING_ENABLED, true) != false &&
            MmkvManager.decodeSettingsBool(AppConfig.PREF_ROUTE_ONLY_ENABLED, false)
    }

    suspend fun ensureRussiaAssetsFresh(context: Context, force: Boolean = false): Result<Unit> {
        if (!isRussiaModeEnabled() && !force) {
            return Result.success(Unit)
        }

        return updateMutex.withLock {
            withContext(Dispatchers.IO) {
                val assetDirectory = File(Utils.userAssetPath(context.applicationContext))
                val filesReady = requiredAssets.all { asset ->
                    val file = File(assetDirectory, asset.fileName)
                    file.isFile && file.length() >= MIN_DATA_FILE_BYTES
                }
                val lastUpdated = MmkvManager.decodeSettingsLong(
                    AppConfig.PREF_RF_POLICY_ASSETS_UPDATED_AT,
                    0L,
                )
                if (!force && isRegionalPolicyAssetFresh(lastUpdated, System.currentTimeMillis(), filesReady)) {
                    return@withContext Result.success(Unit)
                }

                runCatching {
                    downloadVerifiedAssets(assetDirectory)
                    MmkvManager.encodeSettings(
                        AppConfig.PREF_GEO_FILES_SOURCES,
                        RUNET_SOURCE_REPOSITORY,
                    )
                    MmkvManager.encodeSettings(
                        AppConfig.PREF_RF_POLICY_ASSETS_UPDATED_AT,
                        System.currentTimeMillis(),
                    )
                    Unit
                }.onFailure { error ->
                    Log.e(AppConfig.TAG, "Failed to refresh Russia policy data", error)
                }
            }
        }
    }

    private fun moveRestrictionRulesToFront() {
        val rules = MmkvManager.decodeRoutingRulesets() ?: return
        val restrictionRules = rules.filter { rule ->
            rule.remarks?.startsWith(RKN_RULE_REMARKS_PREFIX) == true
        }
        if (restrictionRules.isEmpty()) {
            throw IllegalStateException("Russia policy restrictions rules are missing")
        }
        val remainingRules = rules.filterNot { rule ->
            rule.remarks?.startsWith(RKN_RULE_REMARKS_PREFIX) == true
        }
        MmkvManager.encodeRoutingRulesets((restrictionRules + remainingRules).toMutableList())
    }

    private fun configureRoutingPolicy(context: Context, mode: RegionalPolicyMode) {
        val routingPreset = when (mode) {
            RegionalPolicyMode.International -> ROUTING_PRESET_GLOBAL
            RegionalPolicyMode.Russia -> ROUTING_PRESET_RUSSIA
        }

        SettingsManager.resetRoutingRulesetsFromPresets(context.applicationContext, routingPreset)
        if (mode == RegionalPolicyMode.Russia) {
            moveRestrictionRulesToFront()
            MmkvManager.encodeSettings(AppConfig.PREF_ROUTING_DOMAIN_STRATEGY, "IPIfNonMatch")
            // Resolve device DNS inside Xray. This keeps DNS reachable through
            // the tunnel and lets the domain rules run before any IP fallback.
            MmkvManager.encodeSettings(AppConfig.PREF_LOCAL_DNS_ENABLED, true)
            MmkvManager.encodeSettings(AppConfig.PREF_SNIFFING_ENABLED, true)
            MmkvManager.encodeSettings(AppConfig.PREF_ROUTE_ONLY_ENABLED, true)
        }
        MmkvManager.encodeSettings(AppConfig.PREF_REGIONAL_POLICY_MODE, mode.storageValue)
    }

    @Throws(IOException::class)
    private fun downloadVerifiedAssets(assetDirectory: File) {
        if (!assetDirectory.exists() && !assetDirectory.mkdirs()) {
            throw IOException("Unable to create policy data directory")
        }

        val temporaryFiles = mutableMapOf<GeoAsset, File>()
        try {
            requiredAssets.forEach { asset ->
                val temporaryFile = File(assetDirectory, ".${asset.fileName}.rkn.tmp")
                downloadAndVerifyAsset(asset, temporaryFile)
                temporaryFiles[asset] = temporaryFile
            }
            replaceAssetsTransactionally(assetDirectory, temporaryFiles)
        } finally {
            temporaryFiles.values.forEach { it.delete() }
        }
    }

    @Throws(IOException::class)
    private fun downloadAndVerifyAsset(asset: GeoAsset, target: File) {
        var lastError: Exception? = null
        sourceBaseUrls.forEach { baseUrl ->
            try {
                if (target.exists() && !target.delete()) {
                    throw IOException("Unable to replace temporary ${asset.fileName}")
                }
                val expectedHash = downloadChecksum(asset, baseUrl)
                val actualHash = downloadDataFile(asset, target, baseUrl)
                if (!actualHash.equals(expectedHash, ignoreCase = true)) {
                    throw IOException("Checksum mismatch for ${asset.fileName}")
                }
                return
            } catch (error: Exception) {
                lastError = error
                target.delete()
            }
        }
        throw IOException("Unable to download verified ${asset.fileName}", lastError)
    }

    @Throws(IOException::class)
    private fun downloadChecksum(asset: GeoAsset, baseUrl: String): String {
        val bytes = downloadBytes(
            url = "$baseUrl/${asset.checksumFileName}",
            maximumBytes = MAX_CHECKSUM_BYTES,
        )
        return parseSha256Checksum(bytes.toString(Charsets.UTF_8))
            ?: throw IOException("Invalid checksum for ${asset.fileName}")
    }

    @Throws(IOException::class)
    private fun downloadDataFile(asset: GeoAsset, target: File, baseUrl: String): String {
        val connection = openConnection("$baseUrl/${asset.fileName}")
        try {
            if (connection.responseCode != HttpURLConnection.HTTP_OK) {
                throw IOException("HTTP ${connection.responseCode} for ${asset.fileName}")
            }
            val digest = MessageDigest.getInstance("SHA-256")
            var totalBytes = 0L
            connection.inputStream.buffered().use { input ->
                FileOutputStream(target).use { output ->
                    val buffer = ByteArray(DEFAULT_BUFFER_SIZE)
                    while (true) {
                        val count = input.read(buffer)
                        if (count < 0) break
                        totalBytes += count
                        if (totalBytes > MAX_DATA_FILE_BYTES) {
                            throw IOException("${asset.fileName} is too large")
                        }
                        digest.update(buffer, 0, count)
                        output.write(buffer, 0, count)
                    }
                    output.fd.sync()
                }
            }
            if (totalBytes < MIN_DATA_FILE_BYTES) {
                throw IOException("${asset.fileName} is unexpectedly small")
            }
            return digest.digest().joinToString("") { byte -> "%02x".format(byte) }
        } finally {
            connection.disconnect()
        }
    }

    @Throws(IOException::class)
    private fun downloadBytes(url: String, maximumBytes: Long): ByteArray {
        val connection = openConnection(url)
        try {
            if (connection.responseCode != HttpURLConnection.HTTP_OK) {
                throw IOException("HTTP ${connection.responseCode} for checksum")
            }
            connection.inputStream.buffered().use { input ->
                val output = java.io.ByteArrayOutputStream()
                val buffer = ByteArray(DEFAULT_BUFFER_SIZE)
                var totalBytes = 0L
                while (true) {
                    val count = input.read(buffer)
                    if (count < 0) break
                    totalBytes += count
                    if (totalBytes > maximumBytes) {
                        throw IOException("Checksum response is too large")
                    }
                    output.write(buffer, 0, count)
                }
                return output.toByteArray()
            }
        } finally {
            connection.disconnect()
        }
    }

    @Throws(IOException::class)
    private fun openConnection(url: String): HttpURLConnection {
        val connection = HttpUtil.createProxyConnection(
            urlStr = url,
            port = 0,
            connectTimeout = CONNECT_TIMEOUT_MS,
            readTimeout = READ_TIMEOUT_MS,
            needStream = true,
        ) ?: throw IOException("Unable to open policy data connection")
        connection.instanceFollowRedirects = true
        connection.useCaches = false
        connection.setRequestProperty("Connection", "close")
        connection.setRequestProperty("User-Agent", "Skryon-Android regional-policy-updater")
        return connection
    }

    @Throws(IOException::class)
    private fun replaceAssetsTransactionally(
        assetDirectory: File,
        temporaryFiles: Map<GeoAsset, File>,
    ) {
        val backups = mutableMapOf<GeoAsset, File>()
        val installedTargets = mutableListOf<File>()
        try {
            requiredAssets.forEach { asset ->
                val target = File(assetDirectory, asset.fileName)
                val backup = File(assetDirectory, ".${asset.fileName}.rkn.bak")
                if (!target.exists() && backup.exists() && !backup.renameTo(target)) {
                    throw IOException("Unable to recover backup for ${asset.fileName}")
                }
                if (backup.exists() && !backup.delete()) {
                    throw IOException("Unable to clear backup for ${asset.fileName}")
                }
                if (target.exists()) {
                    if (!target.renameTo(backup)) {
                        throw IOException("Unable to back up ${asset.fileName}")
                    }
                    backups[asset] = backup
                }
            }

            requiredAssets.forEach { asset ->
                val target = File(assetDirectory, asset.fileName)
                val temporary = temporaryFiles[asset]
                    ?: throw IOException("Missing temporary ${asset.fileName}")
                if (!temporary.renameTo(target)) {
                    throw IOException("Unable to install ${asset.fileName}")
                }
                installedTargets += target
            }

            backups.values.forEach { backup ->
                if (backup.exists() && !backup.delete()) {
                    Log.w(AppConfig.TAG, "Unable to delete regional policy backup ${backup.name}")
                }
            }
        } catch (error: Exception) {
            installedTargets.forEach { it.delete() }
            backups.forEach { (asset, backup) ->
                if (backup.exists()) {
                    val target = File(assetDirectory, asset.fileName)
                    if (!backup.renameTo(target)) {
                        Log.e(AppConfig.TAG, "Unable to restore regional policy asset ${asset.fileName}")
                    }
                }
            }
            throw if (error is IOException) error else IOException("Unable to install policy data", error)
        }
    }
}

internal fun parseSha256Checksum(value: String): String? {
    return Regex("(?i)\\b[0-9a-f]{64}\\b")
        .find(value)
        ?.value
        ?.lowercase(Locale.ROOT)
}

internal fun isRegionalPolicyAssetFresh(
    lastUpdated: Long,
    now: Long,
    filesReady: Boolean,
): Boolean {
    if (!filesReady || lastUpdated <= 0L || now < lastUpdated) return false
    return now - lastUpdated < 6L * 60L * 60L * 1000L
}

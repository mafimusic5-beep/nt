package com.v2ray.ang.ui.premium.vpn

import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.os.SystemClock
import java.net.HttpURLConnection
import java.net.URL
import kotlin.math.min
import javax.net.ssl.HttpsURLConnection
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

enum class PreVpnConnectionQuality {
    Good,
    Unstable,
    Critical,
    Offline,
    Unknown;

    val shouldWarn: Boolean
        get() = this == Critical || this == Offline
}

data class PreVpnConnectionAssessment(
    val quality: PreVpnConnectionQuality,
)

internal data class PreVpnConnectionSnapshot(
    val hasActiveNetwork: Boolean,
    val hasInternetCapability: Boolean,
    val isValidated: Boolean,
    val downstreamBandwidthKbps: Int?,
)

/**
 * Warns only when a real download measurement confirms at most 0.5 Mbps or when
 * there is no usable internet connection. Latency must never create a speed warning.
 */
internal fun classifyPreVpnConnection(snapshot: PreVpnConnectionSnapshot): PreVpnConnectionQuality {
    if (!snapshot.hasActiveNetwork || !snapshot.hasInternetCapability) {
        return PreVpnConnectionQuality.Offline
    }

    val downstreamBandwidthKbps = snapshot.downstreamBandwidthKbps?.takeIf { it > 0 }
    if (downstreamBandwidthKbps != null) {
        return if (downstreamBandwidthKbps <= CRITICAL_BANDWIDTH_KBPS) {
            PreVpnConnectionQuality.Critical
        } else {
            PreVpnConnectionQuality.Good
        }
    }

    // Android validation alone is not allowed to call a working connection "critical".
    // If both Android validation and the real download fail, there is no usable internet.
    return if (snapshot.isValidated) {
        PreVpnConnectionQuality.Unknown
    } else {
        PreVpnConnectionQuality.Offline
    }
}

internal fun calculateDownloadedBandwidthKbps(downloadedBytes: Long, elapsedMs: Long): Int? {
    if (downloadedBytes <= 0L || elapsedMs <= 0L) return null
    return ((downloadedBytes * 8L) / elapsedMs)
        .coerceAtMost(Int.MAX_VALUE.toLong())
        .toInt()
}

internal class PreVpnConnectionQualityChecker(context: Context) {
    private val connectivityManager =
        context.applicationContext.getSystemService(Context.CONNECTIVITY_SERVICE) as? ConnectivityManager

    suspend fun assess(): PreVpnConnectionAssessment = withContext(Dispatchers.IO) {
        try {
            assessCurrentNetwork()
        } catch (error: CancellationException) {
            throw error
        } catch (_: Exception) {
            // Diagnostics must never prevent the existing VPN connection flow.
            PreVpnConnectionAssessment(PreVpnConnectionQuality.Unknown)
        }
    }

    private suspend fun assessCurrentNetwork(): PreVpnConnectionAssessment {
        val manager = connectivityManager
            ?: return PreVpnConnectionAssessment(PreVpnConnectionQuality.Unknown)
        val network = manager.activeNetwork
        val capabilities = network?.let(manager::getNetworkCapabilities)
        val hasActiveNetwork = network != null && capabilities != null
        val hasInternetCapability =
            capabilities?.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET) == true
        val isValidated =
            capabilities?.hasCapability(NetworkCapabilities.NET_CAPABILITY_VALIDATED) == true

        val measuredDownstreamBandwidthKbps = when {
            network == null || !hasInternetCapability -> null
            else -> measureDownloadBandwidthKbps(network)
        }
        val quality = classifyPreVpnConnection(
            PreVpnConnectionSnapshot(
                hasActiveNetwork = hasActiveNetwork,
                hasInternetCapability = hasInternetCapability,
                isValidated = isValidated,
                downstreamBandwidthKbps = measuredDownstreamBandwidthKbps,
            ),
        )
        return PreVpnConnectionAssessment(quality)
    }

    private fun measureDownloadBandwidthKbps(network: Network): Int? {
        val url = "$DOWNLOAD_PROBE_URL&nonce=${SystemClock.elapsedRealtime()}"
        val connection = try {
            network.openConnection(URL(url)) as? HttpsURLConnection ?: return null
        } catch (_: Exception) {
            return null
        }
        var downloadedBytes = 0L
        var startedAt = 0L
        var completedMeasurement = false
        try {
            connection.connectTimeout = DOWNLOAD_CONNECT_TIMEOUT_MS
            connection.readTimeout = DOWNLOAD_READ_TIMEOUT_MS
            connection.requestMethod = "GET"
            connection.instanceFollowRedirects = false
            connection.useCaches = false
            connection.setRequestProperty("Accept-Encoding", "identity")
            connection.setRequestProperty("Cache-Control", "no-cache")

            if (connection.responseCode != HttpURLConnection.HTTP_OK) return null
            startedAt = SystemClock.elapsedRealtime()
            connection.inputStream.use { input ->
                val buffer = ByteArray(DOWNLOAD_BUFFER_BYTES)
                while (downloadedBytes < DOWNLOAD_PROBE_BYTES) {
                    val remaining = (DOWNLOAD_PROBE_BYTES - downloadedBytes).toInt()
                    val count = input.read(buffer, 0, min(buffer.size, remaining))
                    if (count < 0) break
                    downloadedBytes += count
                    if (
                        downloadedBytes >= DOWNLOAD_PROBE_BYTES ||
                        SystemClock.elapsedRealtime() - startedAt >= DOWNLOAD_MAX_MEASURE_MS
                    ) {
                        completedMeasurement = true
                        break
                    }
                }
            }
        } catch (_: Exception) {
            // A stalled or interrupted endpoint is not proof of poor user bandwidth.
            return null
        } finally {
            connection.disconnect()
        }

        if (
            startedAt == 0L ||
            !completedMeasurement ||
            downloadedBytes < DOWNLOAD_MIN_MEASURE_BYTES
        ) {
            return null
        }
        return calculateDownloadedBandwidthKbps(
            downloadedBytes = downloadedBytes,
            elapsedMs = SystemClock.elapsedRealtime() - startedAt,
        )
    }

    private companion object {
        const val DOWNLOAD_PROBE_BYTES = 128L * 1024L
        const val DOWNLOAD_MIN_MEASURE_BYTES = 16L * 1024L
        const val DOWNLOAD_BUFFER_BYTES = 16 * 1024
        const val DOWNLOAD_CONNECT_TIMEOUT_MS = 2_500
        const val DOWNLOAD_READ_TIMEOUT_MS = 1_500
        const val DOWNLOAD_MAX_MEASURE_MS = 3_500L
        const val DOWNLOAD_PROBE_URL = "https://speed.cloudflare.com/__down?bytes=131072"
    }
}

private const val CRITICAL_BANDWIDTH_KBPS = 500

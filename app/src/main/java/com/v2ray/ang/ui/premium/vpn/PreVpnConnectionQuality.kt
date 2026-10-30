package com.v2ray.ang.ui.premium.vpn

import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.os.SystemClock
import java.net.HttpURLConnection
import java.net.URL
import javax.net.ssl.HttpsURLConnection
import kotlin.math.min
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject

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
 * Performs a short real download before the VPN starts and warns only when the
 * measured downstream speed is below 1 MB/s (8 Mbps). If the probe itself cannot
 * produce a trustworthy sample, it must not create a false speed warning.
 */
internal fun classifyPreVpnConnection(snapshot: PreVpnConnectionSnapshot): PreVpnConnectionQuality {
    if (!snapshot.hasActiveNetwork || !snapshot.hasInternetCapability) {
        return PreVpnConnectionQuality.Offline
    }

    val downstreamBandwidthKbps = snapshot.downstreamBandwidthKbps?.takeIf { it > 0 }
    if (downstreamBandwidthKbps != null) {
        return if (downstreamBandwidthKbps < CRITICAL_BANDWIDTH_KBPS) {
            PreVpnConnectionQuality.Critical
        } else {
            PreVpnConnectionQuality.Good
        }
    }

    // Failure of the speed endpoint alone is not proof of a slow connection.
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
        VpnUiDebugLogger.log(
            hypothesisId = "H-PREFLIGHT",
            location = "PreVpnConnectionQuality.kt:assess",
            message = "VPN connect preflight started",
            runId = "vpn-connect",
            data = JSONObject().put("stage", "preflight_start"),
        )
        try {
            val result = assessCurrentNetwork()
            VpnUiDebugLogger.log(
                hypothesisId = "H-PREFLIGHT",
                location = "PreVpnConnectionQuality.kt:assess",
                message = "VPN connect preflight finished",
                runId = "vpn-connect",
                data = JSONObject()
                    .put("stage", "preflight_finish")
                    .put("quality", result.quality.name),
            )
            result
        } catch (error: CancellationException) {
            VpnUiDebugLogger.log(
                hypothesisId = "H-PREFLIGHT",
                location = "PreVpnConnectionQuality.kt:assess",
                message = "VPN connect preflight cancelled",
                runId = "vpn-connect",
                data = JSONObject()
                    .put("stage", "preflight_cancelled")
                    .put("error", error.message ?: "cancelled"),
            )
            throw error
        } catch (error: Exception) {
            // Diagnostics must never prevent the existing VPN connection flow.
            VpnUiDebugLogger.log(
                hypothesisId = "H-PREFLIGHT",
                location = "PreVpnConnectionQuality.kt:assess",
                message = "VPN connect preflight failed; continuing",
                runId = "vpn-connect",
                data = JSONObject()
                    .put("stage", "preflight_error")
                    .put("error", error.message ?: error.javaClass.simpleName),
            )
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
        VpnUiDebugLogger.log(
            hypothesisId = "H-PREFLIGHT",
            location = "PreVpnConnectionQuality.kt:assessCurrentNetwork",
            message = "VPN connect network snapshot",
            runId = "vpn-connect",
            data = JSONObject()
                .put("stage", "network_snapshot")
                .put("quality", quality.name)
                .put("speedProbe", if (measuredDownstreamBandwidthKbps != null) "measured" else "unavailable"),
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
        var completedSample = false
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
                    val elapsedMs = SystemClock.elapsedRealtime() - startedAt
                    if (elapsedMs >= DOWNLOAD_MAX_MEASURE_MS) {
                        completedSample = true
                        break
                    }

                    val remaining = (DOWNLOAD_PROBE_BYTES - downloadedBytes).toInt()
                    val count = input.read(buffer, 0, min(buffer.size, remaining))
                    if (count < 0) {
                        completedSample = downloadedBytes >= DOWNLOAD_PROBE_BYTES
                        break
                    }
                    downloadedBytes += count

                    if (downloadedBytes >= DOWNLOAD_PROBE_BYTES) {
                        completedSample = true
                        break
                    }
                }
            }
        } catch (_: Exception) {
            // Endpoint failure or an interrupted transfer is not proof of poor user bandwidth.
            return null
        } finally {
            connection.disconnect()
        }

        if (
            startedAt == 0L ||
            !completedSample ||
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
        const val DOWNLOAD_PROBE_BYTES = 512L * 1024L
        const val DOWNLOAD_MIN_MEASURE_BYTES = 16L * 1024L
        const val DOWNLOAD_BUFFER_BYTES = 16 * 1024
        const val DOWNLOAD_CONNECT_TIMEOUT_MS = 2_500
        const val DOWNLOAD_READ_TIMEOUT_MS = 3_000
        const val DOWNLOAD_MAX_MEASURE_MS = 2_500L
        const val DOWNLOAD_PROBE_URL = "https://speed.cloudflare.com/__down?bytes=524288"
    }
}

private const val CRITICAL_BANDWIDTH_KBPS = 8_000

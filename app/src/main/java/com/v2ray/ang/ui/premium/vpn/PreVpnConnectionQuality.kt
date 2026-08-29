package com.v2ray.ang.ui.premium.vpn

import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.os.SystemClock
import com.v2ray.ang.AppConfig
import java.net.HttpURLConnection
import java.net.URL
import javax.net.ssl.HttpsURLConnection
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.withContext

enum class PreVpnConnectionQuality {
    Good,
    Unstable,
    Critical,
    Offline,
    Unknown;

    val shouldWarn: Boolean
        get() = this == Unstable || this == Critical || this == Offline
}

data class PreVpnConnectionAssessment(
    val quality: PreVpnConnectionQuality,
)

internal data class PreVpnConnectionSnapshot(
    val hasActiveNetwork: Boolean,
    val hasInternetCapability: Boolean,
    val isValidated: Boolean,
    val downstreamBandwidthKbps: Int?,
    val probeLatenciesMs: List<Long?>,
)

/**
 * Converts Android's network state and a few tiny HTTPS round trips into a coarse,
 * non-numeric quality level. The UI deliberately never exposes timings or a score.
 */
internal fun classifyPreVpnConnection(snapshot: PreVpnConnectionSnapshot): PreVpnConnectionQuality {
    if (!snapshot.hasActiveNetwork || !snapshot.hasInternetCapability) {
        return PreVpnConnectionQuality.Offline
    }
    if (!snapshot.isValidated) {
        return PreVpnConnectionQuality.Critical
    }

    val downstreamBandwidthKbps = snapshot.downstreamBandwidthKbps?.takeIf { it > 0 }
    if (downstreamBandwidthKbps != null && downstreamBandwidthKbps < 128) {
        return PreVpnConnectionQuality.Critical
    }

    val successfulProbes = snapshot.probeLatenciesMs.filterNotNull().sorted()
    // A validated Android network is allowed through if the probe endpoint itself is unavailable,
    // unless Android independently reports very low bandwidth. This avoids endpoint-wide false warnings.
    if (successfulProbes.isEmpty()) {
        return if (downstreamBandwidthKbps != null && downstreamBandwidthKbps < 512) {
            PreVpnConnectionQuality.Unstable
        } else {
            PreVpnConnectionQuality.Unknown
        }
    }

    val medianLatencyMs = successfulProbes[successfulProbes.size / 2]
    val jitterMs = successfulProbes.last() - successfulProbes.first()
    val failedProbeCount = snapshot.probeLatenciesMs.size - successfulProbes.size
    val severeProbeLoss = snapshot.probeLatenciesMs.size >= 3 && successfulProbes.size <= 1
    if (severeProbeLoss || medianLatencyMs >= 1_300L) {
        return PreVpnConnectionQuality.Critical
    }

    val lowBandwidth = downstreamBandwidthKbps != null && downstreamBandwidthKbps < 512
    if (lowBandwidth || failedProbeCount > 0 || medianLatencyMs >= 650L || jitterMs >= 400L) {
        return PreVpnConnectionQuality.Unstable
    }

    return PreVpnConnectionQuality.Good
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

        val probeLatencies = when {
            network == null || !hasInternetCapability || !isValidated -> emptyList()
            else -> collectProbeLatencies(network)
        }
        val quality = classifyPreVpnConnection(
            PreVpnConnectionSnapshot(
                hasActiveNetwork = hasActiveNetwork,
                hasInternetCapability = hasInternetCapability,
                isValidated = isValidated,
                downstreamBandwidthKbps = capabilities?.linkDownstreamBandwidthKbps,
                probeLatenciesMs = probeLatencies,
            ),
        )
        return PreVpnConnectionAssessment(quality)
    }

    private suspend fun collectProbeLatencies(network: Network): List<Long?> {
        val primarySamples = probeBatch(network, AppConfig.DELAY_TEST_URL)
        if (primarySamples.any { it != null }) {
            return primarySamples
        }

        val fallbackSamples = probeBatch(network, AppConfig.DELAY_TEST_URL2)
        return if (fallbackSamples.any { it != null }) fallbackSamples else emptyList()
    }

    private suspend fun probeBatch(network: Network, url: String): List<Long?> = coroutineScope {
        List(PROBE_COUNT) {
            async { measureHttpsRoundTrip(network, url) }
        }.awaitAll()
    }

    private fun measureHttpsRoundTrip(network: Network, url: String): Long? {
        val connection = try {
            network.openConnection(URL(url)) as? HttpsURLConnection ?: return null
        } catch (error: CancellationException) {
            throw error
        } catch (_: Exception) {
            return null
        }
        val startedAt = SystemClock.elapsedRealtime()
        return try {
            connection.connectTimeout = PROBE_TIMEOUT_MS
            connection.readTimeout = PROBE_TIMEOUT_MS
            connection.requestMethod = "GET"
            connection.instanceFollowRedirects = false
            connection.useCaches = false
            connection.setRequestProperty("Cache-Control", "no-cache")

            if (connection.responseCode == HttpURLConnection.HTTP_NO_CONTENT) {
                SystemClock.elapsedRealtime() - startedAt
            } else {
                null
            }
        } catch (error: CancellationException) {
            throw error
        } catch (_: Exception) {
            null
        } finally {
            connection.disconnect()
        }
    }

    private companion object {
        const val PROBE_COUNT = 3
        const val PROBE_TIMEOUT_MS = 1_800
    }
}

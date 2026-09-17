package com.v2ray.ang.ui.premium.vpn

import android.content.Context
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
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
 * Uses only Android's local network capabilities. No third-party speed-test request
 * is made before the VPN starts. Latency must never create a speed warning.
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

        val measuredDownstreamBandwidthKbps = capabilities
            ?.linkDownstreamBandwidthKbps
            ?.takeIf { hasInternetCapability && it > 0 }
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
                .put("reason", buildString {
                    append("active=").append(hasActiveNetwork)
                    append(",internet=").append(hasInternetCapability)
                    append(",validated=").append(isValidated)
                    append(",downKbps=").append(measuredDownstreamBandwidthKbps ?: -1)
                }),
        )
        return PreVpnConnectionAssessment(quality)
    }

}

private const val CRITICAL_BANDWIDTH_KBPS = 500

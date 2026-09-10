package com.v2ray.ang.ui.premium.vpn

import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import java.net.HttpURLConnection
import java.net.URL
import javax.net.ssl.HttpsURLConnection
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext

internal data class VpnTunnelProbe(
    val ok: Boolean,
    val attempt: Int,
    val stage: String,
    val reason: String,
)

internal class VpnTunnelTrafficVerifier(context: Context) {
    private val connectivityManager =
        context.applicationContext.getSystemService(Context.CONNECTIVITY_SERVICE) as? ConnectivityManager

    suspend fun verify(onProbe: (VpnTunnelProbe) -> Unit = {}): VpnTunnelProbe = withContext(Dispatchers.IO) {
        val manager = connectivityManager
            ?: return@withContext VpnTunnelProbe(false, 0, "network", "connectivity_manager_unavailable")

        var last = VpnTunnelProbe(false, 0, "network", "vpn_network_not_found")
        for (attempt in 1..MAX_ATTEMPTS) {
            val vpnNetwork = findVpnNetwork(manager)
            if (vpnNetwork == null) {
                last = VpnTunnelProbe(false, attempt, "vpn_network", "vpn_network_not_found")
                onProbe(last)
                delay(RETRY_DELAY_MS)
                continue
            }

            val primary = probe(vpnNetwork, PRIMARY_PROBE_URL, attempt, "skryon_health")
            onProbe(primary)
            if (!primary.ok) {
                last = primary
                delay(RETRY_DELAY_MS)
                continue
            }

            val secondary = probe(vpnNetwork, SECONDARY_PROBE_URL, attempt, "neutral_https")
            onProbe(secondary)
            if (secondary.ok) {
                return@withContext VpnTunnelProbe(true, attempt, "traffic", "vpn_https_verified")
            }

            last = secondary
            delay(RETRY_DELAY_MS)
        }
        last
    }

    private fun findVpnNetwork(manager: ConnectivityManager): Network? {
        return manager.allNetworks.firstOrNull { network ->
            val capabilities = manager.getNetworkCapabilities(network) ?: return@firstOrNull false
            capabilities.hasTransport(NetworkCapabilities.TRANSPORT_VPN) &&
                capabilities.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
        }
    }

    private fun probe(network: Network, url: String, attempt: Int, stage: String): VpnTunnelProbe {
        val connection = try {
            network.openConnection(URL(url)) as? HttpsURLConnection
                ?: return VpnTunnelProbe(false, attempt, stage, "not_https_connection")
        } catch (error: CancellationException) {
            throw error
        } catch (error: Exception) {
            return VpnTunnelProbe(false, attempt, stage, "open_failed:${safeReason(error)}")
        }

        return try {
            connection.connectTimeout = CONNECT_TIMEOUT_MS
            connection.readTimeout = READ_TIMEOUT_MS
            connection.requestMethod = "GET"
            connection.instanceFollowRedirects = false
            connection.useCaches = false
            connection.setRequestProperty("Cache-Control", "no-cache")
            connection.setRequestProperty("User-Agent", "Skryon-VPN-Probe/1")

            val code = connection.responseCode
            if (code in 200..299) {
                connection.inputStream.use { input ->
                    val buffer = ByteArray(64)
                    input.read(buffer)
                }
                VpnTunnelProbe(true, attempt, stage, "http_$code")
            } else {
                VpnTunnelProbe(false, attempt, stage, "http_$code")
            }
        } catch (error: CancellationException) {
            throw error
        } catch (error: Exception) {
            VpnTunnelProbe(false, attempt, stage, "request_failed:${safeReason(error)}")
        } finally {
            connection.disconnect()
        }
    }

    private fun safeReason(error: Exception): String {
        return error.javaClass.simpleName
            .replace(Regex("[^A-Za-z0-9_-]"), "")
            .take(48)
            .ifBlank { "exception" }
    }

    private companion object {
        const val MAX_ATTEMPTS = 6
        const val RETRY_DELAY_MS = 700L
        const val CONNECT_TIMEOUT_MS = 2_000
        const val READ_TIMEOUT_MS = 2_000
        const val PRIMARY_PROBE_URL = "https://skryon.ru/health"
        const val SECONDARY_PROBE_URL = "https://one.one.one.one/cdn-cgi/trace"
    }
}

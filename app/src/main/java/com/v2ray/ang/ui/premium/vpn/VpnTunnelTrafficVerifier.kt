package com.v2ray.ang.ui.premium.vpn

import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import com.v2ray.ang.AppConfig
import com.v2ray.ang.handler.SettingsManager
import java.net.InetSocketAddress
import java.net.Proxy
import java.net.Socket
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
            if (findVpnNetwork(manager) == null) {
                last = VpnTunnelProbe(false, attempt, "vpn_network", "vpn_network_not_found")
                onProbe(last)
                delay(RETRY_DELAY_MS)
                continue
            }

            val socksFailure = checkLocalSocks(attempt)
            if (socksFailure != null) {
                last = socksFailure
                onProbe(last)
                delay(RETRY_DELAY_MS)
                continue
            }

            val primary = probeThroughLocalSocks(PRIMARY_PROBE_URL, attempt, "skryon_health_via_socks")
            onProbe(primary)
            if (primary.ok) {
                return@withContext VpnTunnelProbe(true, attempt, "traffic", "vpn_https_verified_via_socks")
            }

            val secondary = probeThroughLocalSocks(SECONDARY_PROBE_URL, attempt, "neutral_https_via_socks")
            onProbe(secondary)
            if (secondary.ok) {
                return@withContext VpnTunnelProbe(true, attempt, "traffic", "vpn_https_verified_via_socks_fallback")
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

    private fun checkLocalSocks(attempt: Int): VpnTunnelProbe? {
        return try {
            Socket().use { socket ->
                socket.connect(
                    InetSocketAddress(AppConfig.LOOPBACK, SettingsManager.getSocksPort()),
                    SOCKS_CONNECT_TIMEOUT_MS,
                )
            }
            null
        } catch (error: CancellationException) {
            throw error
        } catch (error: Exception) {
            VpnTunnelProbe(
                ok = false,
                attempt = attempt,
                stage = "local_socks",
                reason = "socks_unreachable:${safeReason(error)}",
            )
        }
    }

    private fun probeThroughLocalSocks(url: String, attempt: Int, stage: String): VpnTunnelProbe {
        val proxy = Proxy(
            Proxy.Type.SOCKS,
            InetSocketAddress(AppConfig.LOOPBACK, SettingsManager.getSocksPort()),
        )
        val connection = try {
            URL(url).openConnection(proxy) as? HttpsURLConnection
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
            connection.setRequestProperty("Connection", "close")
            connection.setRequestProperty("User-Agent", "Skryon-VPN-Probe/2")

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
        const val MAX_ATTEMPTS = 8
        const val RETRY_DELAY_MS = 750L
        const val SOCKS_CONNECT_TIMEOUT_MS = 1_000
        const val CONNECT_TIMEOUT_MS = 2_500
        const val READ_TIMEOUT_MS = 2_500
        const val PRIMARY_PROBE_URL = "https://skryon.ru/health"
        const val SECONDARY_PROBE_URL = "https://one.one.one.one/cdn-cgi/trace"
    }
}

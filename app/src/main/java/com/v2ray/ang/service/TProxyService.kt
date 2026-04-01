package com.v2ray.ang.service

import android.content.Context
import android.os.ParcelFileDescriptor
import android.util.Log
import com.v2ray.ang.AppConfig
import com.v2ray.ang.contracts.Tun2SocksControl
import com.v2ray.ang.handler.ManualDiagnosticCodes
import com.v2ray.ang.handler.ManualModeDebugLogger
import com.v2ray.ang.handler.ManualModeDiagnostics
import com.v2ray.ang.handler.MmkvManager
import com.v2ray.ang.handler.SettingsManager
import java.io.File

/**
 * Manages the tun2socks process that handles VPN traffic
 */
class TProxyService(
    private val context: Context,
    private val vpnInterface: ParcelFileDescriptor,
    private val isRunningProvider: () -> Boolean,
    private val restartCallback: () -> Unit
) : Tun2SocksControl {
    companion object {
        private const val NATIVE_LIB_NAME = "hev-socks5-tunnel"
        @JvmStatic
        @Suppress("FunctionName")
        private external fun TProxyStartService(configPath: String, fd: Int)
        @JvmStatic
        @Suppress("FunctionName")
        private external fun TProxyStopService()
        @JvmStatic
        @Suppress("FunctionName")
        private external fun TProxyGetStats(): LongArray?

        @Volatile
        private var nativeLoaded: Boolean? = null
        @Volatile
        private var nativeLoadError: String? = null

        private fun ensureNativeLoaded(): Boolean {
            nativeLoaded?.let { return it }
            return synchronized(this) {
                nativeLoaded?.let { return@synchronized it }
                try {
                    System.loadLibrary(NATIVE_LIB_NAME)
                    nativeLoaded = true
                    nativeLoadError = null
                    true
                } catch (e: UnsatisfiedLinkError) {
                    nativeLoaded = false
                    nativeLoadError = e.message ?: "Unknown UnsatisfiedLinkError"
                    false
                } catch (e: Throwable) {
                    nativeLoaded = false
                    nativeLoadError = e.message ?: "Unknown native load error"
                    false
                }
            }
        }

        private fun buildNativeDetails(extra: String = ""): String {
            val abi = android.os.Build.SUPPORTED_ABIS.joinToString(",")
            val pkg = AppConfig.ANG_PACKAGE
            val suffix = if (extra.isBlank()) "" else "; $extra"
            return "abi=$abi; package=$pkg; lib=$NATIVE_LIB_NAME; loadError=${nativeLoadError.orEmpty()}$suffix"
        }

        private fun unsatisfiedLinkCode(error: UnsatisfiedLinkError): String {
            val msg = error.message.orEmpty()
            return if (msg.contains("No implementation found", ignoreCase = true)) {
                ManualDiagnosticCodes.TUN_NATIVE_SYMBOL_MISSING
            } else {
                ManualDiagnosticCodes.TUN_NATIVE_LIB_MISSING
            }
        }
    }

    /**
     * Starts the tun2socks process with the appropriate parameters.
     */
    override fun startTun2Socks(): Boolean {
//        Log.i(AppConfig.TAG, "Starting HevSocks5Tunnel via JNI")
        if (!ensureNativeLoaded()) {
            val details = buildNativeDetails()
            ManualModeDiagnostics.reportError(
                code = ManualDiagnosticCodes.TUN_NATIVE_LIB_MISSING,
                message = "Missing native library libhev-socks5-tunnel.so",
                source = "TProxyService",
                details = details,
            )
            // #region agent log
            ManualModeDebugLogger.log(
                hypothesisId = "H1",
                location = "TProxyService.kt:startTun2Socks",
                message = "hev_native_load_failed",
                data = org.json.JSONObject().put("details", details),
            )
            // #endregion
            Log.e(AppConfig.TAG, "hev native library load failed: $details")
            return false
        }
        ManualModeDiagnostics.recordSuccessStep("HEV native library loaded")

        val configContent = buildConfig()
        val configFile = File(context.filesDir, "hev-socks5-tunnel.yaml").apply {
            writeText(configContent)
        }
//        Log.i(AppConfig.TAG, "Config file created: ${configFile.absolutePath}")
        Log.d(AppConfig.TAG, "HevSocks5Tunnel Config content:\n$configContent")

        try {
            val fd = try {
                vpnInterface.fd
            } catch (e: IllegalStateException) {
                ManualModeDiagnostics.reportError(
                    code = ManualDiagnosticCodes.VPN_FD_ALREADY_CLOSED,
                    message = "VPN interface fd is already closed",
                    source = "TProxyService",
                    details = "lib=$NATIVE_LIB_NAME; error=${e.message.orEmpty()}",
                )
                return false
            }
//            Log.i(AppConfig.TAG, "TProxyStartService...")
            TProxyStartService(configFile.absolutePath, fd)
            ManualModeDiagnostics.recordSuccessStep("HEV tun2socks started")
            return true
        } catch (e: UnsatisfiedLinkError) {
            val code = unsatisfiedLinkCode(e)
            val details = buildNativeDetails("jniError=${e.message.orEmpty()}")
            ManualModeDiagnostics.reportError(
                code = code,
                message = if (code == ManualDiagnosticCodes.TUN_NATIVE_SYMBOL_MISSING) {
                    "Missing JNI symbol in libhev-socks5-tunnel.so"
                } else {
                    "Missing native library libhev-socks5-tunnel.so"
                },
                source = "TProxyService",
                details = details,
            )
            Log.e(AppConfig.TAG, "HevSocks5Tunnel native start error: ${e.message}", e)
            return false
        } catch (e: Exception) {
            Log.e(AppConfig.TAG, "HevSocks5Tunnel exception: ${e.message}")
            ManualModeDiagnostics.reportError(
                code = ManualDiagnosticCodes.VPN_SERVICE_START_FAILED,
                message = "Failed to start hev-socks5-tunnel",
                source = "TProxyService",
                details = e.message.orEmpty(),
            )
            return false
        }
    }

    private fun buildConfig(): String {
        val socksPort = SettingsManager.getSocksPort()
        val vpnConfig = SettingsManager.getCurrentVpnInterfaceAddressConfig()
        return buildString {
            appendLine("tunnel:")
            appendLine("  mtu: ${SettingsManager.getVpnMtu()}")
            appendLine("  ipv4: ${vpnConfig.ipv4Client}")

            if (MmkvManager.decodeSettingsBool(AppConfig.PREF_PREFER_IPV6)) {
                appendLine("  ipv6: '${vpnConfig.ipv6Client}'")
            }

            appendLine("socks5:")
            appendLine("  port: ${socksPort}")
            appendLine("  address: ${AppConfig.LOOPBACK}")
            appendLine("  udp: 'udp'")

            // Read-write timeout settings
            val timeoutSetting = MmkvManager.decodeSettingsString(AppConfig.PREF_HEV_TUNNEL_RW_TIMEOUT) ?: AppConfig.HEVTUN_RW_TIMEOUT
            val parts = timeoutSetting.split(",")
                .map { it.trim() }
                .filter { it.isNotEmpty() }
            val tcpTimeout = parts.getOrNull(0)?.toIntOrNull() ?: 300
            val udpTimeout = parts.getOrNull(1)?.toIntOrNull() ?: 60

            appendLine("misc:")
            appendLine("  tcp-read-write-timeout: ${tcpTimeout * 1000}")
            appendLine("  udp-read-write-timeout: ${udpTimeout * 1000}")
            appendLine("  log-level: ${MmkvManager.decodeSettingsString(AppConfig.PREF_HEV_TUNNEL_LOGLEVEL) ?: "warn"}")
        }
    }

    /**
     * Stops the tun2socks process
     */
    override fun stopTun2Socks() {
        if (!ensureNativeLoaded()) {
            Log.w(AppConfig.TAG, "Skip TProxyStopService: native library not loaded")
            return
        }
        try {
            Log.i(AppConfig.TAG, "TProxyStopService...")
            TProxyStopService()
        } catch (e: UnsatisfiedLinkError) {
            val code = unsatisfiedLinkCode(e)
            ManualModeDiagnostics.reportError(
                code = code,
                message = if (code == ManualDiagnosticCodes.TUN_NATIVE_SYMBOL_MISSING) {
                    "Missing JNI symbol in libhev-socks5-tunnel.so"
                } else {
                    "Missing native library libhev-socks5-tunnel.so"
                },
                source = "TProxyService",
                details = buildNativeDetails("jniError=${e.message.orEmpty()}"),
            )
            Log.e(AppConfig.TAG, "Failed to stop hev-socks5-tunnel: ${e.message}", e)
        } catch (e: Exception) {
            Log.e(AppConfig.TAG, "Failed to stop hev-socks5-tunnel", e)
        }
    }
}

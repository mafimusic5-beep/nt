package com.v2ray.ang

import android.app.Application
import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.net.NetworkRequest
import androidx.work.Configuration
import androidx.work.WorkManager
import com.tencent.mmkv.MMKV
import com.v2ray.ang.AppConfig.ANG_PACKAGE
import com.v2ray.ang.handler.SettingsManager
import com.v2ray.ang.handler.SkryonVpnAutoConnectScheduler
import com.v2ray.ang.ui.premium.vpn.VpnServiceDiagnosticsObserver
import com.v2ray.ang.ui.premium.vpn.VpnUiDebugLogger

class AngApplication : Application() {
    companion object {
        lateinit var application: AngApplication
    }

    /**
     * Attaches the base context to the application.
     * @param base The base context.
     */
    override fun attachBaseContext(base: Context?) {
        super.attachBaseContext(base)
        application = this
    }

    private val workManagerConfiguration: Configuration = Configuration.Builder()
        .setDefaultProcessName("${ANG_PACKAGE}:bg")
        .build()

    /**
     * Initializes the application.
     */
    override fun onCreate() {
        super.onCreate()

        MMKV.initialize(this)

        // Initialize WorkManager with the custom configuration
        WorkManager.initialize(this, workManagerConfiguration)

        // Ensure critical preference defaults are present in MMKV early
        SettingsManager.initApp(this)
        SettingsManager.setNightMode()

        VpnUiDebugLogger.initialize(this)
        VpnServiceDiagnosticsObserver.start(this)

        // WorkManager survives process death and waits for connectivity.
        // This callback makes Wi-Fi/LTE recovery prompt while the app's
        // process is alive, including TV resume without reopening the UI.
        SkryonVpnAutoConnectScheduler.ensureScheduled(this)
        runCatching {
            val connectivity = getSystemService(CONNECTIVITY_SERVICE) as ConnectivityManager
            val callback = object : ConnectivityManager.NetworkCallback() {
                override fun onAvailable(network: Network) {
                    SkryonVpnAutoConnectScheduler.requestOnce(this@AngApplication)
                }

                override fun onCapabilitiesChanged(network: Network, capabilities: NetworkCapabilities) {
                    if (capabilities.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)) {
                        SkryonVpnAutoConnectScheduler.requestOnce(this@AngApplication)
                    }
                }
            }
            connectivity.registerNetworkCallback(
                NetworkRequest.Builder()
                    .addCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
                    .addCapability(NetworkCapabilities.NET_CAPABILITY_NOT_VPN)
                    .build(),
                callback,
            )
        }

        es.dmoral.toasty.Toasty.Config.getInstance()
            .setGravity(android.view.Gravity.BOTTOM, 0, 300)
            .apply()
    }
}

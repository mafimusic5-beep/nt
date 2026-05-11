package com.v2ray.ang.ui.premium

import android.graphics.Color as AndroidColor
import android.net.VpnService
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.view.WindowCompat
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import com.v2ray.ang.handler.V2RayServiceManager
import com.v2ray.ang.ui.premium.vpn.VpnMainRoute
import com.v2ray.ang.ui.premium.vpn.VpnMainViewModel
import com.v2ray.ang.ui.premium.vpn.VpnUiDebugLogger
import kotlinx.coroutines.delay
import org.json.JSONObject

private enum class EmeryRoute { Splash, Home }

class PremiumActivity : ComponentActivity() {

    private var onVpnPermissionGranted: (() -> Unit)? = null

    private val vpnPermissionLauncher = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult()
    ) { result ->
        if (result.resultCode == RESULT_OK) {
            onVpnPermissionGranted?.invoke()
            onVpnPermissionGranted = null
        } else {
            onVpnPermissionGranted = null
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val systemBarsColor = AndroidColor.rgb(247, 248, 244)
        WindowCompat.setDecorFitsSystemWindows(window, true)
        window.statusBarColor = systemBarsColor
        window.navigationBarColor = systemBarsColor
        WindowCompat.getInsetsController(window, window.decorView).apply {
            isAppearanceLightStatusBars = true
            isAppearanceLightNavigationBars = true
        }
        setContent {
            EmeryTheme {
                EmeryApp(
                    requestVpnPermission = { onGranted ->
                        val intent = VpnService.prepare(this)
                        if (intent == null) {
                            onGranted()
                        } else {
                            onVpnPermissionGranted = onGranted
                            vpnPermissionLauncher.launch(intent)
                        }
                    },
                    startVpnService = { guid ->
                        V2RayServiceManager.startVService(this, guid)
                    },
                    stopVpnService = {
                        V2RayServiceManager.stopVService(this)
                    },
                )
            }
        }
    }
}

@Composable
private fun EmeryApp(
    requestVpnPermission: ((onGranted: () -> Unit) -> Unit),
    startVpnService: (String) -> Boolean,
    stopVpnService: () -> Unit,
) {
    val navController = rememberNavController()

    Scaffold(
        containerColor = MaterialTheme.colorScheme.background,
    ) { padding ->
        NavHost(
            navController = navController,
            startDestination = EmeryRoute.Splash.name,
            modifier = Modifier.padding(padding),
        ) {
            composable(EmeryRoute.Splash.name) {
                SplashScreen {
                    navController.navigate(EmeryRoute.Home.name) {
                        popUpTo(EmeryRoute.Splash.name) { inclusive = true }
                    }
                }
            }
            composable(EmeryRoute.Home.name) {
                val vpnMainViewModel: VpnMainViewModel = viewModel()
                LaunchedEffect(Unit) {
                    VpnUiDebugLogger.log(
                        hypothesisId = "H2",
                        location = "PremiumActivity.kt:EmeryApp",
                        message = "home route switched to vpn compose screen",
                        data = JSONObject(),
                    )
                }
                VpnMainRoute(
                    viewModel = vpnMainViewModel,
                    requestVpnPermission = requestVpnPermission,
                    startVpnService = startVpnService,
                    stopVpnService = stopVpnService,
                )
            }
        }
    }
}

@Composable
private fun SplashScreen(onFinish: () -> Unit) {
    LaunchedEffect(Unit) {
        delay(1000)
        onFinish()
    }
    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(MaterialTheme.colorScheme.background),
        contentAlignment = Alignment.Center
    ) {
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            Text(
                text = "EMERY",
                style = MaterialTheme.typography.headlineLarge,
                fontWeight = FontWeight.Bold,
                color = MaterialTheme.colorScheme.primary,
                letterSpacing = 4.sp
            )
            Spacer(modifier = Modifier.height(8.dp))
            CircularProgressIndicator(
                modifier = Modifier.size(24.dp),
                strokeWidth = 2.dp,
                color = MaterialTheme.colorScheme.primary
            )
        }
    }
}

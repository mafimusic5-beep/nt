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
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.view.WindowCompat
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import com.v2ray.ang.handler.MmkvManager
import com.v2ray.ang.handler.V2RayServiceManager
import com.v2ray.ang.ui.premium.vpn.VpnMainRoute
import com.v2ray.ang.ui.premium.vpn.VpnMainViewModel
import com.v2ray.ang.ui.premium.vpn.VpnUiDebugLogger
import kotlinx.coroutines.delay
import org.json.JSONObject

private const val SKRYON_ACTIVATION_CODE_PREF = "SKRYON_ACTIVATION_CODE"

private enum class EmeryRoute { Splash, Activation, Home }

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

        val systemBarsColor = AndroidColor.rgb(255, 255, 255)
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
    val context = LocalContext.current

    Scaffold(containerColor = Color.White) { padding ->
        NavHost(
            navController = navController,
            startDestination = EmeryRoute.Splash.name,
            modifier = Modifier.padding(padding),
        ) {
            composable(EmeryRoute.Splash.name) {
                SplashScreen {
                    val nextRoute = if (savedActivationCode().isBlank()) {
                        EmeryRoute.Activation.name
                    } else {
                        EmeryRoute.Home.name
                    }
                    navController.navigate(nextRoute) {
                        popUpTo(EmeryRoute.Splash.name) { inclusive = true }
                    }
                }
            }
            composable(EmeryRoute.Activation.name) {
                SkryonActivationScreen(
                    onActivated = { code ->
                        val formattedCode = formatSkryonActivationCode(code)
                        val result = activateSkryonCode(context, code, formattedCode)
                        if (result.ok) {
                            val guid = saveActivatedSkryonConfig(result.config)
                            MmkvManager.encodeSettings(
                                SKRYON_ACTIVATION_CODE_PREF,
                                result.code.ifBlank { formattedCode },
                            )
                            MmkvManager.encodeSettings(SKRYON_ACTIVATION_CONFIG_PREF, result.config)
                            MmkvManager.encodeSettings(SKRYON_SERVER_GUID_PREF, guid)
                            navController.navigate(EmeryRoute.Home.name) {
                                popUpTo(EmeryRoute.Activation.name) { inclusive = true }
                            }
                        }
                        result
                    },
                )
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

private fun savedActivationCode(): String {
    return MmkvManager.decodeSettingsString(SKRYON_ACTIVATION_CODE_PREF, "")
        ?.trim()
        .orEmpty()
}

@Composable
private fun SplashScreen(onFinish: () -> Unit) {
    LaunchedEffect(Unit) {
        delay(850)
        onFinish()
    }
    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(Color.White),
        contentAlignment = Alignment.Center,
    ) {
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            Text(
                text = "Skryon",
                style = MaterialTheme.typography.headlineLarge,
                fontWeight = FontWeight.Bold,
                color = Color(0xFF111319),
                letterSpacing = 1.2.sp,
            )
            Spacer(modifier = Modifier.height(8.dp))
            CircularProgressIndicator(
                modifier = Modifier.size(24.dp),
                strokeWidth = 2.dp,
                color = Color(0xFF111319),
            )
        }
    }
}

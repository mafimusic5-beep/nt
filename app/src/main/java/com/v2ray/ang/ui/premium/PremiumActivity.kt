package com.v2ray.ang.ui.premium

import android.content.Intent
import android.net.VpnService
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.Crossfade
import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.EaseInOutSine
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.keyframes
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.slideInVertically
import androidx.compose.animation.togetherWith
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Devices
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.filled.PowerSettingsNew
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.SupportAgent
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.NavigationBarItemDefaults
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.view.WindowCompat
import androidx.navigation.NavDestination.Companion.hierarchy
import androidx.navigation.NavGraph.Companion.findStartDestination
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import com.v2ray.ang.AppConfig
import com.v2ray.ang.handler.EmeryAccessManager
import com.v2ray.ang.handler.EmeryAccessProfile
import com.v2ray.ang.handler.EmeryVpnSync
import com.v2ray.ang.handler.MmkvManager
import com.v2ray.ang.handler.V2RayServiceManager
import com.v2ray.ang.network.EmeryAuthClient
import com.v2ray.ang.ui.MainActivity
import com.v2ray.ang.util.Utils
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import org.json.JSONObject

// ──────────────────────────────────────────────
// Navigation & State
// ──────────────────────────────────────────────

private enum class EmeryRoute { Splash, Home, Devices, Support, Settings }

private enum class VpnDisplayState {
    ACTIVATION_REQUIRED, DISCONNECTED, CONNECTING, CONNECTED,
}

private fun vpnStateLabel(state: VpnDisplayState): String = when (state) {
    VpnDisplayState.ACTIVATION_REQUIRED -> "Требуется активация"
    VpnDisplayState.DISCONNECTED -> "VPN отключён"
    VpnDisplayState.CONNECTING -> "Подключение…"
    VpnDisplayState.CONNECTED -> "VPN активен"
}

private fun vpnStateSubtext(state: VpnDisplayState): String = when (state) {
    VpnDisplayState.ACTIVATION_REQUIRED -> "Введите ключ доступа"
    VpnDisplayState.DISCONNECTED -> "Трафик не защищён"
    VpnDisplayState.CONNECTING -> "Ожидание подключения"
    VpnDisplayState.CONNECTED -> "Трафик защищён"
}

private fun vpnStateDotColor(state: VpnDisplayState): Color = when (state) {
    VpnDisplayState.CONNECTED -> EmeryColors.Success
    VpnDisplayState.CONNECTING -> EmeryDarkScheme.primary
    VpnDisplayState.DISCONNECTED -> EmeryColors.TextMuted
    VpnDisplayState.ACTIVATION_REQUIRED -> EmeryColors.Warning
}

private fun mapActivationError(message: String?): String = when (message) {
    "bad_request" -> "Введите корректный ключ"
    "invalid_or_expired_key" -> "Неверный или истёкший ключ"
    "device_limit_reached" -> "Превышен лимит устройств"
    "already_used", "code_already_used" -> "Ключ уже использован"
    "network" -> "Ошибка сети"
    else -> "Произошла ошибка"
}

// ──────────────────────────────────────────────
// Activity
// ──────────────────────────────────────────────

class PremiumActivity : ComponentActivity() {

    private var onVpnPermissionGranted: (() -> Unit)? = null

    private val vpnPermissionLauncher = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult()
    ) { result ->
        if (result.resultCode == RESULT_OK) {
            onVpnPermissionGranted?.invoke()
            onVpnPermissionGranted = null
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        WindowCompat.getInsetsController(window, window.decorView).apply {
            isAppearanceLightStatusBars = false
            isAppearanceLightNavigationBars = false
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
                    openClassicServers = {
                        startActivity(Intent(this, MainActivity::class.java))
                    },
                )
            }
        }
    }
}

// ──────────────────────────────────────────────
// App Shell
// ──────────────────────────────────────────────

@Composable
private fun EmeryApp(
    requestVpnPermission: ((onGranted: () -> Unit) -> Unit),
    openClassicServers: () -> Unit,
) {
    val navController = rememberNavController()
    val navItems = remember {
        listOf(
            Triple(EmeryRoute.Home, Icons.Default.Home, "Главная"),
            Triple(EmeryRoute.Devices, Icons.Default.Devices, "Устройства"),
            Triple(EmeryRoute.Support, Icons.Default.SupportAgent, "Помощь"),
            Triple(EmeryRoute.Settings, Icons.Default.Settings, "Настройки"),
        )
    }

    Scaffold(
        containerColor = MaterialTheme.colorScheme.background,
        bottomBar = {
            val backStackEntry by navController.currentBackStackEntryAsState()
            val currentDest = backStackEntry?.destination
            val showBar = navItems.any { (route, _, _) ->
                currentDest?.hierarchy?.any { it.route == route.name } == true
            }
            AnimatedVisibility(showBar) {
                NavigationBar(
                    containerColor = MaterialTheme.colorScheme.surface,
                    tonalElevation = 0.dp,
                ) {
                    navItems.forEach { (route, icon, label) ->
                        val selected = currentDest?.hierarchy?.any { it.route == route.name } == true
                        NavigationBarItem(
                            selected = selected,
                            onClick = {
                                navController.navigate(route.name) {
                                    popUpTo(navController.graph.findStartDestination().id) { saveState = true }
                                    restoreState = true
                                    launchSingleTop = true
                                }
                            },
                            icon = { Icon(icon, contentDescription = label) },
                            label = { Text(label, style = MaterialTheme.typography.labelSmall) },
                            colors = NavigationBarItemDefaults.colors(
                                selectedIconColor = MaterialTheme.colorScheme.primary,
                                selectedTextColor = MaterialTheme.colorScheme.primary,
                                indicatorColor = MaterialTheme.colorScheme.primaryContainer,
                                unselectedIconColor = MaterialTheme.colorScheme.onSurfaceVariant,
                                unselectedTextColor = MaterialTheme.colorScheme.onSurfaceVariant,
                            ),
                        )
                    }
                }
            }
        },
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
                HomeScreen(requestVpnPermission = requestVpnPermission)
            }
            composable(EmeryRoute.Devices.name) { DevicesScreen() }
            composable(EmeryRoute.Support.name) { SupportScreen() }
            composable(EmeryRoute.Settings.name) {
                SettingsScreen(openClassicServers = openClassicServers)
            }
        }
    }
}

// ──────────────────────────────────────────────
// Splash
// ──────────────────────────────────────────────

@Composable
private fun SplashScreen(onReady: () -> Unit) {
    LaunchedEffect(Unit) {
        delay(800)
        onReady()
    }
    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(MaterialTheme.colorScheme.background),
        contentAlignment = Alignment.Center,
    ) {
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            Text(
                text = "EVPN",
                style = MaterialTheme.typography.displaySmall,
                color = EmeryColors.Brand,
                fontWeight = FontWeight.Bold,
                letterSpacing = 3.sp,
            )
            Spacer(modifier = Modifier.height(8.dp))
            Text(
                text = "Прогрев",
                style = MaterialTheme.typography.titleMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

// ──────────────────────────────────────────────
// Home Screen
// ──────────────────────────────────────────────

@Composable
private fun HomeScreen(
    requestVpnPermission: ((onGranted: () -> Unit) -> Unit),
) {
    val context = LocalContext.current
    var profile by remember { mutableStateOf(EmeryAccessManager.loadProfile()) }
    var isRunning by remember { mutableStateOf(V2RayServiceManager.isRunning()) }
    var isConnecting by remember { mutableStateOf(false) }

    LaunchedEffect(Unit) {
        while (true) {
            val running = V2RayServiceManager.isRunning()
            if (running != isRunning) {
                isConnecting = false
                isRunning = running
            }
            profile = EmeryAccessManager.loadProfile()
            delay(800)
        }
    }

    LaunchedEffect(isConnecting) {
        if (isConnecting) {
            delay(15_000)
            isConnecting = false
        }
    }

    val isActivated = profile != null
    val vpnState = when {
        !isActivated -> VpnDisplayState.ACTIVATION_REQUIRED
        isConnecting -> VpnDisplayState.CONNECTING
        isRunning -> VpnDisplayState.CONNECTED
        else -> VpnDisplayState.DISCONNECTED
    }

    val dotColor by animateColorAsState(
        targetValue = vpnStateDotColor(vpnState),
        animationSpec = tween(600),
        label = "dot",
    )

    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 20.dp, vertical = 16.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        Text(
            text = "EVPN",
            style = MaterialTheme.typography.titleLarge,
            color = EmeryColors.Brand,
            fontWeight = FontWeight.Bold,
            letterSpacing = 2.sp,
        )

        EmeryCard(modifier = Modifier.fillMaxWidth()) {
            Column(
                modifier = Modifier.padding(24.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
            ) {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Box(
                        modifier = Modifier
                            .size(8.dp)
                            .clip(CircleShape)
                            .background(dotColor),
                    )
                    Spacer(Modifier.width(10.dp))
                    Crossfade(
                        targetState = vpnState,
                        animationSpec = tween(400),
                        label = "status_label",
                    ) { state ->
                        Text(
                            text = vpnStateLabel(state),
                            style = MaterialTheme.typography.titleMedium,
                            fontWeight = FontWeight.SemiBold,
                            color = MaterialTheme.colorScheme.onSurface,
                        )
                    }
                }

                Spacer(Modifier.height(4.dp))

                Crossfade(
                    targetState = vpnState,
                    animationSpec = tween(400),
                    label = "status_sub",
                ) { state ->
                    Text(
                        text = vpnStateSubtext(state),
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.fillMaxWidth(),
                    )
                }

                Spacer(Modifier.height(32.dp))

                ConnectButton(
                    state = vpnState,
                    enabled = isActivated && profile?.vpnEnabled != false,
                    onClick = {
                        if (isRunning) {
                            V2RayServiceManager.stopVService(context)
                            // #region agent log
                            PremiumDebugLogger.log(
                                context = context,
                                hypothesisId = "H3",
                                location = "PremiumActivity.kt:HomeScreen",
                                message = "vpn stop pressed",
                                data = JSONObject(),
                            )
                            // #endregion
                        } else {
                            isConnecting = true
                            val startAction = {
                                V2RayServiceManager.startVServiceFromToggle(context)
                                // #region agent log
                                PremiumDebugLogger.log(
                                    context = context,
                                    hypothesisId = "H2",
                                    location = "PremiumActivity.kt:HomeScreen",
                                    message = "vpn start pressed",
                                    data = JSONObject().put(
                                        "serverSelected",
                                        !MmkvManager.getSelectServer().isNullOrEmpty()
                                    ),
                                )
                                // #endregion
                            }
                            requestVpnPermission(startAction)
                        }
                    },
                )

                Spacer(Modifier.height(16.dp))

                Text(
                    text = "Москва",
                    style = MaterialTheme.typography.bodyMedium,
                    color = EmeryColors.TextMuted,
                )
            }
        }

        @Suppress("DEPRECATION")
        AnimatedContent(
            targetState = isActivated,
            transitionSpec = {
                (fadeIn(tween(500)) + slideInVertically(
                    initialOffsetY = { it / 8 },
                    animationSpec = tween(500),
                )) togetherWith fadeOut(tween(300))
            },
            label = "home_block",
        ) { activated ->
            if (activated) {
                profile?.let { SubscriptionInfoBlock(profile = it) }
            } else {
                ActivationBlock(
                    onActivated = { profile = EmeryAccessManager.loadProfile() },
                )
            }
        }
    }
}

// ──────────────────────────────────────────────
// Connect Button
// ──────────────────────────────────────────────

@Composable
private fun ConnectButton(
    state: VpnDisplayState,
    enabled: Boolean,
    onClick: () -> Unit,
) {
    val isConnecting = state == VpnDisplayState.CONNECTING
    val isConnected = state == VpnDisplayState.CONNECTED
    val showRing = isConnecting || isConnected

    val infiniteTransition = rememberInfiniteTransition(label = "ring")
    val fastBreath by infiniteTransition.animateFloat(
        initialValue = 0f,
        targetValue = 1f,
        animationSpec = infiniteRepeatable(
            animation = tween(2200, easing = EaseInOutSine),
            repeatMode = RepeatMode.Reverse,
        ),
        label = "fast",
    )
    val slowBreath by infiniteTransition.animateFloat(
        initialValue = 0f,
        targetValue = 1f,
        animationSpec = infiniteRepeatable(
            animation = tween(5000, easing = EaseInOutSine),
            repeatMode = RepeatMode.Reverse,
        ),
        label = "slow",
    )

    val breath = when {
        isConnecting -> fastBreath
        isConnected -> slowBreath
        else -> 0f
    }
    val ringAlpha = when {
        isConnecting -> 0.10f + breath * 0.25f
        isConnected -> 0.06f + breath * 0.10f
        else -> 0f
    }
    val ringScale = when {
        isConnecting -> 1f + breath * 0.14f
        isConnected -> 1.05f + breath * 0.02f
        else -> 1f
    }
    val ringColor = if (isConnected) EmeryColors.ConnectedGlow else EmeryColors.ConnectingGlow

    val buttonBg by animateColorAsState(
        targetValue = when {
            !enabled -> EmeryColors.ConnectIdle.copy(alpha = 0.5f)
            isConnected -> EmeryColors.ConnectActive
            isConnecting -> MaterialTheme.colorScheme.primary
            else -> EmeryColors.ConnectIdle
        },
        animationSpec = tween(600),
        label = "btn_bg",
    )
    val iconTint by animateColorAsState(
        targetValue = when {
            !enabled -> EmeryColors.TextMuted
            isConnected -> EmeryColors.OnSuccess
            isConnecting -> MaterialTheme.colorScheme.onPrimary
            else -> MaterialTheme.colorScheme.onSurfaceVariant
        },
        animationSpec = tween(600),
        label = "icon_tint",
    )

    val buttonSize = 132.dp

    Box(
        modifier = Modifier.size(buttonSize + 40.dp),
        contentAlignment = Alignment.Center,
    ) {
        if (showRing) {
            Canvas(
                modifier = Modifier
                    .size(buttonSize + 16.dp)
                    .graphicsLayer(
                        scaleX = ringScale,
                        scaleY = ringScale,
                        alpha = ringAlpha,
                    ),
            ) {
                drawCircle(
                    color = ringColor,
                    style = Stroke(width = 2.dp.toPx()),
                )
            }
        }

        Box(
            modifier = Modifier
                .size(buttonSize)
                .clip(CircleShape)
                .background(buttonBg)
                .then(if (enabled) Modifier.clickable(onClick = onClick) else Modifier)
                .then(
                    if (!showRing && enabled) {
                        Modifier.border(
                            1.dp,
                            MaterialTheme.colorScheme.outline.copy(alpha = 0.4f),
                            CircleShape,
                        )
                    } else {
                        Modifier
                    }
                ),
            contentAlignment = Alignment.Center,
        ) {
            if (isConnecting) {
                CircularProgressIndicator(
                    modifier = Modifier.size(36.dp),
                    color = iconTint,
                    strokeWidth = 2.5.dp,
                )
            } else {
                Icon(
                    imageVector = Icons.Default.PowerSettingsNew,
                    contentDescription = if (isConnected) "Отключить" else "Подключить",
                    modifier = Modifier.size(40.dp),
                    tint = iconTint,
                )
            }
        }
    }
}

// ──────────────────────────────────────────────
// Activation Block
// ──────────────────────────────────────────────

@Composable
private fun ActivationBlock(onActivated: () -> Unit) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var key by remember { mutableStateOf("") }
    var loading by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    var shakeKey by remember { mutableIntStateOf(0) }
    val shakeOffset = remember { Animatable(0f) }

    LaunchedEffect(shakeKey) {
        if (shakeKey > 0) {
            shakeOffset.snapTo(0f)
            shakeOffset.animateTo(
                targetValue = 0f,
                animationSpec = keyframes {
                    durationMillis = 400
                    8f at 50
                    (-8f) at 100
                    6f at 150
                    (-6f) at 200
                    3f at 250
                    (-3f) at 300
                },
            )
        }
    }

    EmeryCard(
        modifier = Modifier
            .fillMaxWidth()
            .offset(x = shakeOffset.value.dp),
    ) {
        Column(modifier = Modifier.padding(20.dp)) {
            Text(
                text = "Активация доступа",
                style = MaterialTheme.typography.titleMedium,
                fontWeight = FontWeight.SemiBold,
            )
            Spacer(Modifier.height(14.dp))
            OutlinedTextField(
                value = key,
                onValueChange = { key = it; error = null },
                modifier = Modifier.fillMaxWidth(),
                shape = RoundedCornerShape(14.dp),
                label = { Text("Ключ доступа") },
                singleLine = true,
                colors = OutlinedTextFieldDefaults.colors(
                    focusedBorderColor = MaterialTheme.colorScheme.primary,
                    unfocusedBorderColor = MaterialTheme.colorScheme.outline,
                    focusedLabelColor = MaterialTheme.colorScheme.primary,
                    unfocusedLabelColor = MaterialTheme.colorScheme.onSurfaceVariant,
                    cursorColor = MaterialTheme.colorScheme.primary,
                ),
            )
            Spacer(Modifier.height(14.dp))
            Button(
                onClick = {
                    if (loading) return@Button
                    if (key.isBlank()) {
                        error = "Введите ключ доступа"
                        shakeKey++
                        return@Button
                    }
                    loading = true
                    error = null
                    // #region agent log
                    PremiumDebugLogger.log(
                        context = context,
                        hypothesisId = "H5",
                        location = "PremiumActivity.kt:ActivationBlock",
                        message = "activation started",
                        data = JSONObject().put("keyLength", key.length),
                    )
                    // #endregion
                    scope.launch {
                        val result = EmeryAuthClient.verifyAccessKey(key)
                        result.fold(
                            onSuccess = { p ->
                                EmeryAccessManager.saveProfile(p)
                                EmeryVpnSync.syncProfileAndVpnConfig(p.accessKey).fold(
                                    onSuccess = {
                                        // #region agent log
                                        PremiumDebugLogger.log(
                                            context = context,
                                            hypothesisId = "H5",
                                            location = "PremiumActivity.kt:ActivationBlock",
                                            message = "activation success",
                                            data = JSONObject().put("vpnEnabled", p.vpnEnabled),
                                        )
                                        // #endregion
                                        loading = false
                                        onActivated()
                                    },
                                    onFailure = {
                                        loading = false
                                        error = "Ошибка синхронизации"
                                        shakeKey++
                                    },
                                )
                            },
                            onFailure = { e ->
                                loading = false
                                error = mapActivationError(e.message)
                                shakeKey++
                            },
                        )
                    }
                },
                modifier = Modifier
                    .fillMaxWidth()
                    .height(48.dp),
                shape = RoundedCornerShape(14.dp),
                colors = ButtonDefaults.buttonColors(
                    containerColor = MaterialTheme.colorScheme.primary,
                    contentColor = MaterialTheme.colorScheme.onPrimary,
                ),
            ) {
                if (loading) {
                    CircularProgressIndicator(
                        modifier = Modifier.size(18.dp),
                        color = MaterialTheme.colorScheme.onPrimary,
                        strokeWidth = 2.dp,
                    )
                } else {
                    Text("Активировать доступ")
                }
            }
            AnimatedVisibility(error != null) {
                Column {
                    Spacer(Modifier.height(10.dp))
                    Text(
                        text = error.orEmpty(),
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.error,
                    )
                }
            }
        }
    }
}

// ──────────────────────────────────────────────
// Subscription Info Block
// ──────────────────────────────────────────────

@Composable
private fun SubscriptionInfoBlock(profile: EmeryAccessProfile) {
    val devices = MmkvManager.decodeSettingsInt(AppConfig.PREF_EMERY_DEVICES_USED, 0).coerceIn(0, 5)
    val bootEnabled = MmkvManager.decodeStartOnBoot()
    val devicesColor = if (devices >= 5) EmeryColors.Warning else MaterialTheme.colorScheme.onSurface

    EmeryCard(modifier = Modifier.fillMaxWidth()) {
        Column(
            modifier = Modifier.padding(20.dp),
            verticalArrangement = Arrangement.spacedBy(2.dp),
        ) {
            InfoRow("Тариф", profile.planName.ifBlank { "Прогрев" })
            InfoRow("Активно до", profile.expiresAt)
            InfoRow("Устройств", "$devices из 5", valueColor = devicesColor)
            InfoRow("Регион", "Москва")
            InfoRow("Автоподключение", if (bootEnabled) "включено" else "выключено")
            if (!profile.vpnEnabled) {
                Spacer(Modifier.height(8.dp))
                Text(
                    text = "Подписка неактивна",
                    style = MaterialTheme.typography.bodySmall,
                    color = EmeryColors.Warning,
                )
            }
        }
    }
}

// ──────────────────────────────────────────────
// Devices Screen
// ──────────────────────────────────────────────

@Composable
private fun DevicesScreen() {
    val deviceId = Utils.getDeviceIdForXUDPBaseKey()
    val used = MmkvManager.decodeSettingsInt(AppConfig.PREF_EMERY_DEVICES_USED, 0).coerceIn(0, 5)
    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 20.dp, vertical = 16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text(
            text = "Устройства",
            style = MaterialTheme.typography.headlineSmall,
            fontWeight = FontWeight.SemiBold,
        )
        EmeryCard(modifier = Modifier.fillMaxWidth()) {
            Column(
                modifier = Modifier.padding(20.dp),
                verticalArrangement = Arrangement.spacedBy(4.dp),
            ) {
                InfoRow("Использовано", "$used из 5")
                Box(
                    modifier = Modifier
                        .fillMaxWidth()
                        .height(1.dp)
                        .background(MaterialTheme.colorScheme.outlineVariant),
                )
                InfoRow("Текущее устройство", deviceId)
            }
        }
    }
}

// ──────────────────────────────────────────────
// Support Screen
// ──────────────────────────────────────────────

@Composable
private fun SupportScreen() {
    val context = LocalContext.current
    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 20.dp, vertical = 16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text(
            text = "Помощь",
            style = MaterialTheme.typography.headlineSmall,
            fontWeight = FontWeight.SemiBold,
        )
        EmeryCard(modifier = Modifier.fillMaxWidth()) {
            Column(modifier = Modifier.padding(20.dp)) {
                Text(
                    text = "Поддержка",
                    style = MaterialTheme.typography.titleMedium,
                    fontWeight = FontWeight.Medium,
                )
                Spacer(Modifier.height(6.dp))
                Text(
                    text = "Связь с командой и канал проекта в Telegram",
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Spacer(Modifier.height(16.dp))
                OutlinedButton(
                    onClick = { Utils.openUri(context, "https://t.me") },
                    shape = RoundedCornerShape(14.dp),
                    border = BorderStroke(1.dp, MaterialTheme.colorScheme.outline),
                ) {
                    Text("Открыть Telegram")
                }
            }
        }
    }
}

// ──────────────────────────────────────────────
// Settings Screen
// ──────────────────────────────────────────────

@Composable
private fun SettingsScreen(openClassicServers: () -> Unit) {
    var autoStart by remember {
        mutableStateOf(MmkvManager.decodeSettingsBool(AppConfig.PREF_AUTO_START_VPN, true))
    }
    var autoReconnect by remember {
        mutableStateOf(MmkvManager.decodeSettingsBool(AppConfig.PREF_AUTO_RECONNECT, true))
    }
    var bootStart by remember {
        mutableStateOf(MmkvManager.decodeStartOnBoot())
    }
    val isRunning = V2RayServiceManager.isRunning()
    val selectedServer = MmkvManager.getSelectServer()

    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 20.dp, vertical = 16.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        Text(
            text = "Настройки",
            style = MaterialTheme.typography.headlineSmall,
            fontWeight = FontWeight.SemiBold,
        )
        SettingsToggle("Автозапуск VPN", autoStart) {
            autoStart = it
            MmkvManager.encodeSettings(AppConfig.PREF_AUTO_START_VPN, it)
        }
        SettingsToggle("Автопереподключение", autoReconnect) {
            autoReconnect = it
            MmkvManager.encodeSettings(AppConfig.PREF_AUTO_RECONNECT, it)
        }
        SettingsToggle("Запуск после перезагрузки", bootStart) {
            bootStart = it
            MmkvManager.encodeStartOnBoot(it)
        }
        EmeryCard(modifier = Modifier.fillMaxWidth()) {
            Column(modifier = Modifier.padding(16.dp)) {
                Text("Регион", style = MaterialTheme.typography.titleMedium)
                Spacer(Modifier.height(4.dp))
                Text("Москва", style = MaterialTheme.typography.bodyLarge)
            }
        }

        Spacer(Modifier.height(12.dp))
        Text(
            text = "Диагностика",
            style = MaterialTheme.typography.titleMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        EmeryCard(modifier = Modifier.fillMaxWidth()) {
            Column(
                modifier = Modifier.padding(16.dp),
                verticalArrangement = Arrangement.spacedBy(4.dp),
            ) {
                InfoRow("Сервис VPN", if (isRunning) "Запущен" else "Остановлен")
                InfoRow("Выбранный сервер", selectedServer ?: "Не выбран")
            }
        }
        OutlinedButton(
            onClick = openClassicServers,
            shape = RoundedCornerShape(14.dp),
            border = BorderStroke(1.dp, MaterialTheme.colorScheme.outline),
            modifier = Modifier.fillMaxWidth(),
        ) {
            Text("Расширенная панель")
        }
    }
}

// ──────────────────────────────────────────────
// Reusable Components
// ──────────────────────────────────────────────

@Composable
private fun EmeryCard(
    modifier: Modifier = Modifier,
    content: @Composable ColumnScope.() -> Unit,
) {
    Card(
        modifier = modifier,
        shape = RoundedCornerShape(16.dp),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
        border = BorderStroke(1.dp, MaterialTheme.colorScheme.outlineVariant),
        elevation = CardDefaults.cardElevation(defaultElevation = 0.dp),
        content = content,
    )
}

@Composable
private fun InfoRow(
    label: String,
    value: String,
    valueColor: Color = MaterialTheme.colorScheme.onSurface,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 6.dp),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            text = label,
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Text(
            text = value,
            style = MaterialTheme.typography.bodyMedium,
            color = valueColor,
            fontWeight = FontWeight.Medium,
        )
    }
}

@Composable
private fun SettingsToggle(label: String, value: Boolean, onChange: (Boolean) -> Unit) {
    EmeryCard {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 16.dp, vertical = 12.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.SpaceBetween,
        ) {
            Text(
                text = label,
                style = MaterialTheme.typography.bodyLarge,
                modifier = Modifier.weight(1f),
            )
            Switch(
                checked = value,
                onCheckedChange = onChange,
                colors = SwitchDefaults.colors(
                    checkedThumbColor = MaterialTheme.colorScheme.primary,
                    checkedTrackColor = MaterialTheme.colorScheme.primaryContainer,
                    uncheckedThumbColor = MaterialTheme.colorScheme.onSurfaceVariant,
                    uncheckedTrackColor = MaterialTheme.colorScheme.surfaceVariant,
                    uncheckedBorderColor = MaterialTheme.colorScheme.outline,
                ),
            )
        }
    }
}

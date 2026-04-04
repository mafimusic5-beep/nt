$premium = Join-Path $PSScriptRoot '..\app\src\main\java\com\v2ray\ang\ui\premium\PremiumActivity.kt'
$viewModel = Join-Path $PSScriptRoot '..\app\src\main\java\com\v2ray\ang\ui\premium\vpn\VpnMainViewModel.kt'

$premium = [System.IO.Path]::GetFullPath($premium)
$viewModel = [System.IO.Path]::GetFullPath($viewModel)

@'
package com.v2ray.ang.ui.premium

import android.content.Intent
import android.net.VpnService
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Devices
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.filled.SupportAgent
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.view.WindowCompat
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.navigation.NavDestination.Companion.hierarchy
import androidx.navigation.NavGraph.Companion.findStartDestination
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import com.v2ray.ang.handler.EmeryAccessManager
import com.v2ray.ang.handler.SettingsManager
import com.v2ray.ang.handler.V2RayServiceManager
import com.v2ray.ang.ui.AccessKeyActivity
import com.v2ray.ang.ui.premium.vpn.VpnMainRoute
import com.v2ray.ang.ui.premium.vpn.VpnMainViewModel
import com.v2ray.ang.ui.premium.vpn.VpnServiceCommand
import com.v2ray.ang.ui.premium.vpn.VpnUiDebugLogger
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.launch
import org.json.JSONObject

private enum class EmeryRoute { Splash, Home, Devices, Support }

private data class NavItem(
    val route: EmeryRoute,
    val icon: ImageVector,
    val label: String
)

private val navItems = listOf(
    NavItem(EmeryRoute.Home, Icons.Default.Home, "Главная"),
    NavItem(EmeryRoute.Devices, Icons.Default.Devices, "Устройства"),
    NavItem(EmeryRoute.Support, Icons.Default.SupportAgent, "Поддержка")
)

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
        if (!EmeryAccessManager.isActivated()) {
            startActivity(Intent(this, AccessKeyActivity::class.java))
            finish()
            return
        }
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
                    }
                )
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun EmeryApp(
    requestVpnPermission: ((onGranted: () -> Unit) -> Unit),
) {
    val navController = rememberNavController()
    var showMenu by remember { mutableStateOf(false) }
    val sheetState = rememberModalBottomSheetState()
    val scope = rememberCoroutineScope()

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
                val context = LocalContext.current

                LaunchedEffect(Unit) {
                    VpnUiDebugLogger.log(
                        hypothesisId = "H2",
                        location = "PremiumActivity.kt:EmeryApp",
                        message = "home route switched to vpn compose screen",
                        data = JSONObject(),
                    )
                }

                LaunchedEffect(vpnMainViewModel, context) {
                    vpnMainViewModel.commands.collectLatest { command ->
                        when (command) {
                            VpnServiceCommand.Start -> {
                                if (SettingsManager.isVpnMode()) {
                                    requestVpnPermission {
                                        V2RayServiceManager.startVService(context)
                                    }
                                } else {
                                    V2RayServiceManager.startVService(context)
                                }
                            }

                            VpnServiceCommand.Stop -> {
                                V2RayServiceManager.stopVService(context)
                            }
                        }
                    }
                }

                VpnMainRoute(
                    viewModel = vpnMainViewModel,
                    onSettingsClick = { showMenu = true },
                )
            }
            composable(EmeryRoute.Devices.name) { DevicesScreen { navController.popBackStack() } }
            composable(EmeryRoute.Support.name) { SupportScreen { navController.popBackStack() } }
        }

        if (showMenu) {
            ModalBottomSheet(
                onDismissRequest = { showMenu = false },
                sheetState = sheetState,
                containerColor = MaterialTheme.colorScheme.surface,
                tonalElevation = 8.dp,
                shape = RoundedCornerShape(topStart = 24.dp, topEnd = 24.dp)
            ) {
                Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(16.dp)
                        .padding(bottom = 24.dp)
                ) {
                    Text(
                        "Навигация",
                        style = MaterialTheme.typography.titleMedium,
                        modifier = Modifier.padding(horizontal = 16.dp, vertical = 8.dp),
                        color = MaterialTheme.colorScheme.primary
                    )
                    Spacer(modifier = Modifier.height(8.dp))
                    navItems.forEach { item ->
                        val isSelected = navController.currentDestination?.hierarchy?.any { it.route == item.route.name } == true
                        Row(
                            modifier = Modifier
                                .fillMaxWidth()
                                .background(if (isSelected) MaterialTheme.colorScheme.primaryContainer else Color.Transparent)
                                .clickable {
                                    scope.launch { sheetState.hide() }.invokeOnCompletion {
                                        showMenu = false
                                        if (!isSelected) {
                                            navController.navigate(item.route.name) {
                                                popUpTo(navController.graph.findStartDestination().id) {
                                                    saveState = true
                                                }
                                                launchSingleTop = true
                                                restoreState = true
                                            }
                                        }
                                    }
                                }
                                .padding(16.dp),
                            verticalAlignment = Alignment.CenterVertically
                        ) {
                            Icon(
                                imageVector = item.icon,
                                contentDescription = null,
                                tint = if (isSelected) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurface
                            )
                            Spacer(Modifier.width(16.dp))
                            Text(
                                text = item.label,
                                style = MaterialTheme.typography.bodyLarge,
                                color = if (isSelected) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurface
                            )
                        }
                    }
                }
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

@Composable
private fun DevicesScreen(onBack: () -> Unit) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(16.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center
    ) {
        Text("Устройства", style = MaterialTheme.typography.titleLarge)
        Spacer(modifier = Modifier.height(24.dp))
        OutlinedButton(onClick = onBack) { Text("Назад") }
    }
}

@Composable
private fun SupportScreen(onBack: () -> Unit) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(16.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center
    ) {
        Text("Поддержка", style = MaterialTheme.typography.titleLarge)
        Spacer(modifier = Modifier.height(24.dp))
        OutlinedButton(onClick = onBack) { Text("Назад") }
    }
}
'@ | Set-Content $premium -Encoding UTF8

@'
package com.v2ray.ang.ui.premium.vpn

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.v2ray.ang.handler.EmeryAccessManager
import com.v2ray.ang.handler.EmeryVpnSync
import com.v2ray.ang.handler.V2RayServiceManager
import com.v2ray.ang.network.EmeryBackendClient
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import org.json.JSONObject

data class VpnServerRegionUi(
    val serverId: Long,
    val title: String,
    val healthStatus: String,
)

sealed class VpnServiceCommand {
    object Start : VpnServiceCommand()
    object Stop : VpnServiceCommand()
}

class VpnMainViewModel : ViewModel() {

    private val _uiState = MutableStateFlow(VpnMainUiState())
    val uiState: StateFlow<VpnMainUiState> = _uiState.asStateFlow()

    private val _availableRegions = MutableStateFlow<List<VpnServerRegionUi>>(emptyList())
    val availableRegions: StateFlow<List<VpnServerRegionUi>> = _availableRegions.asStateFlow()

    private val _selectedRegion = MutableStateFlow<VpnServerRegionUi?>(null)
    val selectedRegion: StateFlow<VpnServerRegionUi?> = _selectedRegion.asStateFlow()

    private val _commands = MutableSharedFlow<VpnServiceCommand>(extraBufferCapacity = 1)
    val commands: SharedFlow<VpnServiceCommand> = _commands.asSharedFlow()

    private var connectJob: Job? = null
    private var timerJob: Job? = null

    init {
        refreshAvailableRegions()
        observeVpnRuntimeState()
    }

    private fun observeVpnRuntimeState() {
        viewModelScope.launch {
            V2RayServiceManager.vpnState.collectLatest { runtimeState ->
                when (runtimeState) {
                    V2RayServiceManager.VpnRuntimeState.CONNECTING -> {
                        timerJob?.cancel()
                        _uiState.update { state ->
                            state.copy(
                                connectionState = VpnConnectionState.Connecting,
                                errorMessage = null,
                            )
                        }
                    }

                    V2RayServiceManager.VpnRuntimeState.CONNECTED -> {
                        val wasConnected = _uiState.value.connectionState == VpnConnectionState.Connected
                        _uiState.update { state ->
                            state.copy(
                                connectionState = VpnConnectionState.Connected,
                                errorMessage = null,
                            )
                        }
                        if (!wasConnected) {
                            startTimer()
                        }
                    }

                    V2RayServiceManager.VpnRuntimeState.DISCONNECTING -> {
                        // Keep the current UI state until the runtime reports DISCONNECTED.
                    }

                    V2RayServiceManager.VpnRuntimeState.DISCONNECTED -> {
                        timerJob?.cancel()
                        _uiState.update { state ->
                            state.copy(
                                connectionState = VpnConnectionState.Disconnected,
                                elapsedSeconds = 0L,
                            )
                        }
                    }

                    V2RayServiceManager.VpnRuntimeState.ERROR -> {
                        timerJob?.cancel()
                        _uiState.update { state ->
                            state.copy(
                                connectionState = VpnConnectionState.Disconnected,
                                elapsedSeconds = 0L,
                                errorMessage = "VPN start failed",
                            )
                        }
                    }
                }
            }
        }
    }

    fun refreshAvailableRegions() {
        viewModelScope.launch {
            val currentSelectedId = _selectedRegion.value?.serverId
            val regions = EmeryBackendClient.fetchVpnServers()
                .getOrElse { emptyList() }
                .filter { it.isAvailable }
                .map {
                    VpnServerRegionUi(
                        serverId = it.id,
                        title = it.city,
                        healthStatus = it.healthStatus,
                    )
                }

            _availableRegions.value = regions
            _selectedRegion.value = regions.firstOrNull { it.serverId == currentSelectedId } ?: regions.firstOrNull()

            VpnUiDebugLogger.log(
                hypothesisId = "H6",
                location = "VpnMainViewModel.kt:refreshAvailableRegions",
                message = "available regions refreshed",
                data = JSONObject()
                    .put("count", regions.size)
                    .put("selectedServerId", _selectedRegion.value?.serverId ?: -1),
            )
        }
    }

    fun onLocationSelected(serverId: Long) {
        val selected = _availableRegions.value.firstOrNull { it.serverId == serverId } ?: return
        _selectedRegion.value = selected
        VpnUiDebugLogger.log(
            hypothesisId = "H5",
            location = "VpnMainViewModel.kt:onLocationSelected",
            message = "location selected",
            data = JSONObject().put("serverId", selected.serverId).put("title", selected.title),
        )
    }

    fun onConnectClick() {
        val currentState = _uiState.value
        val selectedRegion = _selectedRegion.value ?: run {
            VpnUiDebugLogger.log(
                hypothesisId = "H3",
                location = "VpnMainViewModel.kt:onConnectClick",
                message = "connect blocked by missing region",
                data = JSONObject(),
            )
            return
        }
        val accessKey = EmeryAccessManager.loadProfile()?.accessKey?.trim().orEmpty()
        if (accessKey.isBlank()) {
            VpnUiDebugLogger.log(
                hypothesisId = "H3",
                location = "VpnMainViewModel.kt:onConnectClick",
                message = "connect blocked by missing saved access key",
                data = JSONObject(),
            )
            return
        }
        if (currentState.connectionState != VpnConnectionState.Disconnected) {
            VpnUiDebugLogger.log(
                hypothesisId = "H3",
                location = "VpnMainViewModel.kt:onConnectClick",
                message = "connect ignored due to state",
                data = JSONObject().put("state", currentState.connectionState.name),
            )
            return
        }

        connectJob?.cancel()
        timerJob?.cancel()
        _uiState.update { state ->
            state.copy(
                connectionState = VpnConnectionState.Connecting,
                elapsedSeconds = 0L,
                errorMessage = null,
            )
        }

        connectJob = viewModelScope.launch {
            val result = EmeryVpnSync.connectToServer(accessKey = accessKey, serverId = selectedRegion.serverId)
            result.fold(
                onSuccess = {
                    _commands.tryEmit(VpnServiceCommand.Start)
                    VpnUiDebugLogger.log(
                        hypothesisId = "H3",
                        location = "VpnMainViewModel.kt:onConnectClick",
                        message = "server imported, waiting for runtime connected",
                        data = JSONObject()
                            .put("serverId", selectedRegion.serverId)
                            .put("title", selectedRegion.title),
                    )
                },
                onFailure = { error ->
                    timerJob?.cancel()
                    _uiState.update { state ->
                        state.copy(
                            connectionState = VpnConnectionState.Disconnected,
                            elapsedSeconds = 0L,
                            errorMessage = error.message ?: "connect_failed",
                        )
                    }
                    VpnUiDebugLogger.log(
                        hypothesisId = "H3",
                        location = "VpnMainViewModel.kt:onConnectClick",
                        message = "backend connect failed",
                        data = JSONObject()
                            .put("serverId", selectedRegion.serverId)
                            .put("reason", error.message ?: "unknown"),
                    )
                    refreshAvailableRegions()
                },
            )
        }
    }

    fun onDisconnectClick() {
        if (!V2RayServiceManager.isRunning()) {
            timerJob?.cancel()
            _uiState.update { state ->
                state.copy(
                    connectionState = VpnConnectionState.Disconnected,
                    elapsedSeconds = 0L,
                )
            }
            return
        }

        _commands.tryEmit(VpnServiceCommand.Stop)

        VpnUiDebugLogger.log(
            hypothesisId = "H3",
            location = "VpnMainViewModel.kt:onDisconnectClick",
            message = "stop requested, waiting for runtime disconnected",
            data = JSONObject(),
        )
    }

    private fun startTimer() {
        timerJob?.cancel()
        timerJob = viewModelScope.launch {
            while (isActive) {
                delay(1000L)
                _uiState.update { state ->
                    if (state.connectionState == VpnConnectionState.Connected) {
                        state.copy(elapsedSeconds = state.elapsedSeconds + 1L)
                    } else {
                        state
                    }
                }
            }
        }
    }

    override fun onCleared() {
        connectJob?.cancel()
        timerJob?.cancel()
        super.onCleared()
    }
}
'@ | Set-Content $viewModel -Encoding UTF8

Write-Host 'patched PremiumActivity.kt and VpnMainViewModel.kt'

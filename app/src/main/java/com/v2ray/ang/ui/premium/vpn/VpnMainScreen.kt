package com.v2ray.ang.ui.premium.vpn

import androidx.compose.animation.animateColorAsState
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import com.v2ray.ang.AppConfig
import com.v2ray.ang.R
import com.v2ray.ang.handler.MmkvManager
import com.v2ray.ang.handler.RegionalPolicyManager
import com.v2ray.ang.handler.RegionalPolicyMode
import kotlinx.coroutines.launch

@Composable
fun VpnMainRoute(
    viewModel: VpnMainViewModel,
    requestVpnPermission: ((onGranted: () -> Unit) -> Unit),
    startVpnService: (String) -> Boolean,
    stopVpnService: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val uiState by viewModel.uiState.collectAsState()
    VpnMainScreen(
        uiState = uiState,
        locations = uiState.locations,
        onLocationSelected = viewModel::onLocationSelected,
        onConnectClick = {
            requestVpnPermission {
                viewModel.onConnectClick(startVpnService)
            }
        },
        onDisconnectClick = { viewModel.onDisconnectClick(stopVpnService) },
        modifier = modifier,
    )
}

@Composable
fun VpnMainScreen(
    uiState: VpnMainUiState,
    locations: List<VpnLocationOption>,
    onLocationSelected: (String) -> Unit,
    onConnectClick: () -> Unit,
    onDisconnectClick: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val context = LocalContext.current
    val policyScope = rememberCoroutineScope()
    var selectedTab by remember { mutableStateOf(MainTab.Home) }
    var autoConnectEnabled by remember { mutableStateOf(MmkvManager.decodeStartOnBoot()) }
    var regionalPolicyMode by remember { mutableStateOf(RegionalPolicyManager.readMode()) }
    var regionalPolicyUpdating by remember { mutableStateOf(false) }
    var regionalPolicyError by remember { mutableStateOf("") }
    var reconnectAfterPolicyChange by remember { mutableStateOf(false) }

    LaunchedEffect(
        reconnectAfterPolicyChange,
        uiState.connectionState,
        uiState.connectButtonEnabled,
    ) {
        if (reconnectAfterPolicyChange && uiState.connectionState == VpnConnectionState.Disconnected) {
            reconnectAfterPolicyChange = false
            if (uiState.connectButtonEnabled) {
                onConnectClick()
            }
        }
    }

    BoxWithConstraints(
        modifier = modifier
            .fillMaxSize()
            .background(AppUiColors.Background)
            .navigationBarsPadding()
            .imePadding(),
    ) {
        val compact = maxHeight < 830.dp
        val tight = maxHeight < 730.dp
        val horizontalPadding = if (tight) 18.dp else 24.dp
        val showParisBackground = selectedTab == MainTab.Home && uiState.selectedLocation.cityLabel() == "Париж"

        if (showParisBackground) {
            Image(
                painter = painterResource(id = R.drawable.skryon_bg_paris),
                contentDescription = null,
                modifier = Modifier.fillMaxSize(),
                contentScale = ContentScale.Crop,
                alignment = Alignment.TopCenter,
            )
        } else {
            BackgroundShape(
                modifier = Modifier
                    .align(Alignment.TopCenter)
                    .padding(top = if (tight) 170.dp else 205.dp)
                    .fillMaxWidth()
                    .height(if (tight) 360.dp else 430.dp),
            )
        }

        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(start = horizontalPadding, top = 0.dp, end = horizontalPadding, bottom = if (tight) 8.dp else 12.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            HeaderBar(selectedLocation = uiState.selectedLocation, compact = compact)

            if (selectedTab == MainTab.Home) {
                Spacer(Modifier.height(if (tight) 18.dp else if (compact) 30.dp else 42.dp))
                StatusBeacon(connectionState = uiState.connectionState, compact = compact, tight = tight)
                Spacer(Modifier.height(if (tight) 10.dp else 16.dp))
                Text(
                    text = screenTitle(uiState.connectionState),
                    style = if (tight) MaterialTheme.typography.headlineSmall else MaterialTheme.typography.headlineMedium,
                    fontWeight = FontWeight.SemiBold,
                    color = AppUiColors.TextPrimary,
                    textAlign = TextAlign.Center,
                    maxLines = 1,
                )
                Spacer(Modifier.height(if (tight) 5.dp else 8.dp))
                Text(
                    text = screenSubtitle(uiState.connectionState),
                    style = if (tight) MaterialTheme.typography.bodyMedium else MaterialTheme.typography.titleMedium,
                    color = AppUiColors.TextSecondary,
                    textAlign = TextAlign.Center,
                    maxLines = 1,
                )
                if (uiState.locationsError.isNotBlank()) {
                    Spacer(Modifier.height(if (tight) 8.dp else 12.dp))
                    Text(
                        text = uiState.locationsError,
                        style = MaterialTheme.typography.bodySmall,
                        color = AppUiColors.TextSecondary,
                        textAlign = TextAlign.Center,
                        maxLines = 2,
                        overflow = TextOverflow.Ellipsis,
                    )
                }
                Spacer(Modifier.weight(1f))
                RegionSelectorCard(
                    selectedLocation = uiState.selectedLocation,
                    locations = locations,
                    compact = compact,
                    tight = tight,
                    onLocationSelected = onLocationSelected,
                )
                Spacer(Modifier.height(if (tight) 12.dp else 16.dp))
                PrimaryConnectButton(
                    state = uiState.connectionState,
                    enabled = uiState.connectButtonEnabled,
                    compact = compact,
                    tight = tight,
                    onClick = {
                        if (uiState.connectionState == VpnConnectionState.Connected) {
                            onDisconnectClick()
                        } else {
                            autoConnectEnabled = true
                            MmkvManager.encodeStartOnBoot(true)
                            onConnectClick()
                        }
                    },
                )
                if (uiState.connectionState == VpnConnectionState.Disconnected && !uiState.connectButtonEnabled) {
                    Spacer(Modifier.height(if (tight) 6.dp else 10.dp))
                    Text(
                        text = when {
                            uiState.activationKey.isBlank() -> "Ключ доступа не найден"
                            uiState.locationsLoading -> "Регион загружается"
                            uiState.locationsError.isNotBlank() -> uiState.locationsError
                            else -> "Регион недоступен"
                        },
                        style = MaterialTheme.typography.bodyMedium,
                        color = AppUiColors.TextSecondary,
                        textAlign = TextAlign.Center,
                        maxLines = 2,
                        overflow = TextOverflow.Ellipsis,
                    )
                }
            } else {
                Spacer(Modifier.height(if (tight) 16.dp else 22.dp))
                AdvancedPage(
                    selectedPolicyMode = regionalPolicyMode,
                    policyUpdateInProgress = regionalPolicyUpdating,
                    policyUpdateError = regionalPolicyError,
                    onRegionalPolicyConfirmed = { mode ->
                        regionalPolicyError = ""
                        regionalPolicyUpdating = true
                        policyScope.launch {
                            val result = RegionalPolicyManager.apply(context, mode)
                            regionalPolicyUpdating = false
                            result.fold(
                                onSuccess = {
                                    regionalPolicyMode = mode
                                    reconnectAfterPolicyChange = true
                                    if (uiState.connectionState != VpnConnectionState.Disconnected) {
                                        onDisconnectClick()
                                    }
                                },
                                onFailure = {
                                    regionalPolicyError =
                                        "Не удалось загрузить актуальный список ограничений. Проверьте интернет и повторите."
                                },
                            )
                        }
                    },
                    autoConnectEnabled = autoConnectEnabled,
                    onAutoConnectChange = { enabled ->
                        autoConnectEnabled = enabled
                        MmkvManager.encodeStartOnBoot(enabled)
                    },
                    compact = compact,
                    tight = tight,
                    modifier = Modifier
                        .fillMaxWidth()
                        .weight(1f),
                )
            }

            Spacer(Modifier.height(if (tight) 14.dp else 20.dp))
            BottomNavigationBar(
                selectedTab = selectedTab,
                compact = compact,
                onHomeClick = { selectedTab = MainTab.Home },
                onAdvancedClick = { selectedTab = MainTab.Advanced },
            )
        }
    }
}

private fun screenTitle(state: VpnConnectionState): String = when (state) {
    VpnConnectionState.Disconnected -> "Защита выключена"
    VpnConnectionState.Connecting -> "Включаем защиту"
    VpnConnectionState.Connected -> "Защита включена"
}

private fun screenSubtitle(state: VpnConnectionState): String = when (state) {
    VpnConnectionState.Disconnected -> "Ваше соединение не защищено"
    VpnConnectionState.Connecting -> "Создаём защищённое подключение"
    VpnConnectionState.Connected -> "Ваше соединение безопасно"
}

@Composable
private fun HeaderBar(selectedLocation: VpnLocationOption, compact: Boolean) {
    Row(
        modifier = Modifier.fillMaxWidth().height(if (compact) 48.dp else 54.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Row(modifier = Modifier.weight(1f), verticalAlignment = Alignment.CenterVertically) {
            Text(
                text = "Skryon",
                style = if (compact) MaterialTheme.typography.headlineSmall else MaterialTheme.typography.headlineMedium,
                fontWeight = FontWeight.SemiBold,
                color = AppUiColors.TextPrimary,
                maxLines = 1,
            )
            Spacer(Modifier.width(7.dp))
            Text(text = "VPN", style = MaterialTheme.typography.titleSmall, color = AppUiColors.TextSecondary, maxLines = 1)
        }
        Text(
            text = selectedLocation.cityLabel(),
            modifier = Modifier.weight(1f),
            style = if (compact) MaterialTheme.typography.titleMedium else MaterialTheme.typography.titleLarge,
            color = AppUiColors.TextPrimary,
            fontWeight = FontWeight.Medium,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
            textAlign = TextAlign.Center,
        )
        Spacer(modifier = Modifier.weight(1f))
    }
}

@Composable
private fun StatusBeacon(connectionState: VpnConnectionState, compact: Boolean = false, tight: Boolean = false) {
    val coreColor by animateColorAsState(
        targetValue = if (connectionState == VpnConnectionState.Connected) AppUiColors.PositiveStrong else AppUiColors.Positive,
        label = "beacon-core",
    )
    val beaconSize = when { tight -> 72.dp; compact -> 88.dp; else -> 104.dp }
    val midSize = when { tight -> 52.dp; compact -> 64.dp; else -> 74.dp }
    val coreSize = when { tight -> 25.dp; compact -> 30.dp; else -> 34.dp }
    Box(Modifier.size(beaconSize), contentAlignment = Alignment.Center) {
        Box(Modifier.size(beaconSize).clip(CircleShape).background(coreColor.copy(alpha = 0.05f)))
        Box(Modifier.size(midSize).clip(CircleShape).background(coreColor.copy(alpha = 0.11f)))
        Box(Modifier.size(coreSize).clip(CircleShape).background(coreColor))
    }
}

@Composable
private fun RegionSelectorCard(
    selectedLocation: VpnLocationOption,
    locations: List<VpnLocationOption>,
    compact: Boolean,
    tight: Boolean,
    onLocationSelected: (String) -> Unit,
) {
    var expanded by remember { mutableStateOf(false) }
    val selectedCode = selectedLocation.countryCodeLabel()
    val selectedTitle = selectedLocation.cityLabel()
    val cardShape = RoundedCornerShape(if (compact) 18.dp else 20.dp)

    BoxWithConstraints(modifier = Modifier.fillMaxWidth()) {
        val menuWidth = maxWidth

        Row(
            modifier = Modifier
                .fillMaxWidth()
                .height(if (tight) 72.dp else 78.dp)
                .clip(cardShape)
                .background(Color.White.copy(alpha = 0.96f))
                .border(1.dp, AppUiColors.Border.copy(alpha = 0.45f), cardShape)
                .clickable(enabled = locations.isNotEmpty()) {
                    expanded = true
                }
                .padding(horizontal = if (compact) 16.dp else 18.dp, vertical = 12.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text = "Регион",
                    style = MaterialTheme.typography.bodySmall,
                    color = AppUiColors.TextSecondary,
                    fontWeight = FontWeight.Medium,
                    maxLines = 1,
                )
                Spacer(Modifier.height(7.dp))
                Row(verticalAlignment = Alignment.CenterVertically) {
                    FlagMark(code = selectedCode, modifier = Modifier.width(28.dp).height(20.dp))
                    Spacer(Modifier.width(10.dp))
                    Text(
                        text = "$selectedTitle • $selectedCode",
                        style = if (compact) MaterialTheme.typography.titleLarge else MaterialTheme.typography.headlineSmall,
                        color = AppUiColors.TextPrimary,
                        fontWeight = FontWeight.SemiBold,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                }
            }
        }

        DropdownMenu(
            expanded = expanded && locations.isNotEmpty(),
            onDismissRequest = { expanded = false },
            modifier = Modifier
                .width(menuWidth)
                .background(Color.White),
        ) {
            locations.forEach { location ->
                val code = location.countryCodeLabel()
                val selected = location.id == selectedLocation.id || location.title == selectedLocation.title
                DropdownMenuItem(
                    text = {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            FlagMark(code = code, modifier = Modifier.width(28.dp).height(20.dp))
                            Spacer(Modifier.width(12.dp))
                            Column(modifier = Modifier.weight(1f)) {
                                Text(
                                    text = location.cityLabel(),
                                    style = MaterialTheme.typography.titleMedium,
                                    color = AppUiColors.TextPrimary,
                                    fontWeight = if (selected) FontWeight.SemiBold else FontWeight.Normal,
                                    maxLines = 1,
                                    overflow = TextOverflow.Ellipsis,
                                )
                                Text(
                                    text = code,
                                    style = MaterialTheme.typography.bodySmall,
                                    color = AppUiColors.TextSecondary,
                                    maxLines = 1,
                                )
                            }
                        }
                    },
                    onClick = {
                        expanded = false
                        onLocationSelected(location.id)
                    },
                    modifier = Modifier.background(
                        if (selected) AppUiColors.RegionSelected else Color.White,
                    ),
                )
            }
        }
    }
}

@Composable
private fun RegionChip(
    location: VpnLocationOption,
    selected: Boolean,
    compact: Boolean,
    onClick: () -> Unit,
) {
    val code = location.countryCodeLabel()
    val shape = RoundedCornerShape(if (compact) 16.dp else 18.dp)
    val background = if (selected) AppUiColors.RegionSelected else Color.White
    val borderColor = if (selected) AppUiColors.Positive.copy(alpha = 0.22f) else AppUiColors.Border

    Row(
        modifier = Modifier
            .width(if (compact) 122.dp else 142.dp)
            .height(if (compact) 56.dp else 64.dp)
            .clip(shape)
            .background(background)
            .border(1.dp, borderColor, shape)
            .clickable { onClick() }
            .padding(horizontal = if (compact) 10.dp else 12.dp, vertical = if (compact) 8.dp else 10.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        FlagMark(code = code, modifier = Modifier.width(22.dp).height(16.dp))
        Spacer(Modifier.width(9.dp))
        Column(modifier = Modifier.weight(1f)) {
            Text(
                text = location.cityLabel(),
                style = MaterialTheme.typography.bodyMedium,
                color = AppUiColors.TextPrimary,
                fontWeight = if (selected) FontWeight.Medium else FontWeight.Normal,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            Spacer(Modifier.height(2.dp))
            Text(
                text = code,
                style = MaterialTheme.typography.bodySmall,
                color = AppUiColors.TextSecondary,
                maxLines = 1,
            )
        }
    }
}

@Composable
private fun FlagMark(code: String, modifier: Modifier = Modifier) {
    Canvas(modifier = modifier.clip(RoundedCornerShape(3.dp))) {
        val w = size.width
        val h = size.height
        fun stripe(color: Color, left: Float, top: Float, right: Float, bottom: Float) {
            drawRect(color, topLeft = Offset(w * left, h * top), size = androidx.compose.ui.geometry.Size(w * (right - left), h * (bottom - top)))
        }

        when (code.uppercase()) {
            "FR" -> {
                stripe(Color(0xFF21468B), 0f, 0f, 0.333f, 1f)
                stripe(Color.White, 0.333f, 0f, 0.666f, 1f)
                stripe(Color(0xFFEF4135), 0.666f, 0f, 1f, 1f)
            }
            "DE" -> {
                stripe(Color(0xFF111111), 0f, 0f, 1f, 0.333f)
                stripe(Color(0xFFDD0000), 0f, 0.333f, 1f, 0.666f)
                stripe(Color(0xFFFFCE00), 0f, 0.666f, 1f, 1f)
            }
            "NL" -> {
                stripe(Color(0xFFAE1C28), 0f, 0f, 1f, 0.333f)
                stripe(Color.White, 0f, 0.333f, 1f, 0.666f)
                stripe(Color(0xFF21468B), 0f, 0.666f, 1f, 1f)
            }
            "UK", "GB" -> {
                drawRect(Color(0xFF012169))
                stripe(Color.White, 0.42f, 0f, 0.58f, 1f)
                stripe(Color.White, 0f, 0.40f, 1f, 0.60f)
                stripe(Color(0xFFC8102E), 0.46f, 0f, 0.54f, 1f)
                stripe(Color(0xFFC8102E), 0f, 0.45f, 1f, 0.55f)
            }
            "PL" -> {
                stripe(Color.White, 0f, 0f, 1f, 0.5f)
                stripe(Color(0xFFDC143C), 0f, 0.5f, 1f, 1f)
            }
            "RU" -> {
                stripe(Color.White, 0f, 0f, 1f, 0.333f)
                stripe(Color(0xFF0039A6), 0f, 0.333f, 1f, 0.666f)
                stripe(Color(0xFFD52B1E), 0f, 0.666f, 1f, 1f)
            }
            "US" -> {
                stripe(Color(0xFFB22234), 0f, 0f, 1f, 1f)
                stripe(Color.White, 0f, 0.15f, 1f, 0.28f)
                stripe(Color.White, 0f, 0.43f, 1f, 0.56f)
                stripe(Color.White, 0f, 0.71f, 1f, 0.84f)
                stripe(Color(0xFF3C3B6E), 0f, 0f, 0.45f, 0.55f)
            }
            "EU" -> {
                drawRect(Color(0xFF244AA5))
                drawCircle(Color(0xFFFFD700), radius = h * 0.12f, center = Offset(w * 0.50f, h * 0.50f))
            }
            else -> {
                drawRect(AppUiColors.Border)
                stripe(Color.White.copy(alpha = 0.62f), 0f, 0f, 1f, 0.5f)
            }
        }
    }
}

@Composable
private fun AutoConnectCard(
    enabled: Boolean,
    compact: Boolean,
    onCheckedChange: (Boolean) -> Unit,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(if (compact) 18.dp else 22.dp))
            .background(Color.White.copy(alpha = 0.94f))
            .border(1.dp, AppUiColors.Border, RoundedCornerShape(if (compact) 18.dp else 22.dp))
            .clickable { onCheckedChange(!enabled) }
            .padding(horizontal = if (compact) 14.dp else 18.dp, vertical = if (compact) 10.dp else 12.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(modifier = Modifier.weight(1f)) {
            Text(
                text = "Автоподключение",
                style = if (compact) MaterialTheme.typography.bodyLarge else MaterialTheme.typography.titleMedium,
                color = AppUiColors.TextPrimary,
                fontWeight = FontWeight.Medium,
                maxLines = 1,
            )
            Spacer(Modifier.height(2.dp))
            Text(
                text = if (enabled) "VPN запустится после перезагрузки" else "Включится после первого запуска VPN",
                style = MaterialTheme.typography.bodySmall,
                color = AppUiColors.TextSecondary,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
        }
        Spacer(Modifier.width(12.dp))
        Switch(
            checked = enabled,
            onCheckedChange = onCheckedChange,
        )
    }
}

@Composable
private fun BottomNavigationBar(selectedTab: MainTab, compact: Boolean, onHomeClick: () -> Unit, onAdvancedClick: () -> Unit) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .height(if (compact) 62.dp else 70.dp)
            .clip(RoundedCornerShape(if (compact) 22.dp else 26.dp))
            .background(Color.White.copy(alpha = 0.94f))
            .border(1.dp, AppUiColors.Border, RoundedCornerShape(if (compact) 22.dp else 26.dp))
            .padding(horizontal = if (compact) 8.dp else 10.dp, vertical = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.SpaceBetween,
    ) {
        BottomNavItem(
            label = "Главная",
            iconType = MiniIconType.Home,
            selected = selectedTab == MainTab.Home,
            compact = compact,
            modifier = Modifier.weight(1f),
            onClick = onHomeClick,
        )
        Spacer(Modifier.width(if (compact) 8.dp else 12.dp))
        BottomNavItem(
            label = "Расширенные",
            iconType = MiniIconType.Settings,
            selected = selectedTab == MainTab.Advanced,
            compact = compact,
            modifier = Modifier.weight(1f),
            onClick = onAdvancedClick,
        )
    }
}

@Composable
private fun BottomNavItem(
    label: String,
    iconType: MiniIconType,
    selected: Boolean,
    compact: Boolean,
    modifier: Modifier = Modifier,
    onClick: () -> Unit,
) {
    val tint = if (selected) AppUiColors.TextPrimary else AppUiColors.TextSecondary
    val background = if (selected) AppUiColors.SelectedSurface else Color.Transparent
    Row(
        modifier = modifier
            .clip(RoundedCornerShape(18.dp))
            .background(background)
            .clickable { onClick() }
            .padding(horizontal = if (compact) 8.dp else 12.dp, vertical = if (compact) 10.dp else 12.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.Center,
    ) {
        MiniIcon(type = iconType, tint = tint, size = if (compact) 18.dp else 21.dp)
        Spacer(Modifier.width(if (compact) 7.dp else 9.dp))
        Text(
            text = label,
            style = if (compact) MaterialTheme.typography.bodyMedium else MaterialTheme.typography.bodyLarge,
            color = tint,
            fontWeight = if (selected) FontWeight.Medium else FontWeight.Normal,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
        )
    }
}

@Composable
private fun AdvancedPage(
    selectedPolicyMode: RegionalPolicyMode?,
    policyUpdateInProgress: Boolean,
    policyUpdateError: String,
    onRegionalPolicyConfirmed: (RegionalPolicyMode) -> Unit,
    autoConnectEnabled: Boolean,
    onAutoConnectChange: (Boolean) -> Unit,
    compact: Boolean,
    tight: Boolean,
    modifier: Modifier = Modifier,
) {
    var dnsValue by remember { mutableStateOf(readDnsSetting()) }
    var statusText by remember { mutableStateOf("") }

    Column(
        modifier = modifier.verticalScroll(rememberScrollState()),
        horizontalAlignment = Alignment.Start,
    ) {
        Text(
            text = "Расширенные",
            style = if (tight) MaterialTheme.typography.headlineSmall else MaterialTheme.typography.headlineMedium,
            color = AppUiColors.TextPrimary,
            fontWeight = FontWeight.SemiBold,
            maxLines = 1,
        )
        Spacer(Modifier.height(8.dp))
        Text(
            text = "Региональная политика и параметры VPN",
            style = MaterialTheme.typography.titleMedium,
            color = AppUiColors.TextSecondary,
            maxLines = 2,
        )
        Spacer(Modifier.height(if (tight) 14.dp else 18.dp))

        RegionalPolicyCard(
            selectedMode = selectedPolicyMode,
            updateInProgress = policyUpdateInProgress,
            updateError = policyUpdateError,
            compact = compact,
            tight = tight,
            onPolicyConfirmed = onRegionalPolicyConfirmed,
        )

        Spacer(Modifier.height(if (tight) 12.dp else 16.dp))

        DeviceTableCard(
            compact = compact,
            tight = tight,
        )

        Spacer(Modifier.height(if (tight) 12.dp else 16.dp))

        AutoConnectCard(
            enabled = autoConnectEnabled,
            compact = compact,
            onCheckedChange = onAutoConnectChange,
        )

        Spacer(Modifier.height(if (tight) 12.dp else 16.dp))

        DnsSettingsCard(
            dnsValue = dnsValue,
            statusText = statusText,
            compact = compact,
            tight = tight,
            onDnsChange = {
                dnsValue = it
                statusText = ""
            },
            onSaveClick = {
                val normalized = normalizeDnsInput(dnsValue)
                dnsValue = normalized
                saveDnsSettings(normalized)
                statusText = "DNS сохранён. Переподключите VPN."
            },
        )

        Spacer(Modifier.height(if (tight) 8.dp else 12.dp))
    }
}

@Composable
private fun RegionalPolicyCard(
    selectedMode: RegionalPolicyMode?,
    updateInProgress: Boolean,
    updateError: String,
    compact: Boolean,
    tight: Boolean,
    onPolicyConfirmed: (RegionalPolicyMode) -> Unit,
) {
    var showOutsideRussiaConfirmation by remember { mutableStateOf(false) }
    val shape = RoundedCornerShape(if (compact) 18.dp else 22.dp)

    fun requestMode(mode: RegionalPolicyMode) {
        if (updateInProgress || mode == selectedMode) {
            return
        }
        if (mode == RegionalPolicyMode.International) {
            showOutsideRussiaConfirmation = true
        } else {
            onPolicyConfirmed(mode)
        }
    }

    Column(
        modifier = Modifier
            .fillMaxWidth()
            .clip(shape)
            .background(Color.White.copy(alpha = 0.96f))
            .border(1.dp, AppUiColors.Border, shape)
            .padding(horizontal = if (compact) 14.dp else 18.dp, vertical = if (tight) 14.dp else 18.dp),
        horizontalAlignment = Alignment.Start,
    ) {
        Text(
            text = "Региональная политика РФ",
            style = MaterialTheme.typography.titleMedium,
            color = AppUiColors.TextPrimary,
            fontWeight = FontWeight.SemiBold,
        )
        Spacer(Modifier.height(6.dp))
        Text(
            text = "Выберите территорию текущего использования VPN.",
            style = MaterialTheme.typography.bodySmall,
            color = AppUiColors.TextSecondary,
        )
        Spacer(Modifier.height(if (tight) 10.dp else 14.dp))
        PolicyChoiceRow(
            selected = selectedMode == RegionalPolicyMode.International,
            enabled = !updateInProgress,
            title = "Международный",
            description = "VPN используется за пределами Российской Федерации",
            onClick = { requestMode(RegionalPolicyMode.International) },
        )
        Spacer(Modifier.height(8.dp))
        PolicyChoiceRow(
            selected = selectedMode == RegionalPolicyMode.Russia,
            enabled = !updateInProgress,
            title = "Российская Федерация",
            description = "Для использования в РФ; ограниченные ресурсы блокируются",
            onClick = { requestMode(RegionalPolicyMode.Russia) },
        )
        if (selectedMode == RegionalPolicyMode.Russia) {
            Spacer(Modifier.height(10.dp))
            Text(
                text = "Трафик к доменам и IP из актуального списка ограничений блокируется без перенаправления.",
                style = MaterialTheme.typography.bodySmall,
                color = AppUiColors.TextPrimary,
                fontWeight = FontWeight.Medium,
            )
        }
        Spacer(Modifier.height(if (tight) 10.dp else 14.dp))
        Text(
            text = "Сервис не определяет, не проверяет и не сохраняет ваше местоположение.",
            style = MaterialTheme.typography.bodySmall,
            color = AppUiColors.TextSecondary,
        )
        if (updateInProgress) {
            Spacer(Modifier.height(12.dp))
            Row(verticalAlignment = Alignment.CenterVertically) {
                CircularProgressIndicator(
                    modifier = Modifier.size(18.dp),
                    strokeWidth = 2.dp,
                    color = AppUiColors.TextPrimary,
                )
                Spacer(Modifier.width(10.dp))
                Text(
                    text = "Обновляем список ограничений…",
                    style = MaterialTheme.typography.bodySmall,
                    color = AppUiColors.TextPrimary,
                )
            }
        } else if (updateError.isNotBlank()) {
            Spacer(Modifier.height(12.dp))
            Text(
                text = updateError,
                style = MaterialTheme.typography.bodySmall,
                color = Color(0xFFB42318),
            )
        }
    }

    if (showOutsideRussiaConfirmation) {
        AlertDialog(
            onDismissRequest = { showOutsideRussiaConfirmation = false },
            title = {
                Text(
                    text = "Использование за пределами РФ",
                    fontWeight = FontWeight.SemiBold,
                )
            },
            text = {
                Text(
                    text = "Я подтверждаю, что текущее VPN-подключение используется за пределами Российской Федерации.",
                )
            },
            confirmButton = {
                Button(
                    onClick = {
                        showOutsideRussiaConfirmation = false
                        onPolicyConfirmed(RegionalPolicyMode.International)
                    },
                    colors = ButtonDefaults.buttonColors(
                        containerColor = AppUiColors.TextPrimary,
                        contentColor = Color.White,
                    ),
                ) {
                    Text(
                        text = "Подтвердить и переподключиться",
                        textAlign = TextAlign.Center,
                    )
                }
            },
            dismissButton = {
                TextButton(onClick = { showOutsideRussiaConfirmation = false }) {
                    Text("Отмена")
                }
            },
        )
    }
}

@Composable
private fun PolicyChoiceRow(
    selected: Boolean,
    enabled: Boolean,
    title: String,
    description: String,
    onClick: () -> Unit,
) {
    val shape = RoundedCornerShape(14.dp)
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clip(shape)
            .background(if (selected) AppUiColors.SelectedSurface else Color.Transparent)
            .border(
                width = 1.dp,
                color = if (selected) AppUiColors.TextPrimary.copy(alpha = 0.22f) else AppUiColors.Border,
                shape = shape,
            )
            .clickable(enabled = enabled, onClick = onClick)
            .padding(horizontal = 10.dp, vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        RadioButton(
            selected = selected,
            enabled = enabled,
            onClick = null,
        )
        Spacer(Modifier.width(8.dp))
        Column(modifier = Modifier.weight(1f)) {
            Text(
                text = title,
                style = MaterialTheme.typography.bodyLarge,
                color = AppUiColors.TextPrimary,
                fontWeight = FontWeight.SemiBold,
            )
            Spacer(Modifier.height(2.dp))
            Text(
                text = description,
                style = MaterialTheme.typography.bodySmall,
                color = AppUiColors.TextSecondary,
            )
        }
    }
}

@Composable
private fun DnsSettingsCard(
    dnsValue: String,
    statusText: String,
    compact: Boolean,
    tight: Boolean,
    onDnsChange: (String) -> Unit,
    onSaveClick: () -> Unit,
) {
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(if (compact) 18.dp else 22.dp))
            .background(Color.White.copy(alpha = 0.96f))
            .border(1.dp, AppUiColors.Border, RoundedCornerShape(if (compact) 18.dp else 22.dp))
            .padding(horizontal = if (compact) 14.dp else 18.dp, vertical = if (tight) 14.dp else 18.dp),
        horizontalAlignment = Alignment.Start,
    ) {
        Text(
            text = "DNS",
            style = MaterialTheme.typography.titleMedium,
            color = AppUiColors.TextPrimary,
            fontWeight = FontWeight.Medium,
            maxLines = 1,
        )
        Spacer(Modifier.height(if (tight) 10.dp else 14.dp))
        OutlinedTextField(
            value = dnsValue,
            onValueChange = onDnsChange,
            modifier = Modifier.fillMaxWidth(),
            singleLine = true,
            label = { Text("DNS сервер") },
            placeholder = { Text("1.1.1.1,8.8.8.8") },
            shape = RoundedCornerShape(16.dp),
        )
        Spacer(Modifier.height(8.dp))
        Text(
            text = "Можно указать один или несколько DNS через запятую.",
            style = MaterialTheme.typography.bodySmall,
            color = AppUiColors.TextSecondary,
        )
        Spacer(Modifier.height(if (tight) 12.dp else 16.dp))
        Button(
            onClick = onSaveClick,
            modifier = Modifier.fillMaxWidth().height(if (tight) 50.dp else 54.dp),
            shape = RoundedCornerShape(if (compact) 18.dp else 22.dp),
            colors = ButtonDefaults.buttonColors(
                containerColor = AppUiColors.TextPrimary,
                contentColor = Color.White,
            ),
        ) {
            Text(
                text = "Сохранить DNS",
                style = MaterialTheme.typography.titleMedium,
                fontWeight = FontWeight.Medium,
                maxLines = 1,
            )
        }
        if (statusText.isNotBlank()) {
            Spacer(Modifier.height(10.dp))
            Text(
                text = statusText,
                style = MaterialTheme.typography.bodyMedium,
                color = AppUiColors.PositiveStrong,
                textAlign = TextAlign.Center,
                modifier = Modifier.fillMaxWidth(),
            )
        }
    }
}

private fun readDnsSetting(): String {
    return normalizeDnsInput(MmkvManager.decodeSettingsString(AppConfig.PREF_VPN_DNS, AppConfig.DNS_VPN))
}

private fun saveDnsSettings(dns: String) {
    MmkvManager.encodeSettings(AppConfig.PREF_VPN_DNS, dns)
    MmkvManager.encodeSettings(AppConfig.PREF_REMOTE_DNS, dns)
    MmkvManager.encodeSettings(AppConfig.PREF_DOMESTIC_DNS, dns)
}

private fun normalizeDnsInput(value: String?): String {
    return value
        ?.split(',', '\n', ';', ' ')
        ?.map { it.trim() }
        ?.filter { it.isNotEmpty() }
        ?.joinToString(",")
        ?.takeIf { it.isNotEmpty() }
        ?: AppConfig.DNS_VPN
}

@Composable
private fun BackgroundShape(modifier: Modifier = Modifier) {
    Canvas(modifier = modifier) {
        val w = size.width
        val h = size.height
        drawCircle(Color(0xFFF1F5F7).copy(alpha = 0.78f), radius = w * 0.30f, center = Offset(w * 0.13f, h * 0.36f))
        drawCircle(Color(0xFFF1F5F7).copy(alpha = 0.72f), radius = w * 0.31f, center = Offset(w * 0.85f, h * 0.34f))
        val center = Path().apply {
            moveTo(w * 0.50f, h * 0.04f)
            lineTo(w * 0.92f, h * 0.74f)
            lineTo(w * 0.08f, h * 0.74f)
            close()
        }
        drawPath(center, Color.White.copy(alpha = 0.52f))
        drawRect(brush = Brush.verticalGradient(0f to Color.White.copy(alpha = 0f), 1f to Color.White))
    }
}

@Composable
fun HumanSilhouetteBlock(uiState: VpnMainUiState, modifier: Modifier = Modifier) {
    Box(modifier = modifier, contentAlignment = Alignment.Center) { StatusBeacon(uiState.connectionState) }
}

@Composable
fun HologramManBlock(uiState: VpnMainUiState, modifier: Modifier = Modifier) {
    HumanSilhouetteBlock(uiState = uiState, modifier = modifier)
}

@Composable
fun LocationSelector(selectedLocation: VpnLocationOption, locations: List<VpnLocationOption>, onLocationSelected: (String) -> Unit, modifier: Modifier = Modifier) {
    Row(
        modifier = modifier
            .clip(RoundedCornerShape(18.dp))
            .background(AppUiColors.Surface)
            .border(1.dp, AppUiColors.Border, RoundedCornerShape(18.dp))
            .clickable(enabled = locations.isNotEmpty()) { onLocationSelected(selectedLocation.title) }
            .padding(horizontal = 16.dp, vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            text = selectedLocation.regionLabel(),
            style = MaterialTheme.typography.bodyLarge,
            color = AppUiColors.TextPrimary,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
        )
    }
}

@Composable
fun ConnectionTimer(time: String) {
    Text(time, style = MaterialTheme.typography.headlineMedium, color = AppUiColors.TextPrimary, fontWeight = FontWeight.Medium)
}

@Composable
fun ConnectionStatusOverlay(uiState: VpnMainUiState, modifier: Modifier = Modifier) { Box(modifier = modifier) }

@Composable
fun ActivationKeyField(value: String, onValueChange: (String) -> Unit, enabled: Boolean, modifier: Modifier = Modifier) { Box(modifier = modifier) }

@Composable
fun PrimaryConnectButton(state: VpnConnectionState, enabled: Boolean, onClick: () -> Unit, compact: Boolean = false, tight: Boolean = false, modifier: Modifier = Modifier) {
    val containerColor by animateColorAsState(targetValue = if (enabled) Color(0xFF101319) else Color(0xFFB9BEC6), label = "primary-button-color")
    val label = when (state) {
        VpnConnectionState.Disconnected -> "Включить VPN"
        VpnConnectionState.Connecting -> "Включаем..."
        VpnConnectionState.Connected -> "Отключить VPN"
    }
    Button(
        onClick = onClick,
        enabled = enabled && state != VpnConnectionState.Connecting,
        modifier = modifier.fillMaxWidth().height(if (tight) 54.dp else if (compact) 60.dp else 66.dp),
        shape = RoundedCornerShape(if (compact) 22.dp else 26.dp),
        colors = ButtonDefaults.buttonColors(
            containerColor = containerColor,
            contentColor = Color.White,
            disabledContainerColor = containerColor.copy(alpha = 0.80f),
            disabledContentColor = Color.White.copy(alpha = 0.72f),
        ),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.Center) {
            if (state == VpnConnectionState.Connected || state == VpnConnectionState.Connecting) {
                PauseGlyph(tint = Color.White.copy(alpha = 0.86f), compact = compact)
                Spacer(Modifier.width(18.dp))
            }
            Text(text = label, style = if (compact) MaterialTheme.typography.titleMedium else MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Medium, maxLines = 1)
        }
    }
}

@Composable
private fun PauseGlyph(tint: Color, compact: Boolean) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.width(if (compact) 3.dp else 4.dp).height(if (compact) 16.dp else 18.dp).clip(RoundedCornerShape(999.dp)).background(tint))
        Spacer(Modifier.width(6.dp))
        Box(Modifier.width(if (compact) 3.dp else 4.dp).height(if (compact) 16.dp else 18.dp).clip(RoundedCornerShape(999.dp)).background(tint))
    }
}

private enum class MainTab { Home, Advanced }

private enum class MiniIconType { Home, Settings }

@Composable
private fun MiniIcon(type: MiniIconType, tint: Color, size: Dp) {
    Canvas(Modifier.size(size)) {
        val sw = this.size.width
        val sh = this.size.height
        val minSide = this.size.minDimension
        val stroke = (minSide * 0.08f).coerceAtLeast(1.5f)
        when (type) {
            MiniIconType.Home -> {
                val p = Path().apply {
                    moveTo(sw * 0.18f, sh * 0.48f)
                    lineTo(sw * 0.50f, sh * 0.20f)
                    lineTo(sw * 0.82f, sh * 0.48f)
                    lineTo(sw * 0.76f, sh * 0.82f)
                    lineTo(sw * 0.28f, sh * 0.82f)
                    close()
                }
                drawPath(p, tint)
            }
            MiniIconType.Settings -> {
                drawCircle(tint, radius = minSide * 0.23f, center = Offset(sw / 2f, sh / 2f), style = Stroke(width = stroke))
                repeat(6) { index ->
                    val angle = Math.toRadians((index * 60.0) - 90.0)
                    val start = Offset(sw / 2f + kotlin.math.cos(angle).toFloat() * minSide * 0.30f, sh / 2f + kotlin.math.sin(angle).toFloat() * minSide * 0.30f)
                    val end = Offset(sw / 2f + kotlin.math.cos(angle).toFloat() * minSide * 0.42f, sh / 2f + kotlin.math.sin(angle).toFloat() * minSide * 0.42f)
                    drawLine(tint, start, end, strokeWidth = stroke, cap = StrokeCap.Round)
                }
                drawCircle(tint, radius = minSide * 0.06f, center = Offset(sw / 2f, sh / 2f))
            }
        }
    }
}

private fun VpnLocationOption.cityLabel(): String {
    val value = title.trim()
    val lower = value.lowercase().replace('_', '-').replace('.', '-').replace(' ', '-')
    return when {
        value.isBlank() -> "Париж"
        lower.contains("paris") || lower.contains("париж") || lower.contains("france") || hasRegionToken(lower, "fr") -> "Париж"
        lower.contains("frankfurt") || lower.contains("germany") || hasRegionToken(lower, "de") -> "Франкфурт"
        lower.contains("amsterdam") || lower.contains("netherlands") || hasRegionToken(lower, "nl") -> "Амстердам"
        lower.contains("moscow") || lower.contains("москва") || hasRegionToken(lower, "ru") -> "Москва"
        lower.contains("warsaw") || hasRegionToken(lower, "pl") -> "Варшава"
        lower.contains("london") || hasRegionToken(lower, "uk") || hasRegionToken(lower, "gb") -> "Лондон"
        lower.contains("new-york") || lower.contains("newyork") || hasRegionToken(lower, "us") || hasRegionToken(lower, "usa") -> "Нью-Йорк"
        lower.contains("stockholm") || hasRegionToken(lower, "se") -> "Стокгольм"
        lower.contains("helsinki") || hasRegionToken(lower, "fi") -> "Хельсинки"
        lower.contains("madrid") || hasRegionToken(lower, "es") -> "Мадрид"
        lower.contains("milan") || hasRegionToken(lower, "it") -> "Милан"
        lower.contains("istanbul") || hasRegionToken(lower, "tr") -> "Стамбул"
        lower.contains("singapore") || hasRegionToken(lower, "sg") -> "Сингапур"
        lower.contains("skryon") -> "Париж"
        lower.contains("europe") || hasRegionToken(lower, "eu") -> "Европа"
        else -> value
    }
}

private fun VpnLocationOption.countryCodeLabel(): String {
    val value = title.trim().lowercase().replace('_', '-').replace('.', '-').replace(' ', '-')
    return when {
        cityLabel() == "Париж" -> "FR"
        cityLabel() == "Франкфурт" -> "DE"
        cityLabel() == "Амстердам" -> "NL"
        cityLabel() == "Москва" -> "RU"
        cityLabel() == "Варшава" -> "PL"
        cityLabel() == "Лондон" -> "UK"
        cityLabel() == "Нью-Йорк" -> "US"
        cityLabel() == "Стокгольм" -> "SE"
        cityLabel() == "Хельсинки" -> "FI"
        cityLabel() == "Мадрид" -> "ES"
        cityLabel() == "Милан" -> "IT"
        cityLabel() == "Стамбул" -> "TR"
        cityLabel() == "Сингапур" -> "SG"
        cityLabel() == "Европа" -> "EU"
        hasRegionToken(value, "fr") -> "FR"
        hasRegionToken(value, "de") -> "DE"
        hasRegionToken(value, "nl") -> "NL"
        hasRegionToken(value, "ru") -> "RU"
        hasRegionToken(value, "pl") -> "PL"
        hasRegionToken(value, "uk") || hasRegionToken(value, "gb") -> "UK"
        hasRegionToken(value, "us") || hasRegionToken(value, "usa") -> "US"
        hasRegionToken(value, "eu") -> "EU"
        else -> "VPN"
    }
}

private fun VpnLocationOption.regionLabel(): String {
    val city = cityLabel()
    return when (city) {
        "Париж" -> "Регион FR"
        "Франкфурт" -> "Регион DE"
        "Амстердам" -> "Регион NL"
        "Москва" -> "Регион RU"
        "Варшава" -> "Регион PL"
        "Лондон" -> "Регион UK"
        "Нью-Йорк" -> "Регион US"
        "Европа" -> "Регион EU"
        else -> city
    }
}

private fun hasRegionToken(value: String, token: String): Boolean {
    return Regex("(^|[^a-z0-9])${Regex.escape(token.lowercase())}([^a-z0-9]|$)").containsMatchIn(value)
}

private object AppUiColors {
    val Background = Color.White
    val Surface = Color(0xFFF9FAFB)
    val SelectedSurface = Color(0xFFF4F6F8)
    val RegionSelected = Color(0xFFF3FAEF)
    val Border = Color(0xFFE6E9EE)
    val TextPrimary = Color(0xFF111319)
    val TextSecondary = Color(0xFF7D828D)
    val Positive = Color(0xFF74C84F)
    val PositiveStrong = Color(0xFF36A852)
}

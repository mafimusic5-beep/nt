package com.v2ray.ang.ui.premium

import android.graphics.Color as AndroidColor
import android.net.VpnService
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
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
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.scale
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.view.WindowCompat
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import com.v2ray.ang.AppConfig
import com.v2ray.ang.R
import com.v2ray.ang.handler.MmkvManager
import com.v2ray.ang.handler.RegionalPolicyManager
import com.v2ray.ang.handler.RegionalPolicyMode
import com.v2ray.ang.handler.V2RayServiceManager
import com.v2ray.ang.ui.premium.vpn.VpnMainRoute
import com.v2ray.ang.ui.premium.vpn.VpnMainViewModel
import com.v2ray.ang.ui.premium.vpn.VpnUiDebugLogger
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import org.json.JSONObject

private enum class EmeryRoute { Splash, Activation, RegionalPolicy, Home }

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

    Scaffold(
        containerColor = Color.White,
    ) { padding ->
        NavHost(
            navController = navController,
            startDestination = EmeryRoute.Splash.name,
            modifier = Modifier.padding(padding),
        ) {
            composable(EmeryRoute.Splash.name) {
                SplashScreen {
                    val nextRoute = when {
                        savedActivationCode().isBlank() -> EmeryRoute.Activation.name
                        regionalPolicyPending() -> EmeryRoute.RegionalPolicy.name
                        else -> EmeryRoute.Home.name
                    }
                    navController.navigate(nextRoute) {
                        popUpTo(EmeryRoute.Splash.name) { inclusive = true }
                    }
                }
            }
            composable(EmeryRoute.Activation.name) {
                ActivationScreen(
                    onActivated = { code ->
                        val formattedCode = formatSkryonActivationCode(code)
                        val result = activateSkryonCode(context, code, formattedCode)
                        if (result.ok) {
                            val guid = saveActivatedSkryonConfig(result.config)
                            MmkvManager.encodeSettings(SKRYON_ACTIVATION_CODE_PREF, result.code.ifBlank { formattedCode })
                            MmkvManager.encodeSettings(SKRYON_ACTIVATION_CONFIG_PREF, result.config)
                            MmkvManager.encodeSettings(SKRYON_SERVER_GUID_PREF, guid)
                            MmkvManager.encodeSettings(SKRYON_SERVER_ID_PREF, result.serverId)
                            MmkvManager.encodeSettings(SKRYON_CONFIG_REVISION_PREF, result.revision)
                            MmkvManager.encodeSettings(AppConfig.PREF_REGIONAL_POLICY_MODE, "")
                            MmkvManager.encodeSettings(AppConfig.PREF_REGIONAL_POLICY_PENDING, true)
                            navController.navigate(EmeryRoute.RegionalPolicy.name) {
                                popUpTo(EmeryRoute.Activation.name) { inclusive = true }
                            }
                        }
                        result
                    },
                )
            }
            composable(EmeryRoute.RegionalPolicy.name) {
                RegionalPolicyOnboardingScreen(
                    initialMode = RegionalPolicyManager.readMode(),
                    onContinue = { mode ->
                        val result = RegionalPolicyManager.apply(context, mode)
                        if (result.isSuccess) {
                            MmkvManager.encodeSettings(AppConfig.PREF_REGIONAL_POLICY_PENDING, false)
                            navController.navigate(EmeryRoute.Home.name) {
                                popUpTo(EmeryRoute.RegionalPolicy.name) { inclusive = true }
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

private fun regionalPolicyPending(): Boolean {
    return MmkvManager.decodeSettingsBool(AppConfig.PREF_REGIONAL_POLICY_PENDING, false)
}

@Composable
private fun RegionalPolicyOnboardingScreen(
    initialMode: RegionalPolicyMode?,
    onContinue: suspend (RegionalPolicyMode) -> Result<Unit>,
) {
    val coroutineScope = rememberCoroutineScope()
    var selectedMode by remember(initialMode) { mutableStateOf(initialMode) }
    var saving by remember { mutableStateOf(false) }
    var saveError by remember { mutableStateOf("") }

    BoxWithConstraints(
        modifier = Modifier
            .fillMaxSize()
            .background(Color.White)
            .navigationBarsPadding(),
    ) {
        val compact = maxHeight < 760.dp
        Column(
            modifier = Modifier
                .fillMaxSize()
                .verticalScroll(rememberScrollState())
                .padding(horizontal = if (compact) 20.dp else 24.dp, vertical = if (compact) 20.dp else 28.dp),
        ) {
            Text(
                text = "Skryon",
                style = MaterialTheme.typography.headlineSmall,
                color = Color(0xFF111319),
                fontWeight = FontWeight.Bold,
            )
            Spacer(Modifier.height(if (compact) 24.dp else 34.dp))
            Text(
                text = "Настройка режима",
                style = MaterialTheme.typography.headlineMedium,
                color = Color(0xFF111319),
                fontWeight = FontWeight.SemiBold,
            )
            Spacer(Modifier.height(8.dp))
            Text(
                text = "Активация завершена. Выберите территорию использования VPN.",
                style = MaterialTheme.typography.bodyLarge,
                color = Color(0xFF6F7580),
            )
            Spacer(Modifier.height(if (compact) 18.dp else 24.dp))

            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .clip(RoundedCornerShape(24.dp))
                    .background(Color(0xFFFCFCFD))
                    .border(1.dp, Color(0xFFE3E6EA), RoundedCornerShape(24.dp))
                    .padding(if (compact) 16.dp else 20.dp),
            ) {
                Text(
                    text = "Региональная политика",
                    style = MaterialTheme.typography.titleLarge,
                    color = Color(0xFF111319),
                    fontWeight = FontWeight.SemiBold,
                )
                Spacer(Modifier.height(6.dp))
                Text(
                    text = "Ни один режим не выбирается автоматически.",
                    style = MaterialTheme.typography.bodySmall,
                    color = Color(0xFF6F7580),
                )
                Spacer(Modifier.height(if (compact) 14.dp else 18.dp))
                OnboardingPolicyChoice(
                    selected = selectedMode == RegionalPolicyMode.International,
                    enabled = !saving,
                    title = "Международный",
                    description = "VPN используется за пределами Российской Федерации",
                    onClick = { selectedMode = RegionalPolicyMode.International },
                )
                Spacer(Modifier.height(10.dp))
                OnboardingPolicyChoice(
                    selected = selectedMode == RegionalPolicyMode.Russia,
                    enabled = !saving,
                    title = "Российская Федерация",
                    description = "Для использования в РФ; ограниченные ресурсы блокируются",
                    onClick = { selectedMode = RegionalPolicyMode.Russia },
                )

                if (selectedMode == RegionalPolicyMode.International) {
                    Spacer(Modifier.height(14.dp))
                    Text(
                        text = "Я подтверждаю, что текущее VPN-подключение используется за пределами Российской Федерации.",
                        style = MaterialTheme.typography.bodyMedium,
                        color = Color(0xFF111319),
                        fontWeight = FontWeight.Medium,
                    )
                } else if (selectedMode == RegionalPolicyMode.Russia) {
                    Spacer(Modifier.height(14.dp))
                    Text(
                        text = "Ограничения применяются на сервере. Списки не скачиваются на устройство.",
                        style = MaterialTheme.typography.bodyMedium,
                        color = Color(0xFF111319),
                        fontWeight = FontWeight.Medium,
                    )
                }

                Spacer(Modifier.height(16.dp))
                Text(
                    text = "Если вы находитесь не в РФ, выберите «Международный». Позже политику можно переключить в разделе «Расширенные».",
                    style = MaterialTheme.typography.bodySmall,
                    color = Color(0xFF067A6F),
                    fontWeight = FontWeight.Medium,
                )
                Spacer(Modifier.height(10.dp))
                Text(
                    text = "Сервис не определяет, не проверяет и не сохраняет ваше местоположение.",
                    style = MaterialTheme.typography.bodySmall,
                    color = Color(0xFF6F7580),
                )
                if (saving) {
                    Spacer(Modifier.height(14.dp))
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        CircularProgressIndicator(
                            modifier = Modifier.size(18.dp),
                            strokeWidth = 2.dp,
                            color = Color(0xFF111319),
                        )
                        Spacer(Modifier.width(10.dp))
                        Text(
                            text = "Применяем политику…",
                            style = MaterialTheme.typography.bodySmall,
                            color = Color(0xFF111319),
                        )
                    }
                } else if (saveError.isNotBlank()) {
                    Spacer(Modifier.height(14.dp))
                    Text(
                        text = saveError,
                        style = MaterialTheme.typography.bodySmall,
                        color = Color(0xFFB42318),
                    )
                }
            }

            Spacer(Modifier.height(if (compact) 18.dp else 24.dp))
            Button(
                onClick = {
                    val mode = selectedMode ?: return@Button
                    saveError = ""
                    saving = true
                    coroutineScope.launch {
                        val result = onContinue(mode)
                        saving = false
                        if (result.isFailure) {
                            saveError =
                                "Не удалось сохранить выбранную политику. Повторите."
                        }
                    }
                },
                enabled = selectedMode != null && !saving,
                modifier = Modifier
                    .fillMaxWidth()
                    .height(if (compact) 54.dp else 60.dp),
                shape = RoundedCornerShape(20.dp),
                colors = ButtonDefaults.buttonColors(
                    containerColor = Color(0xFF111319),
                    contentColor = Color.White,
                    disabledContainerColor = Color(0xFFB9BEC6),
                    disabledContentColor = Color.White.copy(alpha = 0.78f),
                ),
            ) {
                Text(
                    text = when {
                        selectedMode == null -> "Выберите режим"
                        saving -> "Сохраняем…"
                        selectedMode == RegionalPolicyMode.International -> "Подтвердить и продолжить"
                        else -> "Сохранить и продолжить"
                    },
                    style = MaterialTheme.typography.titleMedium,
                    fontWeight = FontWeight.Medium,
                )
            }
        }
    }
}

@Composable
private fun OnboardingPolicyChoice(
    selected: Boolean,
    enabled: Boolean,
    title: String,
    description: String,
    onClick: () -> Unit,
) {
    val shape = RoundedCornerShape(16.dp)
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clip(shape)
            .background(if (selected) Color(0xFFF0F7F6) else Color.White)
            .border(
                width = 1.dp,
                color = if (selected) Color(0xFF067A6F).copy(alpha = 0.45f) else Color(0xFFE3E6EA),
                shape = shape,
            )
            .clickable(enabled = enabled, onClick = onClick)
            .padding(horizontal = 12.dp, vertical = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        RadioButton(
            selected = selected,
            enabled = enabled,
            onClick = null,
        )
        Spacer(Modifier.width(10.dp))
        Column(modifier = Modifier.weight(1f)) {
            Text(
                text = title,
                style = MaterialTheme.typography.bodyLarge,
                color = Color(0xFF111319),
                fontWeight = FontWeight.SemiBold,
            )
            Spacer(Modifier.height(3.dp))
            Text(
                text = description,
                style = MaterialTheme.typography.bodySmall,
                color = Color(0xFF6F7580),
            )
        }
    }
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

@Composable
private fun ActivationScreen(
    onActivated: suspend (String) -> SkryonActivationResult,
) {
    var code by remember { mutableStateOf("") }
    var error by remember { mutableStateOf("") }
    var isLoading by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()

    BoxWithConstraints(
        modifier = Modifier
            .fillMaxSize()
            .background(Color.White)
            .navigationBarsPadding(),
    ) {
        val compact = maxHeight < 850.dp
        val pantherHeight = if (compact) maxHeight * 0.68f else maxHeight * 0.72f
        val pantherTop = if (compact) 92.dp else 112.dp
        val pantherOffsetX = if (compact) 64.dp else 86.dp
        val cardHeight = if (compact) 306.dp else 368.dp

        Text(
            text = "Skryon",
            modifier = Modifier
                .align(Alignment.TopStart)
                .padding(start = 30.dp, top = 0.dp)
                .offset(y = if (compact) 4.dp else 6.dp),
            style = TextStyle(
                fontSize = if (compact) 45.sp else 52.sp,
                fontWeight = FontWeight.Bold,
                letterSpacing = 0.sp,
                color = Color(0xFF07080A),
            ),
            maxLines = 1,
        )

        Image(
            painter = painterResource(id = R.drawable.skryon_panther_activation),
            contentDescription = null,
            modifier = Modifier
                .align(Alignment.TopCenter)
                .offset(x = pantherOffsetX, y = pantherTop)
                .fillMaxWidth(1.62f)
                .height(pantherHeight),
            contentScale = ContentScale.Fit,
        )

        Box(
            modifier = Modifier
                .align(Alignment.BottomCenter)
                .fillMaxWidth()
                .height(cardHeight + 118.dp)
                .background(
                    Brush.verticalGradient(
                        0f to Color.White.copy(alpha = 0f),
                        0.26f to Color.White.copy(alpha = 0.92f),
                        1f to Color.White,
                    ),
                ),
        )

        Column(
            modifier = Modifier
                .align(Alignment.BottomCenter)
                .fillMaxWidth()
                .height(cardHeight)
                .shadow(
                    elevation = 8.dp,
                    shape = RoundedCornerShape(topStart = 34.dp, topEnd = 34.dp),
                    spotColor = Color(0x14000000),
                )
                .clip(RoundedCornerShape(topStart = 34.dp, topEnd = 34.dp))
                .background(Color.White)
                .padding(horizontal = if (compact) 26.dp else 36.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Spacer(Modifier.height(if (compact) 22.dp else 34.dp))
            Text(
                text = "Активация",
                style = TextStyle(
                    fontSize = if (compact) 29.sp else 34.sp,
                    lineHeight = if (compact) 34.sp else 39.sp,
                    fontWeight = FontWeight.Bold,
                    color = Color(0xFF111319),
                    textAlign = TextAlign.Center,
                ),
                maxLines = 1,
            )
            Spacer(Modifier.height(8.dp))
            Text(
                text = "Введите код для доступа",
                style = TextStyle(
                    fontSize = if (compact) 17.sp else 20.sp,
                    lineHeight = if (compact) 23.sp else 26.sp,
                    color = Color(0xFF7D828D),
                    textAlign = TextAlign.Center,
                ),
                maxLines = 1,
            )
            Spacer(Modifier.height(if (compact) 24.dp else 34.dp))
            ActivationCodeInput(
                code = code,
                onCodeChange = {
                    code = it
                    error = ""
                },
                compact = compact,
            )
            if (error.isNotBlank()) {
                Spacer(Modifier.height(8.dp))
                Text(
                    text = error,
                    style = TextStyle(fontSize = 14.sp, color = Color(0xFFE54848)),
                    textAlign = TextAlign.Center,
                )
                Spacer(Modifier.height(6.dp))
            } else {
                Spacer(Modifier.height(if (compact) 13.dp else 20.dp))
            }
            Button(
                onClick = {
                    if (isLoading) {
                        return@Button
                    }
                    if (code.length < SKRYON_ACTIVATION_CODE_LENGTH) {
                        error = "Введите код полностью"
                    } else {
                        scope.launch {
                            isLoading = true
                            error = ""
                            val result = onActivated(code)
                            if (!result.ok) {
                                error = result.error.ifBlank { "Ошибка активации" }
                            }
                            isLoading = false
                        }
                    }
                },
                enabled = !isLoading,
                modifier = Modifier
                    .fillMaxWidth()
                    .height(if (compact) 56.dp else 64.dp),
                shape = RoundedCornerShape(18.dp),
                colors = ButtonDefaults.buttonColors(
                    containerColor = Color(0xFF08090B),
                    contentColor = Color.White,
                    disabledContainerColor = Color(0xFF2D3036),
                    disabledContentColor = Color.White,
                ),
            ) {
                Text(
                    text = if (isLoading) "Проверка..." else "Войти",
                    style = TextStyle(
                        fontSize = if (compact) 22.sp else 25.sp,
                        fontWeight = FontWeight.Medium,
                    ),
                )
            }
        }
    }
}

@Composable
private fun ActivationCodeInput(
    code: String,
    onCodeChange: (String) -> Unit,
    compact: Boolean,
) {
    BasicTextField(
        value = code,
        onValueChange = { value ->
            onCodeChange(sanitizeSkryonActivationCode(value))
        },
        singleLine = true,
        textStyle = TextStyle(color = Color.Transparent, fontSize = 1.sp),
        cursorBrush = SolidColor(Color.Transparent),
        keyboardOptions = KeyboardOptions(
            capitalization = KeyboardCapitalization.Characters,
            keyboardType = KeyboardType.Ascii,
        ),
        modifier = Modifier.fillMaxWidth(),
        decorationBox = { innerTextField ->
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .height(if (compact) 54.dp else 60.dp),
                contentAlignment = Alignment.Center,
            ) {
                ActivationCodeSlots(
                    code = code,
                    compact = compact,
                )
                Box(
                    modifier = Modifier
                        .fillMaxSize()
                        .alpha(0.01f),
                ) {
                    innerTextField()
                }
            }
        },
    )
}

@Composable
private fun ActivationCodeSlots(
    code: String,
    compact: Boolean,
) {
    val groups = listOf(1, 3, 2, 2, 2, 1)
    var index = 0
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        groups.forEachIndexed { groupIndex, groupSize ->
            repeat(groupSize) {
                val char = code.getOrNull(index)?.toString().orEmpty()
                index += 1
                CodeCharacterSlot(
                    char = char,
                    compact = compact,
                )
            }
            if (groupIndex != groups.lastIndex) {
                Text(
                    text = "-",
                    style = TextStyle(
                        fontSize = if (compact) 20.sp else 24.sp,
                        fontWeight = FontWeight.Normal,
                        color = Color(0xFF111319),
                        textAlign = TextAlign.Center,
                    ),
                    modifier = Modifier.width(if (compact) 10.dp else 13.dp),
                )
            }
        }
    }
}

@Composable
private fun CodeCharacterSlot(
    char: String,
    compact: Boolean,
) {
    val characterState = rememberActivationCharacterState(char)

    Column(
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
        modifier = Modifier.width(if (compact) 20.dp else 24.dp),
    ) {
        Text(
            text = characterState.visibleChar,
            style = TextStyle(
                fontSize = if (compact) 20.sp else 24.sp,
                lineHeight = if (compact) 24.sp else 28.sp,
                fontWeight = FontWeight.Normal,
                color = Color(0xFF111319),
                textAlign = TextAlign.Center,
            ),
            modifier = Modifier
                .height(if (compact) 28.dp else 32.dp)
                .scale(characterState.scale)
                .alpha(characterState.alpha),
            maxLines = 1,
        )
        Box(
            modifier = Modifier
                .width(if (compact) 19.dp else 23.dp)
                .height(1.35.dp),
        ) {
            Box(
                modifier = Modifier
                    .fillMaxSize()
                    .background(Color(0xFFC4C9D0)),
            )
            Box(
                modifier = Modifier
                    .fillMaxSize()
                    .alpha(characterState.underlineAlpha)
                    .background(Color(0xFF111319)),
            )
        }
    }
}

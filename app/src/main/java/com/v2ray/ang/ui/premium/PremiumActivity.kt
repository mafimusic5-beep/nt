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
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
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
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalClipboardManager
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
import com.v2ray.ang.R
import com.v2ray.ang.handler.MmkvManager
import com.v2ray.ang.handler.V2RayServiceManager
import com.v2ray.ang.ui.premium.vpn.VpnMainRoute
import com.v2ray.ang.ui.premium.vpn.VpnMainViewModel
import com.v2ray.ang.ui.premium.vpn.VpnUiDebugLogger
import java.util.Locale
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import org.json.JSONObject

private const val ACTIVATION_CODE_LENGTH = 11

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
                ActivationScreen(
                    onActivated = { code ->
                        val formattedCode = formatActivationCode(code)
                        val result = activateSkryonCode(context, code, formattedCode)
                        if (result.ok) {
                            val guid = saveActivatedSkryonConfig(result.config)
                            MmkvManager.encodeSettings(SKRYON_ACTIVATION_CODE_PREF, result.code.ifBlank { formattedCode })
                            MmkvManager.encodeSettings(SKRYON_ACTIVATION_CONFIG_PREF, result.config)
                            MmkvManager.encodeSettings(SKRYON_SERVER_GUID_PREF, guid)
                            MmkvManager.encodeSettings(SKRYON_SERVER_ID_PREF, result.serverId)
                            MmkvManager.encodeSettings(SKRYON_CONFIG_REVISION_PREF, result.revision)
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
        val uiScale = (maxWidth.value / 393f).coerceIn(0.82f, 1.45f)

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

        // Keep the soft white transition behind the panther. The lower card is drawn
        // after the image and masks its transparent tail, so the paws meet its top edge.
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
                .offset(y = -cardHeight)
                .fillMaxWidth()
                .height(if (compact) 32.dp else 40.dp)
                .background(
                    Brush.verticalGradient(
                        0f to Color.White.copy(alpha = 0f),
                        0.42f to Color.White.copy(alpha = 0.04f),
                        0.76f to Color.White.copy(alpha = 0.24f),
                        1f to Color.White.copy(alpha = 0.62f),
                    ),
                ),
        )

        Column(
            modifier = Modifier
                .align(Alignment.BottomCenter)
                .fillMaxWidth()
                .height(cardHeight)
                .shadow(
                    elevation = 14.dp,
                    shape = RoundedCornerShape(topStart = 34.dp, topEnd = 34.dp),
                    ambientColor = Color(0x38000000),
                    spotColor = Color(0x70000000),
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
                uiScale = uiScale,
                modifier = Modifier
                    .fillMaxWidth()
                    .height(if (compact) 54.dp else 60.dp),
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
                    if (code.length < ACTIVATION_CODE_LENGTH) {
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
                    containerColor = Color.Black,
                    contentColor = Color.White,
                    disabledContainerColor = Color.Black,
                    disabledContentColor = Color.White,
                ),
            ) {
                Text(
                    text = if (isLoading) "Проверка..." else "Войти",
                    style = TextStyle(
                        fontSize = if (compact) 22.sp else 25.sp,
                        fontWeight = FontWeight.Bold,
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
    uiScale: Float,
    modifier: Modifier = Modifier,
) {
    val clipboardManager = LocalClipboardManager.current
    val focusRequester = remember { FocusRequester() }

    LaunchedEffect(Unit) {
        focusRequester.requestFocus()
    }

    Row(
        modifier = modifier
            .background(Color.White, RoundedCornerShape(8.dp * uiScale))
            .border(
                width = 1.dp,
                color = Color(0xFFE1E1E1),
                shape = RoundedCornerShape(8.dp * uiScale),
            ),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        BasicTextField(
            value = code,
            onValueChange = { value ->
                onCodeChange(sanitizeActivationCode(value))
            },
            singleLine = true,
            textStyle = TextStyle(color = Color.Transparent, fontSize = 1.sp),
            cursorBrush = SolidColor(Color.Transparent),
            keyboardOptions = KeyboardOptions(
                capitalization = KeyboardCapitalization.Characters,
                keyboardType = KeyboardType.Ascii,
            ),
            modifier = Modifier
                .weight(1f)
                .fillMaxHeight()
                .focusRequester(focusRequester),
            decorationBox = { innerTextField ->
                Box(
                    modifier = Modifier.fillMaxSize(),
                    contentAlignment = Alignment.Center,
                ) {
                    ActivationCodeSlots(
                        code = code,
                        uiScale = uiScale,
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

        Box(
            modifier = Modifier
                .width(1.dp)
                .height(21.dp * uiScale)
                .background(Color(0xFFE1E1E1)),
        )

        Box(
            modifier = Modifier
                .width(61.dp * uiScale)
                .fillMaxHeight()
                .clickable {
                    clipboardManager.getText()?.text?.let { pasted ->
                        onCodeChange(sanitizeActivationCode(pasted))
                    }
                    focusRequester.requestFocus()
                },
            contentAlignment = Alignment.Center,
        ) {
            Text(
                text = "Вставить",
                style = TextStyle(
                    fontSize = (13f * uiScale).sp,
                    lineHeight = (17f * uiScale).sp,
                    fontWeight = FontWeight.Normal,
                    color = Color(0xFF006B47),
                    textAlign = TextAlign.Center,
                ),
                maxLines = 1,
            )
        }
    }
}

@Composable
private fun ActivationCodeSlots(
    code: String,
    uiScale: Float,
) {
    val groups = listOf(4, 3, 4)
    var index = 0

    Row(
        modifier = Modifier
            .fillMaxSize()
            .padding(horizontal = 8.dp * uiScale),
        horizontalArrangement = Arrangement.spacedBy(
            space = 24.dp * uiScale,
            alignment = Alignment.CenterHorizontally,
        ),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        groups.forEach { groupSize ->
            Row(
                horizontalArrangement = Arrangement.spacedBy(4.dp * uiScale),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                repeat(groupSize) {
                    val slotIndex = index
                    val char = code.getOrNull(slotIndex)?.toString().orEmpty()
                    index += 1
                    CodeCharacterSlot(
                        char = char,
                        active = slotIndex == code.length,
                        uiScale = uiScale,
                    )
                }
            }
        }
    }
}

@Composable
private fun CodeCharacterSlot(
    char: String,
    active: Boolean,
    uiScale: Float,
) {
    Box(
        modifier = Modifier
            .width(13.dp * uiScale)
            .fillMaxHeight(),
    ) {
        if (char.isNotEmpty()) {
            Text(
                text = char,
                modifier = Modifier
                    .align(Alignment.TopCenter)
                    .offset(y = 8.dp * uiScale),
                style = TextStyle(
                    fontSize = (15f * uiScale).sp,
                    lineHeight = (18f * uiScale).sp,
                    fontWeight = FontWeight.Normal,
                    color = Color(0xFF111319),
                    textAlign = TextAlign.Center,
                ),
                maxLines = 1,
            )
        } else if (active) {
            Box(
                modifier = Modifier
                    .align(Alignment.TopCenter)
                    .offset(y = 11.dp * uiScale)
                    .width(1.2.dp)
                    .height(16.dp * uiScale)
                    .background(Color(0xFF00704A)),
            )
        }

        Box(
            modifier = Modifier
                .align(Alignment.TopCenter)
                .offset(y = 32.dp * uiScale)
                .width(13.dp * uiScale)
                .height(1.dp)
                .background(if (active) Color(0xFF00704A) else Color(0xFFB8BDC5)),
        )
    }
}

private fun sanitizeActivationCode(value: String): String {
    return value
        .uppercase(Locale.ROOT)
        .filter { it.isLetterOrDigit() }
        .take(ACTIVATION_CODE_LENGTH)
}

private fun formatActivationCode(rawCode: String): String {
    val groups = listOf(1, 3, 2, 2, 2, 1)
    var index = 0
    return groups.mapNotNull { size ->
        val part = rawCode.drop(index).take(size)
        index += size
        part.takeIf { it.isNotBlank() }
    }.joinToString("-")
}

package com.v2ray.ang.ui.premium

import android.graphics.Color as AndroidColor
import android.net.VpnService
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateDpAsState
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
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
import androidx.compose.ui.draw.scale
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.drawscope.Stroke
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
import com.v2ray.ang.R
import com.v2ray.ang.handler.MmkvManager
import com.v2ray.ang.handler.V2RayServiceManager
import com.v2ray.ang.ui.premium.vpn.VpnMainRoute
import com.v2ray.ang.ui.premium.vpn.VpnMainViewModel
import com.v2ray.ang.ui.premium.vpn.VpnUiDebugLogger
import java.util.Locale
import kotlin.math.min
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import org.json.JSONObject

private const val SKRYON_ACTIVATION_CODE_PREF = "SKRYON_ACTIVATION_CODE"
private const val ACTIVATION_CODE_LENGTH = 11
private val ACTIVATION_CODE_GROUPS = listOf(1, 3, 2, 2, 2, 1)

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
        val cardHeight = if (compact) 318.dp else 384.dp

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
                .padding(horizontal = if (compact) 22.dp else 34.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Spacer(Modifier.height(if (compact) 20.dp else 32.dp))
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
            Spacer(Modifier.height(if (compact) 24.dp else 32.dp))
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
            onCodeChange(sanitizeActivationCode(value))
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
                    .height(if (compact) 66.dp else 74.dp),
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
    val activeIndex = code.length.coerceIn(0, ACTIVATION_CODE_LENGTH - 1)
    var index = 0
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.Center,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        ACTIVATION_CODE_GROUPS.forEachIndexed { groupIndex, groupSize ->
            Row(
                horizontalArrangement = Arrangement.spacedBy(if (compact) 2.dp else 3.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                repeat(groupSize) {
                    val slotIndex = index
                    val char = code.getOrNull(slotIndex)?.toString().orEmpty()
                    CodeCharacterSlot(
                        char = char,
                        active = slotIndex == activeIndex && code.length < ACTIVATION_CODE_LENGTH,
                        compact = compact,
                    )
                    index += 1
                }
            }
            if (groupIndex != ACTIVATION_CODE_GROUPS.lastIndex) {
                ActivationCodeSeparator(compact = compact)
            }
        }
    }
}

@Composable
private fun ActivationCodeSeparator(compact: Boolean) {
    Spacer(Modifier.width(if (compact) 5.dp else 7.dp))
}

@Composable
private fun CodeCharacterSlot(
    char: String,
    active: Boolean,
    compact: Boolean,
) {
    val filled = char.isNotBlank()
    val scale by animateFloatAsState(
        targetValue = when {
            active -> 1.045f
            filled -> 1.0f
            else -> 0.985f
        },
        animationSpec = tween(durationMillis = 170),
        label = "activation-ticket-slot-scale",
    )
    val elevation by animateDpAsState(
        targetValue = when {
            active -> 8.dp
            filled -> 2.dp
            else -> 0.dp
        },
        animationSpec = tween(durationMillis = 180),
        label = "activation-ticket-slot-elevation",
    )
    val borderColor by animateColorAsState(
        targetValue = when {
            active -> Color(0xFF0E4D36)
            filled -> Color(0xFFC2C9D2)
            else -> Color(0xFFD4DAE2)
        },
        animationSpec = tween(durationMillis = 180),
        label = "activation-ticket-slot-border",
    )
    val backgroundColor by animateColorAsState(
        targetValue = if (active) Color(0xFFFFFFFF) else Color(0xFFFCFDFE),
        animationSpec = tween(durationMillis = 180),
        label = "activation-ticket-slot-background",
    )
    val underlineColor by animateColorAsState(
        targetValue = if (active) Color(0xFF0E4D36) else Color(0xFFC9CED6),
        animationSpec = tween(durationMillis = 180),
        label = "activation-ticket-slot-underline",
    )
    val underlineWidth by animateDpAsState(
        targetValue = if (active) 18.dp else 16.dp,
        animationSpec = tween(durationMillis = 180),
        label = "activation-ticket-slot-underline-width",
    )
    val textAlpha by animateFloatAsState(
        targetValue = if (filled) 1f else 0f,
        animationSpec = tween(durationMillis = 120),
        label = "activation-ticket-slot-text-alpha",
    )
    val blink = rememberInfiniteTransition(label = "activation-ticket-slot-cursor")
    val cursorAlpha by blink.animateFloat(
        initialValue = 0.24f,
        targetValue = 1f,
        animationSpec = infiniteRepeatable(
            animation = tween(durationMillis = 620),
            repeatMode = RepeatMode.Reverse,
        ),
        label = "activation-ticket-slot-cursor-alpha",
    )

    Column(
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
        modifier = Modifier.width(if (compact) 21.dp else 24.dp),
    ) {
        Box(
            modifier = Modifier
                .width(if (compact) 21.dp else 24.dp)
                .height(if (compact) 46.dp else 52.dp)
                .scale(scale)
                .shadow(elevation = elevation, shape = RoundedCornerShape(if (compact) 9.dp else 11.dp), spotColor = Color(0x1F0E4D36)),
            contentAlignment = Alignment.Center,
        ) {
            TicketSlotCanvas(
                borderColor = borderColor,
                backgroundColor = backgroundColor,
                active = active,
            )
            if (filled) {
                Text(
                    text = char,
                    style = TextStyle(
                        fontSize = if (compact) 18.sp else 21.sp,
                        lineHeight = if (compact) 22.sp else 25.sp,
                        fontWeight = FontWeight.Normal,
                        color = Color(0xFF06080D).copy(alpha = textAlpha),
                        textAlign = TextAlign.Center,
                    ),
                    maxLines = 1,
                )
            } else if (active) {
                Box(
                    modifier = Modifier
                        .width(1.7.dp)
                        .height(if (compact) 22.dp else 25.dp)
                        .alpha(cursorAlpha)
                        .clip(RoundedCornerShape(999.dp))
                        .background(Color(0xFF0E4D36)),
                )
            }
        }
        Spacer(Modifier.height(if (compact) 5.dp else 7.dp))
        Box(
            modifier = Modifier
                .width(underlineWidth)
                .height(if (active) 1.8.dp else 1.35.dp)
                .clip(RoundedCornerShape(999.dp))
                .background(underlineColor),
        )
    }
}

@Composable
private fun TicketSlotCanvas(
    borderColor: Color,
    backgroundColor: Color,
    active: Boolean,
) {
    Canvas(modifier = Modifier.fillMaxSize()) {
        val w = size.width
        val h = size.height
        val radius = min(w * 0.16f, 8.dp.toPx())
        val notch = min(w * 0.14f, 5.5.dp.toPx())
        val cx = w / 2f
        val strokeWidth = if (active) 1.45.dp.toPx() else 1.dp.toPx()
        val path = Path().apply {
            moveTo(radius, 0f)
            lineTo(w - radius, 0f)
            quadraticBezierTo(w, 0f, w, radius)
            lineTo(w, h - radius)
            quadraticBezierTo(w, h, w - radius, h)
            lineTo(cx + notch, h)
            quadraticBezierTo(cx, h - notch * 1.25f, cx - notch, h)
            lineTo(radius, h)
            quadraticBezierTo(0f, h, 0f, h - radius)
            lineTo(0f, radius)
            quadraticBezierTo(0f, 0f, radius, 0f)
            close()
        }
        drawPath(path = path, color = backgroundColor)
        drawPath(
            path = path,
            color = borderColor,
            style = Stroke(width = strokeWidth),
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
    var index = 0
    return ACTIVATION_CODE_GROUPS.mapNotNull { size ->
        val part = rawCode.drop(index).take(size)
        index += size
        part.takeIf { it.isNotBlank() }
    }.joinToString("-")
}

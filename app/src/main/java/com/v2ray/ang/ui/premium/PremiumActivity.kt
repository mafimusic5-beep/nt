package com.v2ray.ang.ui.premium

import android.graphics.Color as AndroidColor
import android.net.VpnService
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
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
import androidx.compose.foundation.layout.statusBarsPadding
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
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.layout.ContentScale
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
import org.json.JSONObject

private const val SKRYON_ACTIVATION_CODE_PREF = "SKRYON_ACTIVATION_CODE"
private const val ACTIVATION_CODE_LENGTH = 11
private const val ACTIVATION_CODE_PLACEHOLDER = "FAFFGT54QTL"

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
                        MmkvManager.encodeSettings(SKRYON_ACTIVATION_CODE_PREF, formatActivationCode(code))
                        navController.navigate(EmeryRoute.Home.name) {
                            popUpTo(EmeryRoute.Activation.name) { inclusive = true }
                        }
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
    onActivated: (String) -> Unit,
) {
    var code by remember { mutableStateOf("") }
    var error by remember { mutableStateOf("") }

    BoxWithConstraints(
        modifier = Modifier
            .fillMaxSize()
            .background(Color.White)
            .statusBarsPadding()
            .navigationBarsPadding(),
    ) {
        val compact = maxHeight < 850.dp
        val pantherHeight = if (compact) maxHeight * 0.54f else maxHeight * 0.58f
        val pantherTop = if (compact) 42.dp else 74.dp
        val pantherOffsetX = if (compact) 48.dp else 54.dp
        val cardHeight = if (compact) 306.dp else 392.dp

        Text(
            text = "Skryon",
            modifier = Modifier
                .align(Alignment.TopStart)
                .padding(start = 29.dp, top = 0.dp)
                .offset(y = if (compact) (-10).dp else (-4).dp),
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
                .fillMaxWidth(1.18f)
                .height(pantherHeight),
            contentScale = ContentScale.Fit,
        )

        Box(
            modifier = Modifier
                .align(Alignment.BottomCenter)
                .fillMaxWidth()
                .height(cardHeight + 104.dp)
                .background(
                    Brush.verticalGradient(
                        0f to Color.White.copy(alpha = 0f),
                        0.30f to Color.White.copy(alpha = 0.90f),
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
                    elevation = 10.dp,
                    shape = RoundedCornerShape(topStart = 34.dp, topEnd = 34.dp),
                    spotColor = Color(0x18000000),
                )
                .clip(RoundedCornerShape(topStart = 34.dp, topEnd = 34.dp))
                .background(Color.White)
                .padding(horizontal = if (compact) 26.dp else 36.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Spacer(Modifier.height(if (compact) 30.dp else 42.dp))
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
            Spacer(Modifier.height(9.dp))
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
            Spacer(Modifier.height(if (compact) 29.dp else 38.dp))
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
                Spacer(Modifier.height(if (compact) 18.dp else 26.dp))
            }
            Button(
                onClick = {
                    if (code.length < ACTIVATION_CODE_LENGTH) {
                        error = "Введите код полностью"
                    } else {
                        onActivated(code)
                    }
                },
                modifier = Modifier
                    .fillMaxWidth()
                    .height(if (compact) 56.dp else 64.dp),
                shape = RoundedCornerShape(18.dp),
                colors = ButtonDefaults.buttonColors(
                    containerColor = Color(0xFF08090B),
                    contentColor = Color.White,
                ),
            ) {
                Text(
                    text = "Войти",
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
                val typedChar = code.getOrNull(index)?.toString().orEmpty()
                val placeholderChar = ACTIVATION_CODE_PLACEHOLDER.getOrNull(index)?.toString().orEmpty()
                val char = typedChar.ifBlank { placeholderChar }
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
    Column(
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
        modifier = Modifier.width(if (compact) 20.dp else 24.dp),
    ) {
        Text(
            text = char,
            style = TextStyle(
                fontSize = if (compact) 20.sp else 24.sp,
                lineHeight = if (compact) 24.sp else 28.sp,
                fontWeight = FontWeight.Normal,
                color = Color(0xFF111319),
                textAlign = TextAlign.Center,
            ),
            modifier = Modifier.height(if (compact) 28.dp else 32.dp),
            maxLines = 1,
        )
        Box(
            modifier = Modifier
                .width(if (compact) 19.dp else 23.dp)
                .height(1.35.dp)
                .background(Color(0xFFC4C9D0)),
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

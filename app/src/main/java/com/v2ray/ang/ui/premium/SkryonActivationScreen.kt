package com.v2ray.ang.ui.premium

import android.content.ClipboardManager
import android.content.Context
import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateDpAsState
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
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
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
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
import androidx.compose.ui.draw.scale
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
import com.v2ray.ang.R
import java.util.Locale
import kotlinx.coroutines.launch

internal const val SKRYON_ACTIVATION_CODE_LENGTH = 11
private val SKRYON_ACTIVATION_CODE_GROUPS = listOf(1, 3, 2, 2, 2, 1)

@Composable
internal fun SkryonActivationScreen(
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
        val pantherHeight = if (compact) maxHeight * 0.65f else maxHeight * 0.70f
        val pantherTop = if (compact) 92.dp else 112.dp
        val pantherOffsetX = if (compact) 64.dp else 86.dp
        val cardHeight = if (compact) 366.dp else 430.dp

        Text(
            text = "Skryon",
            modifier = Modifier
                .align(Alignment.TopStart)
                .padding(start = 30.dp, top = 0.dp)
                .offset(y = if (compact) 4.dp else 6.dp),
            style = TextStyle(
                fontSize = if (compact) 45.sp else 52.sp,
                fontWeight = FontWeight.Bold,
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
                        0.26f to Color.White.copy(alpha = 0.94f),
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
                    elevation = 6.dp,
                    shape = RoundedCornerShape(topStart = 34.dp, topEnd = 34.dp),
                    spotColor = Color(0x12000000),
                )
                .clip(RoundedCornerShape(topStart = 34.dp, topEnd = 34.dp))
                .background(Color.White)
                .padding(horizontal = if (compact) 22.dp else 34.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Spacer(Modifier.height(if (compact) 22.dp else 32.dp))
            Text(
                text = "Активация",
                style = TextStyle(
                    fontSize = if (compact) 34.sp else 40.sp,
                    lineHeight = if (compact) 40.sp else 46.sp,
                    fontWeight = FontWeight.Bold,
                    color = Color.Black,
                    textAlign = TextAlign.Center,
                ),
                maxLines = 1,
            )
            Spacer(Modifier.height(if (compact) 7.dp else 10.dp))
            Text(
                text = "Введите код для доступа",
                style = TextStyle(
                    fontSize = if (compact) 18.sp else 22.sp,
                    lineHeight = if (compact) 24.sp else 28.sp,
                    color = Color(0xFF858A93),
                    textAlign = TextAlign.Center,
                ),
                maxLines = 1,
            )
            Spacer(Modifier.height(if (compact) 25.dp else 34.dp))
            ActivationCodeInput(
                code = code,
                onCodeChange = {
                    code = it
                    error = ""
                },
                compact = compact,
            )
            if (error.isNotBlank()) {
                Spacer(Modifier.height(9.dp))
                Text(
                    text = error,
                    style = TextStyle(fontSize = 14.sp, color = Color(0xFFE54848)),
                    textAlign = TextAlign.Center,
                )
                Spacer(Modifier.height(8.dp))
            } else {
                Spacer(Modifier.height(if (compact) 22.dp else 28.dp))
            }
            Button(
                onClick = {
                    if (isLoading) return@Button
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
                    .height(if (compact) 64.dp else 72.dp),
                shape = RoundedCornerShape(if (compact) 22.dp else 26.dp),
                colors = ButtonDefaults.buttonColors(
                    containerColor = Color.Black,
                    contentColor = Color.White,
                    disabledContainerColor = Color(0xFF303238),
                    disabledContentColor = Color.White,
                ),
            ) {
                Text(
                    text = if (isLoading) "Проверка..." else "Войти",
                    style = TextStyle(
                        fontSize = if (compact) 23.sp else 27.sp,
                        fontWeight = FontWeight.SemiBold,
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
    val context = LocalContext.current
    val shape = RoundedCornerShape(if (compact) 20.dp else 24.dp)

    Row(
        modifier = Modifier
            .fillMaxWidth()
            .height(if (compact) 94.dp else 106.dp)
            .shadow(
                elevation = 1.dp,
                shape = shape,
                spotColor = Color(0x10000000),
            )
            .clip(shape)
            .background(Color(0xFFFEFEFE))
            .border(1.dp, Color(0xFFE0E2E5), shape)
            .padding(start = if (compact) 16.dp else 20.dp, end = if (compact) 7.dp else 10.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        BasicTextField(
            value = code,
            onValueChange = { onCodeChange(sanitizeActivationCode(it)) },
            singleLine = true,
            textStyle = TextStyle(color = Color.Transparent, fontSize = 1.sp),
            cursorBrush = SolidColor(Color.Transparent),
            keyboardOptions = KeyboardOptions(
                capitalization = KeyboardCapitalization.Characters,
                keyboardType = KeyboardType.Ascii,
            ),
            modifier = Modifier
                .weight(1f)
                .height(if (compact) 58.dp else 66.dp),
            decorationBox = { innerTextField ->
                Box(
                    modifier = Modifier.fillMaxSize(),
                    contentAlignment = Alignment.Center,
                ) {
                    ActivationCodeSlots(code = code, compact = compact)
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
                .height(if (compact) 52.dp else 60.dp)
                .background(Color(0xFFE1E3E6)),
        )

        TextButton(
            onClick = {
                val clipboard = context.getSystemService(Context.CLIPBOARD_SERVICE) as? ClipboardManager
                val pasted = clipboard
                    ?.primaryClip
                    ?.takeIf { it.itemCount > 0 }
                    ?.getItemAt(0)
                    ?.coerceToText(context)
                    ?.toString()
                    .orEmpty()
                if (pasted.isNotBlank()) {
                    onCodeChange(sanitizeActivationCode(pasted))
                }
            },
            modifier = Modifier
                .width(if (compact) 86.dp else 104.dp)
                .height(if (compact) 64.dp else 72.dp),
        ) {
            Text(
                text = "Вставить",
                style = TextStyle(
                    fontSize = if (compact) 16.sp else 18.sp,
                    fontWeight = FontWeight.Medium,
                    color = Color(0xFF0B6A43),
                ),
                maxLines = 1,
            )
        }
    }
}

@Composable
private fun ActivationCodeSlots(
    code: String,
    compact: Boolean,
) {
    val activeIndex = code.length.coerceIn(0, SKRYON_ACTIVATION_CODE_LENGTH - 1)

    BoxWithConstraints(modifier = Modifier.fillMaxWidth()) {
        val groupGap = if (compact) 7.dp else 9.dp
        val slotGap = if (compact) 3.dp else 4.dp
        val availableWidth = maxWidth.value - groupGap.value * 5f - slotGap.value * 5f
        val slotWidth = (availableWidth / SKRYON_ACTIVATION_CODE_LENGTH)
            .dp
            .coerceIn(if (compact) 12.dp else 14.dp, if (compact) 19.dp else 22.dp)

        var index = 0
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.Center,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            SKRYON_ACTIVATION_CODE_GROUPS.forEachIndexed { groupIndex, groupSize ->
                if (groupIndex > 0) {
                    Spacer(Modifier.width(groupGap))
                }
                Row(
                    horizontalArrangement = Arrangement.spacedBy(slotGap),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    repeat(groupSize) {
                        val slotIndex = index
                        ActivationCodeCharacter(
                            char = code.getOrNull(slotIndex)?.toString().orEmpty(),
                            active = slotIndex == activeIndex && code.length < SKRYON_ACTIVATION_CODE_LENGTH,
                            compact = compact,
                            modifier = Modifier.width(slotWidth),
                        )
                        index += 1
                    }
                }
            }
        }
    }
}

@Composable
private fun ActivationCodeCharacter(
    char: String,
    active: Boolean,
    compact: Boolean,
    modifier: Modifier = Modifier,
) {
    val filled = char.isNotBlank()
    val textScale by animateFloatAsState(
        targetValue = if (filled) 1f else 0.88f,
        animationSpec = tween(durationMillis = 140),
        label = "activation-line-character-scale",
    )
    val underlineColor by animateColorAsState(
        targetValue = when {
            active -> Color(0xFF0B6A43)
            filled -> Color(0xFF747B84)
            else -> Color(0xFFC8CDD3)
        },
        animationSpec = tween(durationMillis = 160),
        label = "activation-line-color",
    )
    val underlineHeight by animateDpAsState(
        targetValue = if (active) 2.dp else 1.5.dp,
        animationSpec = tween(durationMillis = 160),
        label = "activation-line-height",
    )
    val blink = rememberInfiniteTransition(label = "activation-line-cursor")
    val cursorAlpha by blink.animateFloat(
        initialValue = 0.22f,
        targetValue = 1f,
        animationSpec = infiniteRepeatable(
            animation = tween(durationMillis = 620),
            repeatMode = RepeatMode.Reverse,
        ),
        label = "activation-line-cursor-alpha",
    )

    Column(
        modifier = modifier.height(if (compact) 52.dp else 60.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Bottom,
    ) {
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .height(if (compact) 35.dp else 41.dp),
            contentAlignment = Alignment.Center,
        ) {
            if (filled) {
                Text(
                    text = char,
                    modifier = Modifier.scale(textScale),
                    style = TextStyle(
                        fontSize = if (compact) 19.sp else 22.sp,
                        lineHeight = if (compact) 23.sp else 26.sp,
                        fontWeight = FontWeight.Medium,
                        color = Color(0xFF111319),
                        textAlign = TextAlign.Center,
                    ),
                    maxLines = 1,
                )
            } else if (active) {
                Box(
                    modifier = Modifier
                        .width(1.8.dp)
                        .height(if (compact) 29.dp else 34.dp)
                        .alpha(cursorAlpha)
                        .clip(RoundedCornerShape(999.dp))
                        .background(Color(0xFF0B6A43)),
                )
            }
        }
        Spacer(Modifier.height(if (compact) 6.dp else 7.dp))
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .height(underlineHeight)
                .clip(RoundedCornerShape(999.dp))
                .background(underlineColor),
        )
    }
}

private fun sanitizeActivationCode(value: String): String {
    return value
        .uppercase(Locale.ROOT)
        .filter { it.isLetterOrDigit() }
        .take(SKRYON_ACTIVATION_CODE_LENGTH)
}

internal fun formatSkryonActivationCode(rawCode: String): String {
    var index = 0
    return SKRYON_ACTIVATION_CODE_GROUPS.mapNotNull { size ->
        val part = rawCode.drop(index).take(size)
        index += size
        part.takeIf { it.isNotBlank() }
    }.joinToString("-")
}

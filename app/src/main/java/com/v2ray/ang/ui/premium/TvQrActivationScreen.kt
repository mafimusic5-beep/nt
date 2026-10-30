package com.v2ray.ang.ui.premium

import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import com.v2ray.ang.R
import com.v2ray.ang.ui.premium.vpn.tvRemoteHighlight
import com.v2ray.ang.util.QRCodeDecoder
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive

/**
 * TV-first sign-in: scan QR on the phone, type the activation code on the
 * phone, and the TV executes its normal device-bound validation.
 * Regular remote code entry remains available as an explicit fallback.
 */
@Composable
internal fun TvQrActivationScreen(
    onActivated: suspend (String) -> SkryonActivationResult,
    onManualEntry: () -> Unit,
) {
    var revision by remember { mutableIntStateOf(0) }
    var session by remember(revision) { mutableStateOf<TvPairingClient.Session?>(null) }
    var status by remember(revision) { mutableStateOf("Создаём безопасный QR-код…") }
    var error by remember(revision) { mutableStateOf("") }
    var activating by remember(revision) { mutableStateOf(false) }

    LaunchedEffect(revision) {
        val current = try {
            TvPairingClient.start()
        } catch (_: Exception) {
            error = "Не удалось подключиться к серверу QR-активации"
            return@LaunchedEffect
        }
        session = current
        status = "Отсканируйте QR-код телефоном"
        while (isActive && System.currentTimeMillis() < current.expiresAt) {
            delay(2_500)
            val result = try {
                TvPairingClient.poll(current)
            } catch (_: Exception) {
                // Transient Wi-Fi loss must not discard a still-valid pairing.
                status = "Ожидаем интернет для активации…"
                continue
            }
            when (result) {
                TvPairingClient.PollResult.Waiting -> status = "Ожидаем ввод кода на телефоне…"
                TvPairingClient.PollResult.Expired -> {
                    error = "QR-код устарел. Обновите его."
                    return@LaunchedEffect
                }
                is TvPairingClient.PollResult.Received -> {
                    activating = true
                    status = "Проверяем код и привязываем телевизор…"
                    val activation = try {
                        onActivated(result.activationCode)
                    } catch (_: Exception) {
                        SkryonActivationResult(ok = false, error = "Ошибка активации")
                    }
                    if (!activation.ok) {
                        error = activation.error.ifBlank { "Не удалось активировать телевизор" }
                        activating = false
                    }
                    // Success navigates to the existing activation policy flow.
                    return@LaunchedEffect
                }
            }
        }
        if (isActive && !activating) error = "Время QR-кода истекло. Обновите его."
    }

    val qrBitmap = remember(session?.url) {
        session?.url?.let { QRCodeDecoder.createQRCode(it, 460)?.asImageBitmap() }
    }

    BoxWithConstraints(
        modifier = Modifier.fillMaxSize().background(Color.White),
        contentAlignment = Alignment.Center,
    ) {
        // Skryon's original panther stays on the activation screen.
        // It's decorative, beside (and behind) the QR form so it cannot
        // replace the QR or intercept remote focus / pointer clicks.
        Image(
            painter = painterResource(id = R.drawable.skryon_panther_activation),
            contentDescription = null,
            modifier = Modifier
                .align(Alignment.CenterEnd)
                .size(width = maxWidth * 0.40f, height = maxHeight * 0.82f)
                .alpha(0.55f),
            contentScale = ContentScale.Fit,
        )

        Text(
            text = "Skryon",
            modifier = Modifier.align(Alignment.TopStart).padding(start = 32.dp, top = 22.dp),
            style = MaterialTheme.typography.headlineLarge,
            fontWeight = FontWeight.Bold,
            color = Color(0xFF111319),
        )
        Column(
            modifier = Modifier.widthIn(max = 540.dp).padding(horizontal = 22.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.Center,
        ) {
            Text(
                text = "Вход через телефон",
                style = MaterialTheme.typography.headlineMedium,
                fontWeight = FontWeight.SemiBold,
                color = Color(0xFF111319),
            )
            Spacer(Modifier.height(12.dp))
            Text(
                text = "Наведите камеру телефона на QR-код и введите код активации.",
                textAlign = TextAlign.Center,
                style = MaterialTheme.typography.titleMedium,
                color = Color(0xFF535D6B),
            )
            Spacer(Modifier.height(18.dp))
            Box(
                modifier = Modifier
                    .size(242.dp)
                    .border(1.dp, Color(0xFFE6E9EE), RoundedCornerShape(18.dp))
                    .background(Color.White),
                contentAlignment = Alignment.Center,
            ) {
                if (qrBitmap != null) {
                    Image(
                        bitmap = qrBitmap,
                        contentDescription = "QR-код для активации телевизора",
                        modifier = Modifier.size(218.dp),
                    )
                } else {
                    CircularProgressIndicator(color = Color(0xFF176B43))
                }
            }
            Spacer(Modifier.height(14.dp))
            Text(
                text = error.ifBlank { status },
                style = MaterialTheme.typography.bodyLarge,
                textAlign = TextAlign.Center,
                color = if (error.isNotBlank()) Color(0xFFB42318) else Color(0xFF535D6B),
            )
            Spacer(Modifier.height(18.dp))
            if (error.isNotBlank()) {
                Button(
                    onClick = { revision++ },
                    colors = ButtonDefaults.buttonColors(containerColor = Color(0xFF111319)),
                    modifier = Modifier.tvRemoteHighlight(),
                ) { Text("Обновить QR-код") }
            }
            TextButton(
                onClick = onManualEntry,
                modifier = Modifier.tvRemoteHighlight(),
            ) {
                Text("Ввести код пультом", color = Color(0xFF111319))
            }
        }
    }
}

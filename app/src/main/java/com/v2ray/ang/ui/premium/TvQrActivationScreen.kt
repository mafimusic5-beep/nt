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
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
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
import com.v2ray.ang.util.QRCodeDecoder
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive

/**
 * TV-only activation via QR: a scoped coroutine automatically requests a new
 * one-time server ticket when the previous one expires after five minutes.
 * The site must be deployed before the TV can display a valid QR, since a
 * locally invented/offline QR would have no corresponding server session.
 */
@Composable
internal fun TvQrActivationScreen(
    onActivated: suspend (String) -> SkryonActivationResult,
) {
    var session by remember { mutableStateOf<TvPairingClient.Session?>(null) }
    var status by remember { mutableStateOf("Подключаем QR-активацию…") }
    var error by remember { mutableStateOf("") }
    var creating by remember { mutableStateOf(true) }
    var secondsRemaining by remember { mutableIntStateOf(0) }

    // Lifecycle-aware: retries stop when the TV screen is no longer visible.
    // A failed server request is retried automatically, with a fixed delay;
    // it must never become a permanent spinner with a fake/offline QR.
    LaunchedEffect(Unit) {
        while (isActive) {
            session = null
            secondsRemaining = 0
            error = ""
            creating = true
            status = "Готовим защищённый QR-код…"
            val active = try {
                TvPairingClient.start()
            } catch (cancelled: CancellationException) {
                throw cancelled
            } catch (_: Exception) {
                creating = false
                error = "Сервер QR-активации недоступен. Повторяем подключение автоматически…"
                delay(15_000)
                continue
            }

            session = active
            creating = false
            status = "Отсканируйте QR-код телефоном"
            var received = false
            while (isActive && !tvQrTicketExpired(active.expiresAt, System.currentTimeMillis())) {
                secondsRemaining = tvQrSecondsRemaining(active.expiresAt, System.currentTimeMillis())
                val polled = try {
                    TvPairingClient.poll(active)
                } catch (cancelled: CancellationException) {
                    throw cancelled
                } catch (_: Exception) {
                    status = "Соединение прервалось. Ожидаем восстановление интернета…"
                    delay(2_500)
                    continue
                }
                when (polled) {
                    TvPairingClient.PollResult.Waiting -> {
                        status = "Ожидаем код с телефона…"
                    }
                    TvPairingClient.PollResult.Expired -> break
                    is TvPairingClient.PollResult.Received -> {
                        received = true
                        session = null // Do not display a used/consumed QR ticket.
                        creating = true
                        status = "Проверяем код активации…"
                        val activation = try {
                            onActivated(polled.activationCode)
                        } catch (cancelled: CancellationException) {
                            throw cancelled
                        } catch (_: Exception) {
                            SkryonActivationResult(ok = false, error = "Ошибка активации")
                        }
                        creating = false
                        if (activation.ok) return@LaunchedEffect
                        error = activation.error.ifBlank { "Не удалось активировать телевизор" }
                        delay(4_000)
                        break // Generate a fresh ticket after a failed code.
                    }
                }
                if (isActive && !received) delay(2_500)
            }
            // QR expired, was consumed or activation failed. Request a fresh
            // server-side ticket immediately; never reuse a stale QR.
        }
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
                } else if (creating) {
                    CircularProgressIndicator(color = Color(0xFF176B43))
                } else {
                    Text(
                        text = "QR недоступен",
                        color = Color(0xFF7D828D),
                        style = MaterialTheme.typography.bodyMedium,
                    )
                }
            }
            Spacer(Modifier.height(14.dp))
            Text(
                text = error.ifBlank { status },
                style = MaterialTheme.typography.bodyLarge,
                textAlign = TextAlign.Center,
                color = if (error.isNotBlank()) Color(0xFFB42318) else Color(0xFF535D6B),
            )
            Spacer(Modifier.height(12.dp))
            Text(
                text = if (session != null) {
                    "QR автоматически обновится через " +
                        "%02d:%02d".format(secondsRemaining / 60, secondsRemaining % 60)
                } else {
                    "Новый QR появится автоматически после подключения к серверу"
                },
                style = MaterialTheme.typography.bodyMedium,
                textAlign = TextAlign.Center,
                color = Color(0xFF7D828D),
            )
        }
    }
}

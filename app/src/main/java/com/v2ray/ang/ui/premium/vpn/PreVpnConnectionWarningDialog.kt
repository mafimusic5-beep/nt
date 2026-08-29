package com.v2ray.ang.ui.premium.vpn

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.height
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.dp

@Composable
internal fun PreVpnConnectionWarningDialog(
    quality: PreVpnConnectionQuality,
    onContinue: () -> Unit,
    onRetry: () -> Unit,
    onDismiss: () -> Unit,
) {
    val copy = quality.warningCopy() ?: return

    AlertDialog(
        onDismissRequest = onDismiss,
        title = {
            Text(
                text = copy.title,
                fontWeight = FontWeight.SemiBold,
            )
        },
        text = {
            Column {
                Text(
                    text = buildAnnotatedString {
                        append("Качество интернета до VPN: ")
                        withStyle(
                            SpanStyle(
                                color = copy.statusColor,
                                fontWeight = FontWeight.Bold,
                            ),
                        ) {
                            append(copy.status)
                        }
                    },
                    style = MaterialTheme.typography.bodyMedium,
                )
                Spacer(Modifier.height(12.dp))
                Text(
                    text = copy.description,
                    style = MaterialTheme.typography.bodyLarge,
                )
            }
        },
        confirmButton = {
            Button(
                onClick = if (copy.canContinue) onContinue else onRetry,
                colors = ButtonDefaults.buttonColors(
                    containerColor = Color(0xFF111319),
                    contentColor = Color.White,
                ),
            ) {
                Text(if (copy.canContinue) "Подключить всё равно" else "Проверить снова")
            }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) {
                Text("Отмена", color = Color(0xFF111319))
            }
        },
    )
}

private data class ConnectionWarningCopy(
    val title: String,
    val status: String,
    val description: String,
    val statusColor: Color,
    val canContinue: Boolean,
)

private fun PreVpnConnectionQuality.warningCopy(): ConnectionWarningCopy? = when (this) {
    PreVpnConnectionQuality.Unstable -> ConnectionWarningCopy(
        title = "Слабая связь",
        status = "НЕСТАБИЛЬНОЕ",
        description =
            "Интернет работает нестабильно ещё до включения VPN. Подключение может быть медленным или не запуститься.",
        statusColor = Color(0xFFC77800),
        canContinue = true,
    )

    PreVpnConnectionQuality.Critical -> ConnectionWarningCopy(
        title = "Слабая связь",
        status = "КРИТИЧЕСКОЕ",
        description =
            "Интернет работает нестабильно ещё до включения VPN. Подключение может быть медленным или не запуститься.",
        statusColor = Color(0xFFC25B18),
        canContinue = true,
    )

    PreVpnConnectionQuality.Offline -> ConnectionWarningCopy(
        title = "Нет доступа к интернету",
        status = "ОТСУТСТВУЕТ",
        description =
            "Без подключения к интернету VPN не сможет запуститься. Проверьте Wi‑Fi или мобильную сеть и повторите.",
        statusColor = Color(0xFFB42318),
        canContinue = false,
    )

    PreVpnConnectionQuality.Good,
    PreVpnConnectionQuality.Unknown -> null
}

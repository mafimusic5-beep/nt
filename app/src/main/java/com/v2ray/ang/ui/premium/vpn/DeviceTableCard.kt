package com.v2ray.ang.ui.premium.vpn

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
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
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.v2ray.ang.handler.EmeryAccessManager
import com.v2ray.ang.handler.EmeryAccessProfile
import com.v2ray.ang.handler.EmeryDeviceRecord
import com.v2ray.ang.network.EmeryBackendClient
import kotlinx.coroutines.launch

@Composable
internal fun DeviceTableCard(
    compact: Boolean,
    tight: Boolean,
    modifier: Modifier = Modifier,
) {
    val scope = rememberCoroutineScope()
    var profile by remember { mutableStateOf(EmeryAccessManager.loadProfile()) }
    var loading by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf("") }

    fun refresh() {
        val accessKey = profile?.accessKey.orEmpty()
        if (accessKey.isBlank() || loading) return
        loading = true
        error = ""
        scope.launch {
            val result = EmeryBackendClient.fetchProfile(
                accessKey = accessKey,
                requireDeviceInventory = true,
            )
            result.fold(
                onSuccess = { refreshed ->
                    EmeryAccessManager.saveProfile(refreshed)
                    profile = refreshed
                },
                onFailure = { throwable ->
                    error = deviceTableError(throwable.message.orEmpty())
                },
            )
            loading = false
        }
    }

    LaunchedEffect(profile?.accessKey) {
        if (!profile?.accessKey.isNullOrBlank()) {
            refresh()
        }
    }

    val shape = RoundedCornerShape(if (compact) 18.dp else 22.dp)
    Column(
        modifier = modifier
            .fillMaxWidth()
            .background(Color.White.copy(alpha = 0.96f), shape)
            .border(1.dp, DeviceTableColors.Border, shape)
            .padding(
                horizontal = if (compact) 14.dp else 18.dp,
                vertical = if (tight) 14.dp else 18.dp,
            ),
    ) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text = "Устройства тарифа",
                    style = MaterialTheme.typography.titleMedium,
                    color = DeviceTableColors.TextPrimary,
                    fontWeight = FontWeight.SemiBold,
                )
                Spacer(Modifier.height(3.dp))
                Text(
                    text = profile?.planName?.ifBlank { "Тариф не определён" } ?: "Активация не найдена",
                    style = MaterialTheme.typography.bodySmall,
                    color = DeviceTableColors.TextSecondary,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
            }
            if (loading) {
                CircularProgressIndicator(
                    strokeWidth = 2.dp,
                    modifier = Modifier.size(20.dp),
                    color = DeviceTableColors.TextPrimary,
                )
            }
        }

        Spacer(Modifier.height(if (tight) 10.dp else 14.dp))

        val currentProfile = profile
        if (currentProfile == null) {
            Text(
                text = "Введите и подтвердите код доступа, чтобы увидеть зарегистрированные устройства.",
                style = MaterialTheme.typography.bodySmall,
                color = DeviceTableColors.TextSecondary,
            )
        } else {
            DeviceUsageSummary(currentProfile)
            Spacer(Modifier.height(10.dp))

            val devices = currentProfile.devices
                .sortedWith(
                    compareByDescending<EmeryDeviceRecord> { it.isCurrent }
                        .thenByDescending { it.active }
                        .thenBy { it.deviceName.lowercase() },
                )

            if (devices.isEmpty()) {
                Text(
                    text = "Сервер не вернул ни одного зарегистрированного устройства.",
                    style = MaterialTheme.typography.bodySmall,
                    color = DeviceTableColors.Error,
                )
            } else {
                DeviceTableHeader()
                devices.forEachIndexed { index, device ->
                    DeviceTableRow(device = device)
                    if (index != devices.lastIndex) {
                        Spacer(Modifier.height(6.dp))
                    }
                }
            }
        }

        if (error.isNotBlank()) {
            Spacer(Modifier.height(10.dp))
            Text(
                text = error,
                style = MaterialTheme.typography.bodySmall,
                color = DeviceTableColors.Error,
            )
        }

        Spacer(Modifier.height(if (tight) 10.dp else 14.dp))
        OutlinedButton(
            onClick = { refresh() },
            enabled = profile != null && !loading,
            modifier = Modifier.fillMaxWidth().height(if (tight) 46.dp else 50.dp),
            shape = RoundedCornerShape(16.dp),
            colors = ButtonDefaults.outlinedButtonColors(
                contentColor = DeviceTableColors.TextPrimary,
                disabledContentColor = DeviceTableColors.TextSecondary,
            ),
        ) {
            Text(if (loading) "Проверяем сервер…" else "Обновить таблицу")
        }
    }
}

@Composable
private fun DeviceUsageSummary(profile: EmeryAccessProfile) {
    val limit = profile.devicesLimit.coerceAtLeast(0)
    val used = profile.devicesUsed.coerceAtLeast(0)
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .background(DeviceTableColors.SelectedSurface, RoundedCornerShape(14.dp))
            .padding(horizontal = 12.dp, vertical = 10.dp),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            text = "Зарегистрировано",
            style = MaterialTheme.typography.bodyMedium,
            color = DeviceTableColors.TextPrimary,
            fontWeight = FontWeight.Medium,
        )
        Text(
            text = "$used из $limit",
            style = MaterialTheme.typography.titleMedium,
            color = if (limit > 0 && used >= limit) DeviceTableColors.Error else DeviceTableColors.Positive,
            fontWeight = FontWeight.SemiBold,
        )
    }
}

@Composable
private fun DeviceTableHeader() {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 10.dp, vertical = 5.dp),
    ) {
        Text(
            text = "Устройство",
            modifier = Modifier.weight(1.25f),
            style = MaterialTheme.typography.bodySmall,
            color = DeviceTableColors.TextSecondary,
            fontWeight = FontWeight.Medium,
        )
        Text(
            text = "Последняя активность",
            modifier = Modifier.weight(1f),
            style = MaterialTheme.typography.bodySmall,
            color = DeviceTableColors.TextSecondary,
            fontWeight = FontWeight.Medium,
        )
    }
}

@Composable
private fun DeviceTableRow(device: EmeryDeviceRecord) {
    val rowShape = RoundedCornerShape(14.dp)
    val background = when {
        device.isCurrent -> DeviceTableColors.SelectedSurface
        !device.active -> DeviceTableColors.DisabledSurface
        else -> Color.White
    }
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .background(background, rowShape)
            .border(1.dp, DeviceTableColors.Border.copy(alpha = 0.65f), rowShape)
            .padding(horizontal = 10.dp, vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(modifier = Modifier.weight(1.25f)) {
            Text(
                text = device.deviceName.ifBlank { "Устройство" },
                style = MaterialTheme.typography.bodyMedium,
                color = DeviceTableColors.TextPrimary,
                fontWeight = if (device.isCurrent) FontWeight.SemiBold else FontWeight.Medium,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            Spacer(Modifier.height(2.dp))
            Text(
                text = buildString {
                    append(device.platform.ifBlank { "unknown" })
                    if (device.appVersion.isNotBlank()) append(" • ${device.appVersion}")
                    if (device.isCurrent) append(" • это устройство")
                    if (!device.active) append(" • отключено")
                },
                style = MaterialTheme.typography.bodySmall,
                color = if (device.active) DeviceTableColors.TextSecondary else DeviceTableColors.Error,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            Spacer(Modifier.height(2.dp))
            Text(
                text = device.deviceId.take(8).ifBlank { "—" },
                style = MaterialTheme.typography.bodySmall,
                color = DeviceTableColors.TextSecondary.copy(alpha = 0.72f),
                maxLines = 1,
            )
        }
        Text(
            text = displayDeviceTime(device.lastSeenAt.ifBlank { device.firstSeenAt }),
            modifier = Modifier.weight(1f),
            style = MaterialTheme.typography.bodySmall,
            color = DeviceTableColors.TextSecondary,
            maxLines = 2,
            overflow = TextOverflow.Ellipsis,
        )
    }
}

private fun displayDeviceTime(value: String): String {
    val cleaned = value.trim()
    if (cleaned.isBlank()) return "нет данных"
    return cleaned
        .replace('T', ' ')
        .removeSuffix("Z")
        .take(16)
}

private fun deviceTableError(reason: String): String {
    return when (reason) {
        "device_inventory_missing" -> "Backend не вернул таблицу устройств. Обновите серверный API."
        "device_inventory_mismatch" -> "Текущее устройство отсутствует в серверной таблице."
        "device_confirmation_missing" -> "Backend не подтвердил идентификатор текущего устройства."
        "device_mismatch" -> "Backend вернул другой идентификатор устройства."
        "device_counter_missing", "device_counter_mismatch" -> "Backend не подтвердил счётчик устройств."
        "plan_limit_mismatch" -> "Серверный лимит не соответствует тарифу."
        "network" -> "Не удалось обновить таблицу устройств: нет соединения с сервером."
        else -> "Не удалось подтвердить список устройств на сервере."
    }
}

private object DeviceTableColors {
    val Border = Color(0xFFE6E9EE)
    val TextPrimary = Color(0xFF111319)
    val TextSecondary = Color(0xFF7D828D)
    val SelectedSurface = Color(0xFFF4F6F8)
    val DisabledSurface = Color(0xFFF6F6F7)
    val Positive = Color(0xFF36A852)
    val Error = Color(0xFFB42318)
}

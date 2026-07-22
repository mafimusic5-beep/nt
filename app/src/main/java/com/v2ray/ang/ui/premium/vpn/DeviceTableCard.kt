package com.v2ray.ang.ui.premium.vpn

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
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
            EmeryBackendClient.fetchProfile(
                accessKey = accessKey,
                requireDeviceInventory = true,
            ).fold(
                onSuccess = { refreshed ->
                    EmeryAccessManager.saveProfile(refreshed)
                    profile = refreshed
                },
                onFailure = {
                    error = "Не удалось обновить данные. Попробуйте ещё раз позже."
                },
            )
            loading = false
        }
    }

    val shape = RoundedCornerShape(if (compact) 18.dp else 22.dp)
    Column(
        modifier = modifier
            .fillMaxWidth()
            .background(Color.White.copy(alpha = 0.96f), shape)
            .border(1.dp, DeviceCardColors.Border, shape)
            .padding(
                horizontal = if (compact) 14.dp else 18.dp,
                vertical = if (tight) 14.dp else 18.dp,
            ),
    ) {
        Text(
            text = "Устройства тарифа",
            style = MaterialTheme.typography.titleMedium,
            color = DeviceCardColors.TextPrimary,
            fontWeight = FontWeight.SemiBold,
        )
        Spacer(Modifier.height(3.dp))
        Text(
            text = profile?.planName?.ifBlank { "Тариф не определён" } ?: "Активация не найдена",
            style = MaterialTheme.typography.bodySmall,
            color = DeviceCardColors.TextSecondary,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
        )

        Spacer(Modifier.height(if (tight) 10.dp else 14.dp))

        val currentProfile = profile
        if (currentProfile == null) {
            Text(
                text = "Введите и подтвердите код доступа, чтобы увидеть устройства тарифа.",
                style = MaterialTheme.typography.bodySmall,
                color = DeviceCardColors.TextSecondary,
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
                .ifEmpty {
                    listOf(
                        EmeryDeviceRecord(
                            deviceId = currentProfile.deviceId,
                            deviceName = currentProfile.deviceName,
                            active = true,
                            isCurrent = true,
                        )
                    )
                }

            devices.forEachIndexed { index, device ->
                DeviceCard(device = device)
                if (index != devices.lastIndex) {
                    Spacer(Modifier.height(8.dp))
                }
            }
        }

        if (error.isNotBlank()) {
            Spacer(Modifier.height(10.dp))
            Text(
                text = error,
                style = MaterialTheme.typography.bodySmall,
                color = DeviceCardColors.Error,
            )
        }

        Spacer(Modifier.height(if (tight) 10.dp else 14.dp))
        OutlinedButton(
            onClick = { refresh() },
            enabled = profile != null && !loading,
            modifier = Modifier.fillMaxWidth().height(if (tight) 46.dp else 50.dp),
            shape = RoundedCornerShape(16.dp),
            colors = ButtonDefaults.outlinedButtonColors(
                contentColor = DeviceCardColors.TextPrimary,
                disabledContentColor = DeviceCardColors.TextSecondary,
            ),
        ) {
            Text("Обновить устройства")
        }
    }
}

@Composable
private fun DeviceUsageSummary(profile: EmeryAccessProfile) {
    val limit = profile.devicesLimit.coerceAtLeast(0)
    val used = profile.devicesUsed.coerceIn(0, limit.coerceAtLeast(profile.devicesUsed))
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .background(DeviceCardColors.SelectedSurface, RoundedCornerShape(14.dp))
            .padding(horizontal = 12.dp, vertical = 10.dp),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            text = "Используется устройств",
            style = MaterialTheme.typography.bodyMedium,
            color = DeviceCardColors.TextPrimary,
            fontWeight = FontWeight.Medium,
        )
        Text(
            text = if (limit > 0) "$used из $limit" else "$used",
            style = MaterialTheme.typography.titleMedium,
            color = DeviceCardColors.Positive,
            fontWeight = FontWeight.SemiBold,
        )
    }
}

@Composable
private fun DeviceCard(device: EmeryDeviceRecord) {
    val shape = RoundedCornerShape(16.dp)
    val background = if (device.isCurrent) DeviceCardColors.SelectedSurface else Color.White
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .background(background, shape)
            .border(1.dp, DeviceCardColors.Border.copy(alpha = 0.75f), shape)
            .padding(horizontal = 12.dp, vertical = 12.dp),
        verticalAlignment = Alignment.Top,
    ) {
        Box(
            modifier = Modifier
                .padding(top = 5.dp)
                .size(9.dp)
                .background(
                    color = if (device.active) DeviceCardColors.Positive else DeviceCardColors.Disabled,
                    shape = CircleShape,
                ),
        )
        Spacer(Modifier.size(10.dp))
        Column(modifier = Modifier.weight(1f)) {
            Text(
                text = displayDeviceName(device),
                style = MaterialTheme.typography.bodyLarge,
                color = DeviceCardColors.TextPrimary,
                fontWeight = if (device.isCurrent) FontWeight.SemiBold else FontWeight.Medium,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            Spacer(Modifier.height(3.dp))
            Text(
                text = if (device.active) "Активно" else "Отключено",
                style = MaterialTheme.typography.bodySmall,
                color = if (device.active) DeviceCardColors.Positive else DeviceCardColors.Error,
            )
            Spacer(Modifier.height(3.dp))
            Text(
                text = displayLastActivity(device),
                style = MaterialTheme.typography.bodySmall,
                color = DeviceCardColors.TextSecondary,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
        }
    }
}

private fun displayDeviceName(device: EmeryDeviceRecord): String {
    if (device.isCurrent) return "Это устройство"
    val value = device.deviceName.trim()
    if (value.isBlank()) return "Android-устройство"
    val lower = value.lowercase()
    val technical = listOf(
        "sdk_gphone",
        "google sdk",
        "android sdk built for",
        "generic_x86",
        "generic x86",
        "x86_64",
        "arm64-v8a",
        "emulator",
    ).any(lower::contains)
    return if (technical) "Android-устройство" else value.take(40)
}

private fun displayLastActivity(device: EmeryDeviceRecord): String {
    val raw = device.lastSeenAt.ifBlank { device.firstSeenAt }.trim()
    if (raw.isBlank()) {
        return if (device.isCurrent && device.active) "Активно сейчас" else "Нет данных об активности"
    }
    val time = raw
        .replace('T', ' ')
        .removeSuffix("Z")
        .take(16)
    return "Последняя активность: $time"
}

private object DeviceCardColors {
    val Border = Color(0xFFE6E9EE)
    val TextPrimary = Color(0xFF111319)
    val TextSecondary = Color(0xFF7D828D)
    val SelectedSurface = Color(0xFFF4F6F8)
    val Positive = Color(0xFF36A852)
    val Disabled = Color(0xFFA8ADB7)
    val Error = Color(0xFFB42318)
}

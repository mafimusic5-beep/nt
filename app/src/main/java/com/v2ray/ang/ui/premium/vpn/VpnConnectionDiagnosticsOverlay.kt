package com.v2ray.ang.ui.premium.vpn

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

@Composable
fun VpnConnectionDiagnosticsOverlay(
    modifier: Modifier = Modifier,
) {
    val events by VpnUiDebugLogger.events.collectAsState()
    var expanded by remember { mutableStateOf(true) }
    val visibleEvents = events.takeLast(6)
    val shape = RoundedCornerShape(14.dp)

    Column(
        modifier = modifier
            .fillMaxWidth()
            .clip(shape)
            .background(Color(0xEE111319))
            .border(1.dp, Color.White.copy(alpha = 0.16f), shape)
            .clickable { expanded = !expanded }
            .padding(horizontal = 12.dp, vertical = 10.dp),
    ) {
        Row(
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                text = "Логи VPN",
                color = Color.White,
                style = MaterialTheme.typography.bodyMedium,
                fontWeight = FontWeight.SemiBold,
            )
            Spacer(Modifier.weight(1f))
            Text(
                text = if (expanded) "Свернуть" else "Развернуть",
                color = Color.White.copy(alpha = 0.72f),
                style = MaterialTheme.typography.bodySmall,
            )
        }

        if (expanded) {
            Text(
                text = "Полный лог: vpn-connection.log",
                color = Color.White.copy(alpha = 0.58f),
                fontSize = 10.sp,
                modifier = Modifier.padding(top = 4.dp, bottom = 5.dp),
            )
            if (visibleEvents.isEmpty()) {
                Text(
                    text = "Диагностика готова. События подключения появятся здесь.",
                    color = Color.White.copy(alpha = 0.78f),
                    fontSize = 11.sp,
                )
            } else {
                visibleEvents.forEach { line ->
                    Row(
                        modifier = Modifier.fillMaxWidth().padding(vertical = 1.dp),
                        verticalAlignment = Alignment.Top,
                    ) {
                        Text(
                            text = "›",
                            color = Color(0xFF8ED36D),
                            fontSize = 11.sp,
                            fontFamily = FontFamily.Monospace,
                        )
                        Spacer(Modifier.width(5.dp))
                        Text(
                            text = line,
                            color = Color.White.copy(alpha = 0.90f),
                            fontSize = 10.sp,
                            lineHeight = 13.sp,
                            fontFamily = FontFamily.Monospace,
                            maxLines = 2,
                            overflow = TextOverflow.Ellipsis,
                        )
                    }
                }
            }
        }
    }
}

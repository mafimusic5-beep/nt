package com.v2ray.ang.ui.premium

import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.v2ray.ang.BuildConfig

internal fun developerActivationLogsEnabled(): Boolean = BuildConfig.DEBUG

@Composable
internal fun DeveloperActivationLogs(
    diagnostic: SkryonActivationDiagnostic?,
    modifier: Modifier = Modifier,
) {
    if (!BuildConfig.DEBUG || diagnostic == null) {
        return
    }

    var open by remember(diagnostic) { mutableStateOf(false) }

    Button(
        onClick = { open = true },
        modifier = modifier,
        colors = ButtonDefaults.buttonColors(
            containerColor = Color(0xFFF1F2F4),
            contentColor = Color(0xFF111319),
        ),
    ) {
        Text(
            text = "Логи",
            style = TextStyle(fontSize = 13.sp),
        )
    }

    if (open) {
        AlertDialog(
            onDismissRequest = { open = false },
            title = {
                Text("Логи разработчика")
            },
            text = {
                SelectionContainer {
                    Text(
                        text = diagnostic.asText(),
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(vertical = 4.dp),
                        style = TextStyle(
                            fontSize = 12.sp,
                            lineHeight = 17.sp,
                            fontFamily = FontFamily.Monospace,
                            color = Color(0xFF30343B),
                        ),
                    )
                }
            },
            confirmButton = {
                TextButton(onClick = { open = false }) {
                    Text("Закрыть")
                }
            },
        )
    }
}

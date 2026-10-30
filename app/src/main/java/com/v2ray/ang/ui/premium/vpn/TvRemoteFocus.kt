package com.v2ray.ang.ui.premium.vpn

import android.content.pm.PackageManager
import android.content.res.Configuration
import androidx.compose.foundation.border
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.drawWithContent
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.Shape
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp

/**
 * TV remotes send DPAD/OK, not touch gestures. Compose clickable controls
 * already receive DPAD clicks when focused; the visible green focus ring and
 * tiny pointer tell the viewer where the next OK press will click.
 *
 * This does NOT synthesize touch events, steal DPAD keys from the TV keyboard,
 * or alter native USB/Bluetooth mouse and touch behaviour.
 */
@Composable
internal fun rememberIsSkryonTv(): Boolean {
    val context = LocalContext.current
    return remember(context) {
        val mode = context.resources.configuration.uiMode and Configuration.UI_MODE_TYPE_MASK
        mode == Configuration.UI_MODE_TYPE_TELEVISION ||
            context.packageManager.hasSystemFeature(PackageManager.FEATURE_LEANBACK)
    }
}

@Composable
internal fun Modifier.tvRemoteFocusOutline(
    shape: Shape = RoundedCornerShape(18.dp),
): Modifier {
    if (!rememberIsSkryonTv()) return this

    var focused by remember { mutableStateOf(false) }
    return this
        .onFocusChanged { focused = it.hasFocus }
        .then(
            if (focused) {
                Modifier
                    .border(3.dp, Color(0xFF176B43), shape)
                    .drawWithContent {
                        drawContent()
                        // Mouse-like indicator follows the selected control.
                        val x = 7.dp.toPx()
                        val y = 6.dp.toPx()
                        val arrow = Path().apply {
                            moveTo(x, y)
                            lineTo(x, y + 18.dp.toPx())
                            lineTo(x + 5.dp.toPx(), y + 13.dp.toPx())
                            lineTo(x + 10.dp.toPx(), y + 23.dp.toPx())
                            lineTo(x + 14.dp.toPx(), y + 21.dp.toPx())
                            lineTo(x + 9.dp.toPx(), y + 12.dp.toPx())
                            lineTo(x + 16.dp.toPx(), y + 12.dp.toPx())
                            close()
                        }
                        drawPath(arrow, Color(0xFF176B43))
                        drawPath(arrow, Color.White, style = Stroke(width = 1.dp.toPx()))
                    }
            } else {
                Modifier
            },
        )
}

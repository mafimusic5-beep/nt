package com.v2ray.ang.ui.premium.vpn

import android.content.Context
import android.content.pm.PackageManager
import androidx.compose.foundation.border
import androidx.compose.foundation.relocation.BringIntoViewRequester
import androidx.compose.foundation.relocation.bringIntoViewRequester
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.composed
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Shape
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.launch

/** Android TV / Google TV: DPAD is the default pointer, a touchscreen is optional. */
internal fun isSkryonTelevision(context: Context): Boolean =
    context.packageManager.hasSystemFeature(PackageManager.FEATURE_LEANBACK) ||
        context.packageManager.hasSystemFeature(PackageManager.FEATURE_TELEVISION)

/**
 * Keep Compose's native DPAD/Enter handling on the clickable/Button/TextField.
 * This modifier only makes focus obvious and scrolls the focused control into
 * view on long TV settings screens, without injecting synthetic mouse clicks.
 */
internal fun Modifier.tvRemoteHighlight(
    shape: Shape = RoundedCornerShape(14.dp),
): Modifier = composed {
    val context = LocalContext.current
    val isTelevision = remember(context) { isSkryonTelevision(context) }
    if (!isTelevision) {
        this
    } else {
        var focused by remember { mutableStateOf(false) }
        val requester = remember { BringIntoViewRequester() }
        val scope = rememberCoroutineScope()
        this
            .bringIntoViewRequester(requester)
            .onFocusChanged { state ->
                focused = state.isFocused
                if (state.isFocused) {
                    scope.launch { requester.bringIntoView() }
                }
            }
            .border(
                width = if (focused) 3.dp else 0.dp,
                color = if (focused) Color(0xFF176B43) else Color.Transparent,
                shape = shape,
            )
    }
}

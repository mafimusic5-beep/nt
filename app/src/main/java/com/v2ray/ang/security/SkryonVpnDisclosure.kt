package com.v2ray.ang.security

import android.app.Activity

object SkryonVpnDisclosure {

    @Suppress("UNUSED_PARAMETER")
    fun showIfNeeded(
        activity: Activity,
        onAccepted: () -> Unit,
        onDeclined: () -> Unit = {},
    ) {
        onAccepted()
    }
}

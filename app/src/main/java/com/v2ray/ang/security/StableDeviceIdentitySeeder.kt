package com.v2ray.ang.security

import android.content.Context

object StableDeviceIdentitySeeder {
    /**
     * Preserve an already stored dp1 identifier during an app update. For a new
     * installation EmeryDeviceIdentity creates a random identifier that is not
     * derived from ANDROID_ID or other hardware data.
     */
    fun seedIfMissing(@Suppress("UNUSED_PARAMETER") context: Context) {
        EmeryDeviceIdentity.deviceId()
    }
}

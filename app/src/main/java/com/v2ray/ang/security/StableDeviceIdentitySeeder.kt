package com.v2ray.ang.security

import android.content.Context
import android.provider.Settings
import com.v2ray.ang.handler.MmkvManager
import java.security.MessageDigest
import java.util.Locale

private const val STABLE_DEVICE_PREF = "pref_emery_device_id"
private const val STABLE_DEVICE_PREFIX = "dp1:"

object StableDeviceIdentitySeeder {
    fun seedIfMissing(context: Context) {
        val existing = MmkvManager.decodeSettingsString(STABLE_DEVICE_PREF)?.trim().orEmpty()
        if (existing.startsWith(STABLE_DEVICE_PREFIX) && existing.length == 68) {
            return
        }

        val androidId = try {
            Settings.Secure.getString(context.contentResolver, Settings.Secure.ANDROID_ID)
                ?.trim()
                ?.takeIf { it.isNotEmpty() && it != "9774d56d682e549c" }
        } catch (_: Exception) {
            null
        } ?: return

        val resolved = STABLE_DEVICE_PREFIX + sha256Hex("skryon-device-v2:$androidId")
        MmkvManager.encodeSettings(STABLE_DEVICE_PREF, resolved)
    }

    private fun sha256Hex(value: String): String {
        val digest = MessageDigest.getInstance("SHA-256")
            .digest(value.toByteArray(Charsets.UTF_8))
        return digest.joinToString(separator = "") { byte ->
            String.format(Locale.US, "%02x", byte.toInt() and 0xff)
        }
    }
}

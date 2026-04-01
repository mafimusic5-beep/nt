package com.v2ray.ang.handler

import com.v2ray.ang.AppConfig
import com.v2ray.ang.BuildConfig

object EmeryApiConfig {

    fun baseUrl(): String {
        val saved = MmkvManager.decodeSettingsString(AppConfig.PREF_EMERY_API_BASE_URL)
        return if (!saved.isNullOrBlank()) normalize(saved) else normalize(BuildConfig.EMERY_API_BASE_URL)
    }

    fun saveBaseUrl(raw: String) {
        MmkvManager.encodeSettings(AppConfig.PREF_EMERY_API_BASE_URL, normalize(raw))
    }

    fun normalize(raw: String): String {
        var url = raw.trim()
        if (url.isNotEmpty() && !url.startsWith("http://") && !url.startsWith("https://")) {
            url = "http://$url"
        }
        return url.trimEnd('/')
    }
}

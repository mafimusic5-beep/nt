package com.v2ray.ang.handler

import com.v2ray.ang.AppConfig
import com.v2ray.ang.BuildConfig

object EmeryApiConfig {

    private const val DEFAULT_BASE_URL = "https://skryon.ru"

    fun baseUrl(): String {
        if (!BuildConfig.DEBUG) return DEFAULT_BASE_URL
        val saved = MmkvManager.decodeSettingsString(AppConfig.PREF_EMERY_API_BASE_URL)
        return normalize(saved ?: BuildConfig.EMERY_API_BASE_URL)
    }

    fun saveBaseUrl(raw: String) {
        if (BuildConfig.DEBUG) {
            MmkvManager.encodeSettings(AppConfig.PREF_EMERY_API_BASE_URL, normalize(raw))
        }
    }

    fun normalize(raw: String): String {
        val url = raw.trim().trimEnd('/')
        if (url.isEmpty()) return DEFAULT_BASE_URL
        if (!BuildConfig.DEBUG && url != DEFAULT_BASE_URL) return DEFAULT_BASE_URL
        return when {
            url.startsWith("https://") -> url
            BuildConfig.DEBUG && !url.contains("://") -> "https://$url"
            else -> DEFAULT_BASE_URL
        }
    }
}

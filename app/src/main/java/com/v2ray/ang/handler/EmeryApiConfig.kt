package com.v2ray.ang.handler

import com.v2ray.ang.AppConfig
import com.v2ray.ang.BuildConfig

object EmeryApiConfig {

    private const val DEFAULT_BASE_URL = "https://skryon.ru"
    private val LEGACY_BASE_URLS = setOf(
        "http://80.71.159.221:9330",
        "http://skryon.ru:9330",
        "https://skryon.ru:9330",
    )

    fun baseUrl(): String {
        val saved = MmkvManager.decodeSettingsString(AppConfig.PREF_EMERY_API_BASE_URL)
        val raw = if (saved.isNullOrBlank()) BuildConfig.EMERY_API_BASE_URL else saved
        val normalized = normalize(raw)
        return if (normalized in LEGACY_BASE_URLS) DEFAULT_BASE_URL else normalized
    }

    fun saveBaseUrl(raw: String) {
        val normalized = normalize(raw)
        MmkvManager.encodeSettings(
            AppConfig.PREF_EMERY_API_BASE_URL,
            if (normalized in LEGACY_BASE_URLS) DEFAULT_BASE_URL else normalized,
        )
    }

    fun normalize(raw: String): String {
        var url = raw.trim()
        if (url.isNotEmpty() && !url.startsWith("http://") && !url.startsWith("https://")) {
            url = "https://$url"
        }
        return url.trimEnd('/')
    }
}

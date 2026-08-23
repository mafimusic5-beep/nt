package com.v2ray.ang.handler

import com.v2ray.ang.AppConfig
import com.v2ray.ang.BuildConfig

object EmeryApiConfig {

    private const val DEFAULT_BASE_URL = "https://skryon.ru"
    private const val LEGACY_BASE_URL = "http://80.71.159.221:9330"
    private const val LEGACY_DOMAIN_BASE_URL = "http://skryon.ru:9330"

    fun baseUrl(): String {
        val saved = MmkvManager.decodeSettingsString(AppConfig.PREF_EMERY_API_BASE_URL)
        val raw = when {
            saved.isNullOrBlank() -> BuildConfig.EMERY_API_BASE_URL
            normalize(saved) in setOf(LEGACY_BASE_URL, LEGACY_DOMAIN_BASE_URL) -> DEFAULT_BASE_URL
            else -> saved
        }
        val normalized = normalize(raw)
        return if (normalized in setOf(LEGACY_BASE_URL, LEGACY_DOMAIN_BASE_URL)) DEFAULT_BASE_URL else normalized
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

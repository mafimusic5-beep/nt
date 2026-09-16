package com.v2ray.ang.handler

import com.v2ray.ang.AppConfig
import com.v2ray.ang.BuildConfig

object EmeryApiConfig {

    private const val DEFAULT_BASE_URL = "https://skryon.ru"
    private const val LEGACY_BASE_URL = "http://80.71.159.221:9330"
    private const val LEGACY_DOMAIN_BASE_URL = "http://skryon.ru:9330"

    fun baseUrl(): String {
        // Release traffic must stay on the host protected by Android's TLS pin-set.
        // Custom endpoints remain available only in debug builds for local development.
        if (!BuildConfig.DEBUG) return DEFAULT_BASE_URL

        val saved = MmkvManager.decodeSettingsString(AppConfig.PREF_EMERY_API_BASE_URL)
        val raw = when {
            saved.isNullOrBlank() -> BuildConfig.EMERY_API_BASE_URL
            normalize(saved) in setOf(LEGACY_BASE_URL, LEGACY_DOMAIN_BASE_URL) -> DEFAULT_BASE_URL
            else -> saved
        }
        val normalized = normalize(raw)
        if (normalized in setOf(LEGACY_BASE_URL, LEGACY_DOMAIN_BASE_URL)) return DEFAULT_BASE_URL
        return normalized
    }

    fun saveBaseUrl(raw: String) {
        if (!BuildConfig.DEBUG) {
            MmkvManager.encodeSettings(AppConfig.PREF_EMERY_API_BASE_URL, DEFAULT_BASE_URL)
            return
        }

        val normalized = normalize(raw)
        val safe = if (normalized in setOf(LEGACY_BASE_URL, LEGACY_DOMAIN_BASE_URL)) {
            DEFAULT_BASE_URL
        } else {
            normalized
        }
        MmkvManager.encodeSettings(AppConfig.PREF_EMERY_API_BASE_URL, safe)
    }

    fun normalize(raw: String): String {
        var url = raw.trim()
        if (url.isNotEmpty() && !url.startsWith("http://") && !url.startsWith("https://")) {
            url = if (BuildConfig.DEBUG) "http://$url" else "https://$url"
        }
        return url.trimEnd('/')
    }
}

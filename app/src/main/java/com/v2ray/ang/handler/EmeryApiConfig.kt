package com.v2ray.ang.handler

import com.v2ray.ang.AppConfig
import java.net.URI

object EmeryApiConfig {

    /**
     * SKRYON_ACTIVATION_ENDPOINT_PIN_V1
     *
     * Activation must always go through the public Cloudflare/Nginx endpoint.
     * Do not point the Android app at backend VPS IPs, old ports, or direct origins:
     * - backend API is private behind the site SSH tunnel;
     * - direct IPs may be blocked by firewall/Cloudflare origin rules;
     * - users can keep legacy MMKV values from older APKs.
     */
    private const val DEFAULT_BASE_URL = "https://skryon.ru"
    private const val DEFAULT_HOST = "skryon.ru"

    private val LEGACY_BASE_URLS = setOf(
        "http://80.71.159.221",
        "http://80.71.159.221:9330",
        "http://157.22.206.113",
        "https://157.22.206.113",
        "http://31.70.76.155",
        "http://31.70.76.155:8080",
        "https://31.70.76.155",
        "http://skryon.ru:9330",
        "https://skryon.ru:9330",
    )

    fun baseUrl(): String {
        val saved = MmkvManager.decodeSettingsString(AppConfig.PREF_EMERY_API_BASE_URL)
        val normalized = normalize(saved.orEmpty())
        val pinned = pinnedPublicBaseUrl(normalized)

        if (pinned != normalized) {
            MmkvManager.encodeSettings(AppConfig.PREF_EMERY_API_BASE_URL, pinned)
        }

        return pinned
    }

    fun saveBaseUrl(raw: String) {
        MmkvManager.encodeSettings(
            AppConfig.PREF_EMERY_API_BASE_URL,
            pinnedPublicBaseUrl(normalize(raw)),
        )
    }

    fun normalize(raw: String): String {
        var url = raw.trim()
        if (url.isEmpty()) {
            return DEFAULT_BASE_URL
        }
        if (!url.startsWith("http://") && !url.startsWith("https://")) {
            url = "https://$url"
        }
        return url.trimEnd('/')
    }

    private fun pinnedPublicBaseUrl(normalized: String): String {
        if (normalized.isBlank() || normalized in LEGACY_BASE_URLS) {
            return DEFAULT_BASE_URL
        }

        val uri = runCatching { URI(normalized) }.getOrNull() ?: return DEFAULT_BASE_URL
        val scheme = uri.scheme.orEmpty().lowercase()
        val host = uri.host.orEmpty().lowercase()

        // Hard pin: only https://skryon.ru is valid for activation.
        // Any port, path, IP, http scheme, or custom host is reset automatically.
        return if (scheme == "https" && host == DEFAULT_HOST && uri.port == -1 && uri.rawPath.isNullOrBlank()) {
            DEFAULT_BASE_URL
        } else {
            DEFAULT_BASE_URL
        }
    }
}

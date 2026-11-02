package com.v2ray.ang.ui.premium.vpn

import com.v2ray.ang.handler.MmkvManager
import org.json.JSONArray
import org.json.JSONObject

/**
 * Instant offline-safe list of PUBLIC region names and IDs only.
 *
 * We deliberately do not persist availability or transport credentials.
 * After restarting the app, a cached region is always CHECKING until the
 * server sends a fresh availability snapshot. A cached name cannot authorize
 * a VPN connection.
 */
internal object VpnRegionDirectoryCache {
    private const val PREF_KEY = "skryon_vpn_public_region_directory_v1"
    private const val MAX_ROWS = 100

    fun load(): List<VpnLocationOption> = runCatching {
        val raw = MmkvManager.decodeSettingsString(PREF_KEY, "").orEmpty()
        if (raw.isBlank()) return@runCatching emptyList()
        val entries = JSONArray(raw)
        (0 until minOf(entries.length(), MAX_ROWS))
            .mapNotNull { index ->
                val item = entries.optJSONObject(index) ?: return@mapNotNull null
                val id = item.optString("id").toLongOrNull()
                    ?.takeIf { it > 0L } ?: return@mapNotNull null
                val title = item.optString("title").trim()
                    .takeIf { it.isNotBlank() && it.length <= 120 }
                    ?: return@mapNotNull null
                VpnLocationOption(
                    id = id.toString(),
                    title = title,
                    availability = VpnRegionAvailability.CHECKING,
                )
            }
            .distinctBy { it.id }
    }.getOrDefault(emptyList())

    fun save(locations: List<VpnLocationOption>) {
        val snapshot = JSONArray()
        locations.asSequence()
            .filter { it.id.toLongOrNull()?.let { id -> id > 0L } == true }
            .take(MAX_ROWS)
            .forEach { region ->
                snapshot.put(
                    JSONObject()
                        .put("id", region.id)
                        .put("title", region.title.take(120)),
                )
            }
        MmkvManager.encodeSettings(PREF_KEY, snapshot.toString())
    }
}

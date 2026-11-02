package com.v2ray.ang.ui.premium.vpn

import com.v2ray.ang.handler.MmkvManager

/**
 * A display-only snapshot of the public region catalog.
 *
 * Loading a saved region never restores a previous "online" indication:
 * availability is unknown until a fresh /api/vpn/servers response arrives.
 * This file contains IDs and names only, never access keys or tunnel configs.
 */
internal object VpnRegionCatalogCache {
    private const val PREF_REGIONS = "SKRYON_PUBLIC_REGION_CATALOG_V1"

    fun read(): List<VpnLocationOption> =
        parseSavedRegionCatalog(MmkvManager.decodeSettingsString(PREF_REGIONS, "").orEmpty())

    fun write(regions: List<VpnLocationOption>) {
        MmkvManager.encodeSettings(PREF_REGIONS, serializeRegionCatalog(regions))
    }
}

internal fun serializeRegionCatalog(regions: List<VpnLocationOption>): String =
    regions.asSequence()
        .filter { it.id.toLongOrNull()?.let { id -> id > 0L } == true }
        .distinctBy { it.id }
        .take(100)
        .joinToString("\n") { region ->
            val cleanTitle = region.title.replace('\t', ' ').replace('\r', ' ')
                .replace('\n', ' ').trim().take(96)
            "${region.id}\t$cleanTitle"
        }

internal fun parseSavedRegionCatalog(raw: String): List<VpnLocationOption> =
    raw.lineSequence()
        .take(100)
        .mapNotNull { line ->
            val fields = line.split('\t', limit = 2)
            if (fields.size != 2 || fields[0].toLongOrNull()?.let { it > 0L } != true) {
                return@mapNotNull null
            }
            val title = fields[1].trim().take(96)
            if (title.isBlank()) return@mapNotNull null
            // IMPORTANT: the last observed availability must NOT be reused.
            VpnLocationOption(id = fields[0], title = title, isAvailable = null)
        }
        .distinctBy { it.id }
        .toList()

internal fun availableRegionIds(regions: List<VpnLocationOption>): List<Long> =
    regions.asSequence()
        .filter { it.isAvailable == true }
        .mapNotNull { it.id.toLongOrNull()?.takeIf { id -> id > 0L } }
        .distinct()
        .toList()

package com.v2ray.ang.handler

import kotlin.random.Random

/**
 * Saved *manual* choice, not the most recently assigned VPN server.
 *
 * A random auto-connection must never persist its assigned server as a user
 * preference. Manual selection remains pinned until the user selects Random.
 */
internal object SkryonRegionPreference {
    const val RANDOM_REGION_ID = "random"
    private const val PINNED_REGION_KEY = "SKRYON_USER_PINNED_REGION_ID"

    fun pinnedServerId(): Long? =
        MmkvManager.decodeSettingsLong(PINNED_REGION_KEY, -1L).takeIf { it > 0L }

    fun isRandom(): Boolean = pinnedServerId() == null

    fun pinServer(serverId: Long) {
        require(serverId > 0L) { "Invalid user selected pool" }
        MmkvManager.encodeSettings(PINNED_REGION_KEY, serverId)
    }

    fun selectRandom() {
        MmkvManager.encodeSettings(PINNED_REGION_KEY, -1L)
    }
}

/**
 * Manual selection always wins. Random connections use a fresh eligible server
 * from the current live pool list, without overwriting any saved preference.
 */
internal fun chooseRegionPoolId(
    pinnedServerId: Long?,
    availableServerIds: List<Long>,
    nextIndex: (Int) -> Int = { Random.Default.nextInt(it) },
): Long? {
    if (pinnedServerId != null) return pinnedServerId.takeIf { it > 0L }
    val options = availableServerIds.filter { it > 0L }.distinct()
    if (options.isEmpty()) return null
    return options[nextIndex(options.size)]
}

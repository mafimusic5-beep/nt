package com.v2ray.ang.handler

import com.v2ray.ang.network.EmeryBackendClient

/**
 * Select an available pool using occupancy data supplied by the server list.
 * Unknown-load pools retain backend priority and are only used after those
 * with verifiable free capacity. Never pin automatic reconnect to an old ID.
 */
internal fun chooseFreestVpnPool(
    servers: List<EmeryBackendClient.BackendServer>,
): EmeryBackendClient.BackendServer? = servers
    .withIndex()
    .filter { (_, server) ->
        server.isAvailable && server.healthStatus !in setOf("down", "offline", "unhealthy", "upgrade_required")
    }
    .sortedWith(
        compareBy<IndexedValue<EmeryBackendClient.BackendServer>> {
            val s = it.value
            s.connectionsLimit == null || s.connectionsLimit <= 0 || s.activeConnections == null
        }.thenBy {
            val s = it.value
            if (s.activeConnections != null && s.connectionsLimit != null && s.connectionsLimit > 0) {
                s.activeConnections.toDouble() / s.connectionsLimit.toDouble()
            } else {
                Double.POSITIVE_INFINITY
            }
        }.thenBy { it.index },
    )
    .firstOrNull()
    ?.value

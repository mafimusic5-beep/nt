package com.v2ray.ang.service

/**
 * IPv6 is a privacy boundary, not a performance preference.
 *
 * Android can have native IPv6 connectivity even when users have disabled the
 * app's "prefer IPv6" option. Leaving IPv6 outside the VPN then exposes users'
 * real network/ISP to destinations that advertise AAAA records.
 *
 * Callers MUST install an IPv6 VPN interface address and every returned route,
 * even if IPv4-only transport is configured; unsupported destinations should
 * fail closed through the VPN rather than silently bypass it.
 *
 * Explicit "bypass LAN" remains respected, matching the existing route list.
 */
internal object SkryonTrafficPrivacyRoutes {
    fun ipv6Routes(bypassLan: Boolean): List<Pair<String, Int>> =
        if (bypassLan) {
            listOf(
                "2000::" to 3, // Global unicast
                "fc00::" to 18, // Existing core FakeIPv6 prefix
            )
        } else {
            listOf("::" to 0) // Full IPv6 tunnel: no direct ISP IPv6 escape
        }
}

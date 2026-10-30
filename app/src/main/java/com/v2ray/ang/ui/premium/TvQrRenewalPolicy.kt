package com.v2ray.ang.ui.premium

/**
 * A TV QR ticket must be renewed once its server-issued lifetime ends.
 * Never keep displaying an expired or consumed QR.
 */
internal fun tvQrTicketExpired(expiresAtMillis: Long, nowMillis: Long): Boolean =
    nowMillis >= expiresAtMillis

internal fun tvQrSecondsRemaining(expiresAtMillis: Long, nowMillis: Long): Int =
    ((expiresAtMillis - nowMillis).coerceAtLeast(0L) / 1_000L).toInt()

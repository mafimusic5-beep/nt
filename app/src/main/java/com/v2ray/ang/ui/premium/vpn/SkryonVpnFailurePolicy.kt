package com.v2ray.ang.ui.premium.vpn

import java.util.Locale

/**
 * Turn *server-issued reason codes* into actionable messages without exposing
 * activation codes, endpoint credentials or raw HTTP bodies in the UI.
 *
 * A bad clock/signature is an AUTHENTICATION FAILURE, never an ordinary
 * transient outage. Do not fall back to a weaker verification or retry a
 * signed acquire until the device has valid time and identity.
 */
internal fun isBlockingSkryonAccessFailure(reason: String?): Boolean =
    reason.orEmpty().trim().lowercase(Locale.ROOT) in setOf(
        "not_found", "expired", "banned", "blocked", "revoked",
        "invalid_or_expired_key", "vpn_disabled", "upgrade_required",
        "device_timestamp_invalid", "device_signature_invalid",
        "device_signature_algorithm_invalid", "device_nonce_invalid",
        "device_replay_detected", "device_not_registered",
        "device_confirmation_missing", "device_mismatch",
        "device_inventory_mismatch", "device_counter_missing",
        "device_counter_mismatch", "device_proof_mismatch",
        "device_key_mismatch", "device_key_revoked",
        "plan_limit_mismatch",
    )

internal fun skryonAccessFailureMessage(reason: String?): String {
    return when (reason.orEmpty().trim().lowercase(Locale.ROOT)) {
        "device_timestamp_invalid" ->
            "Неверное время устройства. Проверьте дату и включите автоматическую синхронизацию времени."
        "device_signature_invalid", "device_signature_algorithm_invalid",
        "device_nonce_invalid", "device_replay_detected",
        "device_proof_mismatch", "device_key_mismatch", "device_key_revoked" ->
            "Сервер отклонил криптографическую проверку устройства. Проверьте регистрацию."
        "device_not_registered", "device_confirmation_missing" ->
            "Это устройство не зарегистрировано для тарифа."
        "device_mismatch", "device_inventory_mismatch" ->
            "Сервер не подтвердил текущее устройство."
        "device_counter_missing", "device_counter_mismatch" ->
            "Сервер не подтвердил список устройств."
        "plan_limit_mismatch" ->
            "Лимит подключений не соответствует тарифу."
        "invalid_or_expired_key", "not_found", "expired", "banned", "blocked", "revoked" ->
            "Код доступа недействителен или истёк."
        "vpn_disabled" -> "Доступ к VPN отключён."
        "upgrade_required" -> "Требуется обновить приложение."
        "network" -> "Не удалось проверить устройство. Проверьте интернет."
        else -> "Не удалось подтвердить доступ этого устройства."
    }
}

internal fun skryonConnectFailureMessage(reason: String?): String {
    val code = reason.orEmpty().trim().lowercase(Locale.ROOT)
    // The acquire endpoint also verifies the device proof. A failed clock or
    // signature must never be masked as a generic server outage.
    if (isBlockingSkryonAccessFailure(code)) return skryonAccessFailureMessage(code)
    return when (code) {
        "server_selection_refresh_required", "server_not_found", "no_server",
        "no_healthy_node", "server_unavailable", "assigned_node_missing" ->
            "В выбранном регионе сейчас нет доступного VPN-сервера."
        "server_capacity_unavailable", "concurrent_limit_reached" ->
            "Нет свободного места для подключения. Проверьте лимит тарифа и доступность региона."
        "vpn_session_acquire_rate_limited", "http_429", "too_many_attempts" ->
            "Слишком много попыток подключения. Повторите через несколько минут."
        "device_assignment_region_locked", "assignment_node_mismatch" ->
            "Устройство уже закреплено за другим сервером. Выберите его или смените регион."
        "assignment_install_in_progress", "assignment_maintenance_in_progress",
        "assignment_state_changed_retry" ->
            "Сервер настраивает персональный доступ. Подключение можно повторить позже."
        "credential_install_failed", "device_gate_not_ready",
        "direct_ingress_not_blocked", "server_config_unavailable",
        "device_gate_config_invalid", "import_failed" ->
            "Защищённый VPN-туннель пока не готов. Сервер должен подтвердить настройку."
        "pool_backend_unreachable", "server_list_unavailable", "http_502", "http_503",
        "http_504", "http_500", "network", "timeout" ->
            "Сервер временно недоступен. Проверьте интернет и повторите подключение."
        "vpn_session_expired", "vpn_session_mismatch" ->
            "VPN-сессия устарела. Подключитесь ещё раз."
        else -> "Не удалось подключиться к серверу. Проверьте диагностику VPN."
    }
}

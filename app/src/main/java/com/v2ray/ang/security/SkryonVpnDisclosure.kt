package com.v2ray.ang.security

import android.app.Activity
import android.app.AlertDialog
import android.content.Intent
import android.net.Uri

object SkryonVpnDisclosure {

    private const val PREFS = "skryon_privacy"
    private const val ACCEPTED_KEY = "vpn_disclosure_v1_accepted"
    private const val PRIVACY_URL = "https://skryon.ru/privacy.html"

    fun showIfNeeded(
        activity: Activity,
        onAccepted: () -> Unit,
        onDeclined: () -> Unit = {},
    ) {
        val preferences = activity.getSharedPreferences(PREFS, Activity.MODE_PRIVATE)
        if (preferences.getBoolean(ACCEPTED_KEY, false)) {
            onAccepted()
            return
        }

        var completed = false
        fun declineOnce() {
            if (!completed) {
                completed = true
                onDeclined()
            }
        }

        val dialog = AlertDialog.Builder(activity)
            .setTitle("VPN-подключение Skryon")
            .setMessage(
                "Skryon использует Android VpnService для создания зашифрованного " +
                    "VPN-туннеля между устройством и выбранным VPN-сервером. Во время " +
                    "подключения сетевой трафик устройства технически проходит через " +
                    "выбранный VPN-сервер. Skryon не использует VpnService для рекламного " +
                    "отслеживания или монетизации трафика и не сохраняет историю посещённых " +
                    "сайтов или содержимое VPN-трафика в VPN access-логах. Нажимая " +
                    "«Продолжить», вы разрешаете Skryon использовать VpnService для " +
                    "создания VPN-подключения."
            )
            .setPositiveButton("Продолжить") { _, _ ->
                completed = true
                preferences.edit().putBoolean(ACCEPTED_KEY, true).apply()
                onAccepted()
            }
            .setNegativeButton("Отмена") { _, _ -> declineOnce() }
            .setNeutralButton("Политика конфиденциальности", null)
            .setOnCancelListener { declineOnce() }
            .create()

        dialog.setOnShowListener {
            dialog.getButton(AlertDialog.BUTTON_NEUTRAL).setOnClickListener {
                runCatching {
                    activity.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(PRIVACY_URL)))
                }
            }
        }
        dialog.show()
    }
}

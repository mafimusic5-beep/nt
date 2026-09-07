package com.v2ray.ang.debug

import android.app.Activity
import android.app.AlertDialog
import android.app.Application
import android.content.ContentProvider
import android.content.ContentValues
import android.database.Cursor
import android.graphics.Color
import android.net.Uri
import android.os.Bundle
import android.view.Gravity
import android.view.ViewGroup
import android.widget.Button
import android.widget.FrameLayout
import android.widget.ScrollView
import android.widget.TextView
import com.v2ray.ang.handler.DeveloperConnectionDiagnostics
import com.v2ray.ang.handler.MmkvManager
import com.v2ray.ang.security.EmeryDeviceGateConfig

/** Installs a small developer-only "Логи" overlay on Skryon screens. */
class DeveloperConnectionDiagnosticsProvider : ContentProvider() {

    override fun onCreate(): Boolean {
        val app = context?.applicationContext as? Application ?: return true
        app.registerActivityLifecycleCallbacks(
            object : Application.ActivityLifecycleCallbacks {
                override fun onActivityResumed(activity: Activity) {
                    if (activity.javaClass.name == "com.v2ray.ang.ui.premium.PremiumActivity") {
                        installLogsButton(activity)
                    }
                }

                override fun onActivityCreated(activity: Activity, state: Bundle?) = Unit
                override fun onActivityStarted(activity: Activity) = Unit
                override fun onActivityPaused(activity: Activity) = Unit
                override fun onActivityStopped(activity: Activity) = Unit
                override fun onActivitySaveInstanceState(activity: Activity, outState: Bundle) = Unit
                override fun onActivityDestroyed(activity: Activity) = Unit
            },
        )
        return true
    }

    private fun installLogsButton(activity: Activity) {
        val root = activity.window.decorView as? ViewGroup ?: return
        if (root.findViewWithTag<Button>(BUTTON_TAG) != null) return

        val button = Button(activity).apply {
            tag = BUTTON_TAG
            text = "Логи"
            isAllCaps = false
            textSize = 12f
            setTextColor(Color.WHITE)
            setPadding(dp(activity, 14), 0, dp(activity, 14), 0)
            setOnClickListener { showLogs(activity) }
        }
        val params = FrameLayout.LayoutParams(
            ViewGroup.LayoutParams.WRAP_CONTENT,
            dp(activity, 42),
            Gravity.END or Gravity.BOTTOM,
        ).apply {
            marginEnd = dp(activity, 16)
            bottomMargin = dp(activity, 88)
        }
        root.addView(button, params)
    }

    private fun showLogs(activity: Activity) {
        val selectedGuid = MmkvManager.getSelectServer()?.trim().orEmpty()
        val profile = selectedGuid.takeIf { it.isNotEmpty() }?.let(MmkvManager::decodeServerConfig)
        val gateProfile = EmeryDeviceGateConfig.isGateProfile(profile)
        val gateDescriptor = if (gateProfile) EmeryDeviceGateConfig.descriptorFor(profile) else null

        val diagnosticText = buildString {
            appendLine("profile_selected=${profile != null}")
            appendLine("gate_profile=$gateProfile")
            appendLine("gate_descriptor=${gateDescriptor != null}")
            appendLine()
            append(DeveloperConnectionDiagnostics.snapshot())
        }

        val text = TextView(activity).apply {
            setTextColor(Color.rgb(30, 30, 30))
            textSize = 12f
            typeface = android.graphics.Typeface.MONOSPACE
            setTextIsSelectable(true)
            setPadding(dp(activity, 18), dp(activity, 12), dp(activity, 18), dp(activity, 12))
            this.text = diagnosticText
        }
        val scroll = ScrollView(activity).apply { addView(text) }

        AlertDialog.Builder(activity)
            .setTitle("Логи подключения")
            .setView(scroll)
            .setNeutralButton("Очистить") { _, _ -> DeveloperConnectionDiagnostics.clear() }
            .setPositiveButton("Закрыть", null)
            .show()
    }

    private fun dp(activity: Activity, value: Int): Int =
        (value * activity.resources.displayMetrics.density).toInt()

    override fun query(
        uri: Uri,
        projection: Array<out String>?,
        selection: String?,
        selectionArgs: Array<out String>?,
        sortOrder: String?,
    ): Cursor? = null

    override fun getType(uri: Uri): String? = null
    override fun insert(uri: Uri, values: ContentValues?): Uri? = null
    override fun delete(uri: Uri, selection: String?, selectionArgs: Array<out String>?): Int = 0
    override fun update(uri: Uri, values: ContentValues?, selection: String?, selectionArgs: Array<out String>?): Int = 0

    private companion object {
        const val BUTTON_TAG = "skryon_developer_connection_logs"
    }
}

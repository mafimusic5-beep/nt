package com.v2ray.ang.security

import android.app.Activity
import android.app.Dialog
import android.content.Context
import android.content.res.ColorStateList
import android.graphics.Color
import android.graphics.Paint
import android.graphics.Typeface
import android.graphics.drawable.ColorDrawable
import android.graphics.drawable.GradientDrawable
import android.graphics.drawable.RippleDrawable
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.view.Window
import android.view.WindowManager
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.LifecycleOwner
import com.v2ray.ang.util.Utils
import kotlin.math.roundToInt

object SkryonActivationDisclosure {

    private const val PREFERENCES = "skryon_activation_disclosure"
    // Bump the version when the disclosure changes materially.
    private const val ACCEPTED_VERSION = "accepted_version"
    private const val VERSION = 2
    private const val PRIVACY_URL = "https://skryon.ru/privacy.html"
    private val green = Color.rgb(0, 143, 91)
    private val openDialogs = mutableMapOf<Activity, Dialog>()

    fun showIfNeeded(
        activity: Activity,
        onAccepted: () -> Unit,
        onDeclined: () -> Unit = {},
    ) {
        if (activity.isFinishing || activity.isDestroyed) return
        if (openDialogs[activity]?.isShowing == true) return

        val preferences = activity.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE)
        if (preferences.getInt(ACCEPTED_VERSION, 0) == VERSION) {
            onAccepted()
            return
        }

        fun dp(value: Int) = (value * activity.resources.displayMetrics.density).roundToInt()
        fun rounded(color: Int, radius: Int) = GradientDrawable().apply {
            setColor(color)
            cornerRadius = dp(radius).toFloat()
        }
        fun buttonBackground(color: Int) = RippleDrawable(
            ColorStateList.valueOf(Color.argb(35, 0, 90, 55)),
            rounded(color, 16),
            rounded(Color.WHITE, 16),
        )
        fun textView(value: String) = TextView(activity).apply {
            text = value
            textSize = 18f
            setTextColor(Color.rgb(20, 27, 35))
            typeface = Typeface.create("sans-serif", Typeface.NORMAL)
            setLineSpacing(dp(3).toFloat(), 1f)
        }
        fun row(topMargin: Int = 0) = LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT,
            ViewGroup.LayoutParams.WRAP_CONTENT,
        ).apply { this.topMargin = dp(topMargin) }

        val dialog = Dialog(activity, android.R.style.Theme_Material_Light_Dialog_NoActionBar)
        dialog.requestWindowFeature(Window.FEATURE_NO_TITLE)
        dialog.setCancelable(true)
        dialog.setCanceledOnTouchOutside(false)

        val content = LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(24), dp(28), dp(24), dp(20))
        }
        content.addView(textView(
            "Перед проверкой кода доступа Skryon передаёт на сервер данные, необходимые для активации."
        ), row())
        content.addView(textView(
            "Передаются код активации, случайный технический идентификатор установки, имя устройства " +
                "и открытый криптографический ключ. Сервер также технически обрабатывает IP-адрес " +
                "и служебные данные запроса: версию приложения, время, одноразовое значение и подпись."
        ), row(22))
        content.addView(textView(
            "Код доступа после активации также используется для аутентификации запросов к сервису. " +
                "Данные нужны для проверки подписки, регистрации или восстановления установки " +
                "и защиты доступа от злоупотреблений."
        ), row(22))

        val privacyLink = textView("Политика конфиденциальности").apply {
            setTextColor(green)
            paintFlags = paintFlags or Paint.UNDERLINE_TEXT_FLAG
            minHeight = dp(48)
            gravity = Gravity.CENTER_VERTICAL
            isFocusable = true
            setOnClickListener { Utils.openUri(activity, PRIVACY_URL) }
        }
        content.addView(privacyLink, row(16))

        val continueButton = Button(activity).apply {
            text = "Продолжить"
            textSize = 18f
            isAllCaps = false
            typeface = Typeface.create("sans-serif", Typeface.BOLD)
            setTextColor(Color.WHITE)
            background = buttonBackground(green)
            minHeight = dp(56)
            setPadding(dp(16), dp(12), dp(16), dp(12))
            stateListAnimator = null
        }
        content.addView(continueButton, row(16))

        val cancelButton = Button(activity).apply {
            text = "Отмена"
            textSize = 18f
            isAllCaps = false
            typeface = Typeface.create("sans-serif", Typeface.BOLD)
            setTextColor(green)
            background = buttonBackground(Color.TRANSPARENT)
            minHeight = dp(52)
            setPadding(dp(16), dp(12), dp(16), dp(12))
            stateListAnimator = null
            setOnClickListener { dialog.cancel() }
        }
        content.addView(cancelButton, row(8))

        val scroll = ScrollView(activity).apply {
            background = rounded(Color.WHITE, 24)
            clipToOutline = true
            isFillViewport = false
            addView(content)
        }
        dialog.setContentView(scroll)
        dialog.window?.apply {
            setBackgroundDrawable(ColorDrawable(Color.TRANSPARENT))
            addFlags(WindowManager.LayoutParams.FLAG_DIM_BEHIND)
            setDimAmount(0.38f)
            setGravity(Gravity.CENTER)
        }

        val lifecycle = (activity as? LifecycleOwner)?.lifecycle
        val observer = LifecycleEventObserver { _, event ->
            if (event == Lifecycle.Event.ON_DESTROY) dialog.dismiss()
        }
        dialog.setOnCancelListener { onDeclined() }
        dialog.setOnDismissListener {
            openDialogs.remove(activity)
            lifecycle?.removeObserver(observer)
        }
        continueButton.setOnClickListener {
            if (activity.isFinishing || activity.isDestroyed) {
                dialog.dismiss()
                return@setOnClickListener
            }
            continueButton.isEnabled = false
            preferences.edit().putInt(ACCEPTED_VERSION, VERSION).apply()
            dialog.dismiss()
            onAccepted()
        }

        openDialogs[activity] = dialog
        lifecycle?.addObserver(observer)
        dialog.show()
        val metrics = activity.resources.displayMetrics
        val width = minOf(metrics.widthPixels - dp(32), dp(560)).coerceAtLeast(1)
        content.measure(
            View.MeasureSpec.makeMeasureSpec(width, View.MeasureSpec.EXACTLY),
            View.MeasureSpec.makeMeasureSpec(0, View.MeasureSpec.UNSPECIFIED),
        )
        val height = minOf(content.measuredHeight, (metrics.heightPixels * 0.80f).roundToInt())
        dialog.window?.setLayout(width, height)
    }
}


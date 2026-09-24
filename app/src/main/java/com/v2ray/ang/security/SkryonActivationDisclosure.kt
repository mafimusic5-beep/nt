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
import android.view.ViewTreeObserver
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
        fun textView(
            value: String,
            size: Float = 18f,
            weight: Int = Typeface.NORMAL,
            color: Int = Color.rgb(20, 27, 35),
        ) = TextView(activity).apply {
            text = value
            textSize = size
            setTextColor(color)
            typeface = Typeface.create("sans-serif", weight)
            setLineSpacing(dp(3).toFloat(), 1f)
        }
        fun row(topMargin: Int = 0) = LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT,
            ViewGroup.LayoutParams.WRAP_CONTENT,
        ).apply { this.topMargin = dp(topMargin) }
        fun separator() = View(activity).apply {
            setBackgroundColor(Color.BLACK)
        }
        fun separatorParams() = LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT,
            1,
        )

        val dialog = Dialog(activity, android.R.style.Theme_Material_Light_Dialog_NoActionBar)
        dialog.requestWindowFeature(Window.FEATURE_NO_TITLE)
        dialog.setCancelable(true)
        dialog.setCanceledOnTouchOutside(false)

        val root = LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            background = rounded(Color.WHITE, 24)
            clipToOutline = true
        }

        val header = LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(24), dp(24), dp(24), dp(18))
        }
        header.addView(
            textView("Skryon", size = 18f, weight = Typeface.BOLD),
            row(),
        )
        header.addView(
            textView("Соглашение и\nуведомления", size = 28f, weight = Typeface.BOLD),
            row(20),
        )
        header.addView(
            textView(
                "Перед активацией и первым подключением ознакомьтесь с полной информацией ниже.",
                size = 16f,
                color = Color.rgb(77, 83, 92),
            ),
            row(10),
        )
        root.addView(header, row())
        root.addView(separator(), separatorParams())

        val body = LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(24), dp(20), dp(24), dp(22))
        }
        body.addView(
            textView("1. Активация и данные устройства", size = 18f, weight = Typeface.BOLD),
            row(),
        )
        body.addView(
            textView(
                "Перед проверкой кода доступа Skryon передаёт на сервер данные, необходимые для активации."
            ),
            row(18),
        )
        body.addView(
            textView(
                "Передаются код активации, случайный технический идентификатор установки, имя устройства " +
                    "и открытый криптографический ключ. Сервер также технически обрабатывает IP-адрес " +
                    "и служебные данные запроса: версию приложения, время, одноразовое значение и подпись."
            ),
            row(22),
        )
        body.addView(
            textView(
                "Код доступа после активации также используется для аутентификации запросов к сервису. " +
                    "Данные нужны для проверки подписки, регистрации или восстановления установки " +
                    "и защиты доступа от злоупотреблений."
            ),
            row(22),
        )

        val privacyLink = textView("Политика конфиденциальности", size = 17f).apply {
            setTextColor(green)
            paintFlags = paintFlags or Paint.UNDERLINE_TEXT_FLAG
            minHeight = dp(48)
            gravity = Gravity.CENTER_VERTICAL
            isFocusable = true
            setOnClickListener { Utils.openUri(activity, PRIVACY_URL) }
        }
        body.addView(privacyLink, row(18))

        val scroll = ScrollView(activity).apply {
            isFillViewport = false
            addView(body)
        }
        root.addView(
            scroll,
            LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                0,
                1f,
            ),
        )
        root.addView(separator(), separatorParams())

        val footer = LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(24), dp(16), dp(24), dp(18))
        }
        footer.addView(
            textView(
                "Нажимая «Продолжить», вы подтверждаете, что ознакомились с этой информацией и Политикой конфиденциальности.",
                size = 14f,
                color = Color.rgb(77, 83, 92),
            ).apply { gravity = Gravity.CENTER },
            row(),
        )

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
            isEnabled = false
            alpha = 0.42f
        }
        footer.addView(continueButton, row(14))

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
        footer.addView(cancelButton, row(6))
        root.addView(footer, row())

        dialog.setContentView(root)
        dialog.window?.apply {
            setBackgroundDrawable(ColorDrawable(Color.TRANSPARENT))
            addFlags(WindowManager.LayoutParams.FLAG_DIM_BEHIND)
            setDimAmount(0.38f)
            setGravity(Gravity.CENTER)
        }

        fun updateContinueAvailability() {
            val scrollChild = scroll.getChildAt(0) ?: return
            val atBottom = scroll.scrollY + scroll.height >= scrollChild.height - dp(2)
            if (continueButton.isEnabled != atBottom) {
                continueButton.isEnabled = atBottom
                continueButton.alpha = if (atBottom) 1f else 0.42f
            }
        }
        val scrollListener = ViewTreeObserver.OnScrollChangedListener {
            updateContinueAvailability()
        }
        scroll.viewTreeObserver.addOnScrollChangedListener(scrollListener)

        val lifecycle = (activity as? LifecycleOwner)?.lifecycle
        val observer = LifecycleEventObserver { _, event ->
            if (event == Lifecycle.Event.ON_DESTROY) dialog.dismiss()
        }
        dialog.setOnCancelListener { onDeclined() }
        dialog.setOnDismissListener {
            openDialogs.remove(activity)
            lifecycle?.removeObserver(observer)
            if (scroll.viewTreeObserver.isAlive) {
                scroll.viewTreeObserver.removeOnScrollChangedListener(scrollListener)
            }
        }
        continueButton.setOnClickListener {
            if (!continueButton.isEnabled) return@setOnClickListener
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
        val width = minOf(metrics.widthPixels - dp(24), dp(560)).coerceAtLeast(1)
        val height = minOf((metrics.heightPixels * 0.90f).roundToInt(), dp(760)).coerceAtLeast(1)
        dialog.window?.setLayout(width, height)
        scroll.post { updateContinueAvailability() }
    }
}

package com.v2ray.ang.ui.premium.vpn

import android.content.pm.PackageManager
import android.content.res.Configuration
import android.graphics.Paint
import android.graphics.Path
import android.graphics.Typeface
import androidx.compose.material3.LocalContentColor
import androidx.compose.material3.LocalTextStyle
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.drawWithContent
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.nativeCanvas
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.text.TextLayoutResult
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.TextUnit
import androidx.compose.ui.unit.sp
import androidx.compose.ui.unit.isSpecified

/**
 * On some Android TVs, system "High contrast/Outline text" changes glyph
 * rendering into white text with a black halo even for a dark-on-white
 * application. Apps cannot turn this OS accessibility preference off.
 *
 * For NON-EDITABLE labels on TV only, retain Compose's real Text layout,
 * semantics, focus and line breaks but paint the visible glyph outlines as
 * vector paths. Android's high-contrast *text* renderer does not intercept
 * Canvas.drawPath(), so Skryon labels remain clean.
 *
 * Handled locally: the global TV accessibility setting is never changed.
 * Phones/tablets and unsupported fonts keep regular Material Text rendering.
 */
@Composable
internal fun TvCrispText(
    text: String,
    modifier: Modifier = Modifier,
    color: Color = Color.Unspecified,
    fontSize: TextUnit = TextUnit.Unspecified,
    fontStyle: FontStyle? = null,
    fontWeight: FontWeight? = null,
    fontFamily: FontFamily? = null,
    letterSpacing: TextUnit = TextUnit.Unspecified,
    textDecoration: TextDecoration? = null,
    textAlign: TextAlign? = null,
    lineHeight: TextUnit = TextUnit.Unspecified,
    overflow: TextOverflow = TextOverflow.Clip,
    softWrap: Boolean = true,
    maxLines: Int = Int.MAX_VALUE,
    minLines: Int = 1,
    onTextLayout: (TextLayoutResult) -> Unit = {},
    style: TextStyle = LocalTextStyle.current,
) {
    val context = LocalContext.current
    val isTv = remember(context) {
        val mode = context.resources.configuration.uiMode and Configuration.UI_MODE_TYPE_MASK
        mode == Configuration.UI_MODE_TYPE_TELEVISION ||
            context.packageManager.hasSystemFeature(PackageManager.FEATURE_LEANBACK)
    }
    val density = LocalDensity.current
    val effectiveFontSize = if (fontSize.isSpecified) fontSize else style.fontSize
    val sizePx = with(density) {
        (if (effectiveFontSize.isSpecified) effectiveFontSize else 16.sp).toPx()
    }
    val effectiveColor = when {
        color != Color.Unspecified -> color
        style.color != Color.Unspecified -> style.color
        else -> LocalContentColor.current
    }
    val effectiveWeight = fontWeight ?: style.fontWeight ?: FontWeight.Normal
    val effectiveStyle = fontStyle ?: style.fontStyle ?: FontStyle.Normal

    // Complex surrogate emoji and interactive/underlined text retain native
    // rendering, which preserves fallback fonts and decoration.
    val useVectorGlyphs = shouldDrawTvGlyphPaths(
        isTv = isTv,
        text = text,
        hasDecoration = textDecoration != null || style.textDecoration != null,
        hasExplicitSize = effectiveFontSize.isSpecified,
        alpha = effectiveColor.alpha,
    )
    var measured by remember(text) { mutableStateOf<TextLayoutResult?>(null) }

    val paint = remember(sizePx, effectiveColor, effectiveWeight, effectiveStyle) {
        Paint(Paint.ANTI_ALIAS_FLAG or Paint.SUBPIXEL_TEXT_FLAG).apply {
            this.color = effectiveColor.toArgb()
            textSize = sizePx
            typeface = Typeface.create(
                "sans-serif",
                when {
                    effectiveWeight.weight >= FontWeight.SemiBold.weight && effectiveStyle == FontStyle.Italic -> Typeface.BOLD_ITALIC
                    effectiveWeight.weight >= FontWeight.SemiBold.weight -> Typeface.BOLD
                    effectiveStyle == FontStyle.Italic -> Typeface.ITALIC
                    else -> Typeface.NORMAL
                },
            )
            this.style = Paint.Style.FILL
        }
    }

    val drawModifier = if (useVectorGlyphs) {
        modifier.drawWithContent {
            val layout = measured
            if (layout == null) {
                drawContent()
            } else {
                // Do not invoke drawContent() here: it contains the Android TV
                // high-contrast text rasterization that adds the unwanted halo.
                val canvas = drawContext.canvas.nativeCanvas
                val save = canvas.save()
                canvas.clipRect(0f, 0f, size.width, size.height)
                val glyphs = Path()
                for (line in 0 until layout.lineCount) {
                    val from = layout.getLineStart(line).coerceIn(0, text.length)
                    val to = layout.getLineEnd(line, visibleEnd = true).coerceIn(from, text.length)
                    var segment = text.substring(from, to).trimEnd('\r', '\n')
                    if (layout.isLineEllipsized(line)) segment += "…"
                    if (segment.isBlank()) continue
                    glyphs.reset()
                    paint.getTextPath(segment, 0, segment.length,
                        layout.getLineLeft(line), layout.getLineBaseline(line), glyphs)
                    canvas.drawPath(glyphs, paint)
                }
                canvas.restoreToCount(save)
            }
        }
    } else modifier

    Text(
        text = text,
        modifier = drawModifier,
        color = color,
        fontSize = fontSize,
        fontStyle = fontStyle,
        fontWeight = fontWeight,
        fontFamily = fontFamily,
        letterSpacing = letterSpacing,
        textDecoration = textDecoration,
        textAlign = textAlign,
        lineHeight = lineHeight,
        overflow = overflow,
        softWrap = softWrap,
        maxLines = maxLines,
        minLines = minLines,
        onTextLayout = { layout ->
            measured = layout
            onTextLayout(layout)
        },
        style = style,
    )
}

/** Keep decorative vector rendering off unsupported text and non-TV devices. */
internal fun shouldDrawTvGlyphPaths(
    isTv: Boolean,
    text: String,
    hasDecoration: Boolean,
    hasExplicitSize: Boolean,
    alpha: Float,
): Boolean =
    isTv && text.isNotEmpty() && !hasDecoration && hasExplicitSize && alpha > 0f &&
        text.none { Character.isSurrogate(it) }

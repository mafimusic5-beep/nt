package com.v2ray.ang.ui.premium.vpn

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class TvCrispTextPolicyTest {
    @Test
    fun `regular TV labels are drawn without system text outline`() {
        assertTrue(shouldDrawTvGlyphPaths(
            isTv = true,
            text = "Создаём защищённое подключение",
            hasDecoration = false,
            hasExplicitSize = true,
            alpha = 1f,
        ))
    }

    @Test
    fun `phone and tablet text retains native renderer`() {
        assertFalse(shouldDrawTvGlyphPaths(
            isTv = false, text = "Подключение", hasDecoration = false,
            hasExplicitSize = true, alpha = 1f,
        ))
    }

    @Test
    fun `emoji with surrogate glyphs retain native color emoji fonts`() {
        assertFalse(shouldDrawTvGlyphPaths(
            isTv = true, text = "👋 Подключено", hasDecoration = false,
            hasExplicitSize = true, alpha = 1f,
        ))
    }

    @Test
    fun `transparent decorated and empty text are never custom painted`() {
        for ((value, decoration, alpha) in listOf(
            Triple("", false, 1f),
            Triple("Подключено", true, 1f),
            Triple("Подключено", false, 0f),
        )) {
            assertFalse(shouldDrawTvGlyphPaths(
                isTv = true, text = value, hasDecoration = decoration,
                hasExplicitSize = true, alpha = alpha,
            ))
        }
    }
}

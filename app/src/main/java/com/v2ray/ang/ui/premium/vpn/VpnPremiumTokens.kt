package com.v2ray.ang.ui.premium.vpn

import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

internal object VpnPremiumTokens {
    object Colors {
        val Background = Color(0xFF0B132B)
        val Surface = Color(0xFF0D1A33)
        val BorderSubtle = Color(0x1AFFFFFF)
        val BorderStrong = Color(0x26FFFFFF)

        val TextPrimary = Color(0xFFF8FAFC)
        val TextSecondary = Color(0xFFA7B3C9)

        val SilhouetteDisconnected = Color(0xFF5E7290)
        val SilhouetteConnecting = Color(0xFFA8BFDB)
        val SilhouetteConnected = Color(0xFFC0D6EF)

        val PrimaryButtonIdle = Color(0xFF162646)
        val PrimaryButtonConnected = Color(0xFF213B63)

        val SettingsCircleFill = Color(0xF2FFFFFF)
        val SettingsIcon = Color(0xFF0B132B)
    }

    object Spacing {
        val ScreenHorizontal: Dp = 22.dp
        val TopPadding: Dp = 14.dp
        val BetweenTopAndHero: Dp = 10.dp
        val HeroToBottom: Dp = 18.dp
        val BottomBlockGap: Dp = 12.dp
        val BottomSafeExtra: Dp = 18.dp
    }

    object Sizes {
        val TopSelectorMaxWidth: Dp = 320.dp
        val SettingsButton: Dp = 38.dp
        val SelectorHeight: Dp = 44.dp
        val PrimaryButtonHeight: Dp = 58.dp
        val FieldCorner: Dp = 18.dp
        val SelectorCorner: Dp = 22.dp
        val PrimaryButtonCorner: Dp = 22.dp
    }

    object Typography {
        val TimerLetterSpacing = 1.1.sp
        val OverlayLetterSpacing = 0.2.sp
    }
}


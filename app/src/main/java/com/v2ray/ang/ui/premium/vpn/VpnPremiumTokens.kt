package com.v2ray.ang.ui.premium.vpn

import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

internal object VpnPremiumTokens {
    object Colors {
        val Background = Color(0xFFF7F8F4)
        val Surface = Color(0xFFFFFFFF)
        val BorderSubtle = Color(0xFFE7ECE2)
        val BorderStrong = Color(0xFFDCE4D5)

        val TextPrimary = Color(0xFF111319)
        val TextSecondary = Color(0xFF7D828D)

        val Positive = Color(0xFF6BC652)
        val PositiveStrong = Color(0xFF49B530)
        val PositiveSoft = Color(0xFFEAF6E3)
        val Track = Color(0xFFD7DDD1)

        val PrimaryButtonIdle = Color(0xFFC8F08E)
        val PrimaryButtonConnected = Color(0xFFC8F08E)
        val ButtonText = Color(0xFF111319)

        val SilhouetteDisconnected = Positive
        val SilhouetteConnecting = Positive
        val SilhouetteConnected = PositiveStrong

        val SettingsCircleFill = Color(0xF2FFFFFF)
        val SettingsIcon = TextPrimary
    }

    object Spacing {
        val ScreenHorizontal: Dp = 24.dp
        val TopPadding: Dp = 16.dp
        val BetweenTopAndHero: Dp = 24.dp
        val HeroToBottom: Dp = 24.dp
        val BottomBlockGap: Dp = 16.dp
        val BottomSafeExtra: Dp = 20.dp
    }

    object Sizes {
        val TopSelectorMaxWidth: Dp = 280.dp
        val SettingsButton: Dp = 46.dp
        val SelectorHeight: Dp = 44.dp
        val PrimaryButtonHeight: Dp = 72.dp
        val FieldCorner: Dp = 18.dp
        val SelectorCorner: Dp = 18.dp
        val PrimaryButtonCorner: Dp = 24.dp
    }

    object Typography {
        val TimerLetterSpacing = 0.sp
        val OverlayLetterSpacing = 0.sp
    }
}

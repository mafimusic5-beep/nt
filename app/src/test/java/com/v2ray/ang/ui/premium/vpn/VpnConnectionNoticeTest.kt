package com.v2ray.ang.ui.premium.vpn

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class VpnConnectionNoticeTest {
    @Test
    fun connectingShowsAnHonestPendingMessageBeforeTunnelVerification() {
        val state = VpnMainUiState(connectionState = VpnConnectionState.Connecting)
        assertTrue(state.connectionNotice.contains("Подключаем VPN"))
        assertFalse(state.connectionNotice.contains("Защита включена"))
    }

    @Test
    fun errorAlreadyDisplayedSeparatelyDoesNotDuplicateConnectionHint() {
        val state = VpnMainUiState(
            connectionState = VpnConnectionState.Connecting,
            locationsError = "Нет связи с сервером",
        )
        assertEquals("", state.connectionNotice)
    }

    @Test
    fun stoppedAndVerifiedStatesHaveNoPendingNotice() {
        assertEquals("", VpnMainUiState().connectionNotice)
        assertEquals(
            "",
            VpnMainUiState(connectionState = VpnConnectionState.Connected).connectionNotice,
        )
    }

    @Test
    fun noticeRespondsToImmutableStateChanges() {
        val before = VpnMainUiState(connectionState = VpnConnectionState.Disconnected)
        assertEquals("", before.connectionNotice)
        assertTrue(before.copy(connectionState = VpnConnectionState.Connecting).connectionNotice.isNotBlank())
        assertEquals("", before.copy(connectionState = VpnConnectionState.Connected).connectionNotice)
    }
}

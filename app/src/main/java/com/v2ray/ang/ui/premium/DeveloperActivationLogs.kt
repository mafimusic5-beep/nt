package com.v2ray.ang.ui.premium

import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier

internal fun developerActivationLogsEnabled(): Boolean = false

@Composable
internal fun DeveloperActivationLogs(
    diagnostic: SkryonActivationDiagnostic?,
    modifier: Modifier = Modifier,
) = Unit

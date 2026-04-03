$premium = Join-Path $PSScriptRoot '..\app\src\main\java\com\v2ray\ang\ui\premium\PremiumActivity.kt'
$vm = Join-Path $PSScriptRoot '..\app\src\main\java\com\v2ray\ang\ui\premium\vpn\VpnMainViewModel.kt'
$screen = Join-Path $PSScriptRoot '..\app\src\main\java\com\v2ray\ang\ui\premium\vpn\VpnMainScreen.kt'
$preview = Join-Path $PSScriptRoot '..\app\src\main\java\com\v2ray\ang\ui\premium\vpn\VpnMainScreenPreview.kt'

$premium = [System.IO.Path]::GetFullPath($premium)
$vm = [System.IO.Path]::GetFullPath($vm)
$screen = [System.IO.Path]::GetFullPath($screen)
$preview = [System.IO.Path]::GetFullPath($preview)

$p = Get-Content $premium -Raw -Encoding UTF8
$p = $p.Replace("import androidx.compose.runtime.LaunchedEffect`n", "import androidx.compose.runtime.LaunchedEffect`nimport androidx.compose.runtime.collectAsState`n")
$p = $p.Replace("import androidx.compose.ui.graphics.Color`n", "import androidx.compose.ui.graphics.Color`nimport androidx.compose.ui.platform.LocalContext`n")
$p = $p.Replace("import com.v2ray.ang.handler.EmeryAccessManager`n", "import com.v2ray.ang.handler.EmeryAccessManager`nimport com.v2ray.ang.handler.SettingsManager`nimport com.v2ray.ang.handler.V2RayServiceManager`n")
$p = $p.Replace("import com.v2ray.ang.ui.premium.vpn.VpnMainRoute`n", "import com.v2ray.ang.ui.premium.vpn.VpnConnectionState`nimport com.v2ray.ang.ui.premium.vpn.VpnMainRoute`n")

$old = @'
            composable(EmeryRoute.Home.name) {
                val vpnMainViewModel: VpnMainViewModel = viewModel()
                LaunchedEffect(Unit) {
                    VpnUiDebugLogger.log(
                        hypothesisId = "H2",
                        location = "PremiumActivity.kt:EmeryApp",
                        message = "home route switched to vpn compose screen",
                        data = JSONObject(),
                    )
                }
                VpnMainRoute(
                    viewModel = vpnMainViewModel,
                    onSettingsClick = { showMenu = true },
                )
            }
'@

$new = @'
            composable(EmeryRoute.Home.name) {
                val vpnMainViewModel: VpnMainViewModel = viewModel()
                val context = LocalContext.current
                val uiState by vpnMainViewModel.uiState.collectAsState()
                var previousConnectionState by remember { mutableStateOf(uiState.connectionState) }

                LaunchedEffect(Unit) {
                    VpnUiDebugLogger.log(
                        hypothesisId = "H2",
                        location = "PremiumActivity.kt:EmeryApp",
                        message = "home route switched to vpn compose screen",
                        data = JSONObject(),
                    )
                }

                LaunchedEffect(uiState.connectionState) {
                    when {
                        previousConnectionState != VpnConnectionState.Connected &&
                            uiState.connectionState == VpnConnectionState.Connected -> {
                            if (SettingsManager.isVpnMode()) {
                                requestVpnPermission {
                                    V2RayServiceManager.startVService(context)
                                }
                            } else {
                                V2RayServiceManager.startVService(context)
                            }
                        }

                        previousConnectionState == VpnConnectionState.Connected &&
                            uiState.connectionState == VpnConnectionState.Disconnected -> {
                            V2RayServiceManager.stopVService(context)
                        }
                    }
                    previousConnectionState = uiState.connectionState
                }

                VpnMainRoute(
                    viewModel = vpnMainViewModel,
                    onSettingsClick = { showMenu = true },
                )
            }
'@

if ($p.Contains($old)) { $p = $p.Replace($old, $new) }
Set-Content $premium $p -Encoding UTF8

$v = Get-Content $vm -Raw -Encoding UTF8
$v = $v.Replace('internal data class VpnServerRegionUi(', 'data class VpnServerRegionUi(')
Set-Content $vm $v -Encoding UTF8

$s = Get-Content $screen -Raw -Encoding UTF8
$s = $s.Replace('enabled = uiState.connectButtonEnabled && selectedLocation != null,', 'enabled = selectedLocation != null && uiState.connectionState != VpnConnectionState.Connecting,')
Set-Content $screen $s -Encoding UTF8

@'
package com.v2ray.ang.ui.premium.vpn

import androidx.compose.runtime.Composable
import androidx.compose.ui.tooling.preview.Preview

@Preview(showBackground = true)
@Composable
private fun VpnMainScreenPreviewPlaceholder() {}
'@ | Set-Content $preview -Encoding UTF8

Write-Host 'patched'

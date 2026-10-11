# Skryon Android beacon → desktop visual port

This directory is **UI-only**. The linked GitHub repositories contain the Android VPN client and website, but no existing Windows VPN client source code was found on the main branch. Do **not** treat this as a functioning Windows VPN, and do not connect the preview's state buttons to fake VPN success.

## Source of truth

Android source: `app/src/main/java/com/v2ray/ang/ui/premium/vpn/VpnMainScreen.kt`, composable `StatusBeacon` (near line 431).
Colors: `app/src/main/java/com/v2ray/ang/ui/premium/vpn/VpnPremiumTokens.kt`.

The previous desktop mock showed a large glossy green sphere. That is **not** the actual Android design. The Android component is a **small flat green core with a white central dot, three translucent filled halos, and three or four expanding outline ripples**.

| Property | Disconnected | Connecting | Connected |
| --- | --- | --- | --- |
| Core color | `#6BC652` | `#6BC652` | `#2F9E44` |
| Relative intensity | 0.62 | 1.0 | 0.86 |
| Ripple cycle | 3200 ms | 1150 ms | 2300 ms |
| Pulse half-cycle | 1800 ms | 700 ms | 1250 ms |
| Glow half-cycle | 2100 ms | 780 ms | 1450 ms |
| Expanding outlines | 3 | 4 | 3 |

Default drawing area: **104dp**, core diameter **34dp**. Compact: 88dp/30dp. Tight: 72dp/25dp.

- Core radius pulses from 96% to 104% using `FastOutSlowInEasing` (`cubic-bezier(0.4,0,0.2,1)`) and reverses.
- Glow ranges from 70% to 100% and reverses.
- Three filled background circles: radii 96%, 70% × pulse, 48% × pulse of the outer radius, with alpha `0.035`, `0.07`, `0.11` × glow × intensity.
- Expanding outlines: from 1.25 × core radius to 0.92 × outer radius; alpha `(1-phase)^2 × 0.17 × intensity`; stroke `1.15 + (1-phase) × 0.85` dp.
- Extra core halo: 1.3 × pulsing core radius, alpha `(0.14 + 0.05 × glow) × intensity`.
- Solid core plus central white dot (12% of core radius, 88% opacity).
- **No gradients, glass effects, 3D lighting, or large glossy ball.**

## Files

- `SkryonBeaconPreview.html`: open in any desktop browser; compare all three visual states with the Android implementation. It does not start a VPN.
- `SkryonStatusBeacon.cs`: WPF `FrameworkElement` control with matching canvas drawing and timing. Targets .NET 6+ WPF; no extra packages. It does not manage a VPN connection.

## Integration in an existing WPF application

1. Add `SkryonStatusBeacon.cs` to the desktop project and adjust the namespace if necessary.
2. Add the namespace in your XAML, e.g. `xmlns:skryon="clr-namespace:Skryon.Desktop.UI"`.
3. Bind the control to the **existing** VPN connection status, not to a separate fake state:

```xml
<skryon:SkryonStatusBeacon
    ConnectionState="{Binding BeaconConnectionState}"
    Density="Normal"
    HorizontalAlignment="Center"
    VerticalAlignment="Center" />
```

Map the application's real disconnected/connecting/connected status to `BeaconConnectionState` in its view model. Keep existing connect/disconnect commands, authentication, routing, and tunnel logic unchanged. The component automatically unsubscribes from the rendering event when unloaded.

If the desktop client is not WPF, use the HTML canvas preview as a visual/behavioral reference and port the drawing into the existing UI toolkit instead of embedding a new UI framework.

## Safety and verification

The changes are isolated in the new `desktop/` directory on a separate branch; Android code, backend, website, and production branches remain unchanged. No Windows build is configured in this repository, so the WPF component has **not been compiled or visually compared against a running Windows client**. Integration requires the actual Windows application's source code and framework.

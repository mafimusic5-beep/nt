// Standalone WPF port of the Android StatusBeacon visual component.
// Source: app/src/main/java/com/v2ray/ang/ui/premium/vpn/VpnMainScreen.kt
// This file does not start, stop, or configure a VPN tunnel.
using System;
using System.Diagnostics;
using System.Windows;
using System.Windows.Media;

namespace Skryon.Desktop.UI;

public enum BeaconConnectionState
{
    Disconnected,
    Connecting,
    Connected
}

public enum BeaconDensity
{
    Normal,
    Compact,
    Tight
}

/// <summary>
/// WPF reproduction of the Android Compose StatusBeacon: flat green core,
/// three translucent halos, expanding outline ripples, and a white dot.
/// No glossy sphere, gradient, shadows, or VPN side effects.
/// </summary>
public sealed class SkryonStatusBeacon : FrameworkElement
{
    public static readonly DependencyProperty ConnectionStateProperty =
        DependencyProperty.Register(
            nameof(ConnectionState),
            typeof(BeaconConnectionState),
            typeof(SkryonStatusBeacon),
            new FrameworkPropertyMetadata(
                BeaconConnectionState.Disconnected,
                FrameworkPropertyMetadataOptions.AffectsRender,
                OnConnectionStateChanged));

    public static readonly DependencyProperty DensityProperty =
        DependencyProperty.Register(
            nameof(Density),
            typeof(BeaconDensity),
            typeof(SkryonStatusBeacon),
            new FrameworkPropertyMetadata(
                BeaconDensity.Normal,
                FrameworkPropertyMetadataOptions.AffectsMeasure |
                FrameworkPropertyMetadataOptions.AffectsRender));

    private readonly Stopwatch _clock = new();
    private bool _subscribed;

    public BeaconConnectionState ConnectionState
    {
        get => (BeaconConnectionState)GetValue(ConnectionStateProperty);
        set => SetValue(ConnectionStateProperty, value);
    }

    public BeaconDensity Density
    {
        get => (BeaconDensity)GetValue(DensityProperty);
        set => SetValue(DensityProperty, value);
    }

    public SkryonStatusBeacon()
    {
        Loaded += (_, _) => UpdateAnimation();
        Unloaded += (_, _) => StopAnimation();
        IsVisibleChanged += (_, _) => UpdateAnimation();
    }

    private static void OnConnectionStateChanged(DependencyObject d, DependencyPropertyChangedEventArgs e)
    {
        var beacon = (SkryonStatusBeacon)d;
        // Restart cycles on state change to avoid carrying an old phase/duration.
        beacon._clock.Restart();
        beacon.InvalidateVisual();
    }

    private double BeaconSize => Density switch
    {
        BeaconDensity.Tight => 72,
        BeaconDensity.Compact => 88,
        _ => 104
    };

    private double CoreSize => Density switch
    {
        BeaconDensity.Tight => 25,
        BeaconDensity.Compact => 30,
        _ => 34
    };

    protected override Size MeasureOverride(Size availableSize) => new(BeaconSize, BeaconSize);

    private void UpdateAnimation()
    {
        if (IsLoaded && IsVisible)
        {
            if (_subscribed) return;
            _clock.Restart();
            CompositionTarget.Rendering += OnRendering;
            _subscribed = true;
        }
        else
        {
            StopAnimation();
        }
    }

    private void StopAnimation()
    {
        if (!_subscribed) return;
        CompositionTarget.Rendering -= OnRendering;
        _subscribed = false;
        _clock.Stop();
    }

    private void OnRendering(object? sender, EventArgs e) => InvalidateVisual();

    protected override void OnRender(DrawingContext drawingContext)
    {
        base.OnRender(drawingContext);

        double diameter = Math.Min(RenderSize.Width, RenderSize.Height);
        if (diameter <= 0) return;

        double scale = diameter / BeaconSize;
        double outerRadius = diameter / 2.0;
        double coreRadius = CoreSize * scale / 2.0;
        var center = new Point(RenderSize.Width / 2.0, RenderSize.Height / 2.0);
        double ms = _clock.Elapsed.TotalMilliseconds;

        double intensity = ConnectionState switch
        {
            BeaconConnectionState.Disconnected => 0.62,
            BeaconConnectionState.Connecting => 1.0,
            _ => 0.86
        };

        Color coreColor = ConnectionState == BeaconConnectionState.Connected
            ? Color.FromRgb(0x2F, 0x9E, 0x44) // Android PositiveStrong
            : Color.FromRgb(0x6B, 0xC6, 0x52); // Android Positive

        double rippleDuration = ConnectionState switch
        {
            BeaconConnectionState.Disconnected => 3200,
            BeaconConnectionState.Connecting => 1150,
            _ => 2300
        };
        double pulseDuration = ConnectionState switch
        {
            BeaconConnectionState.Disconnected => 1800,
            BeaconConnectionState.Connecting => 700,
            _ => 1250
        };
        double glowDuration = ConnectionState switch
        {
            BeaconConnectionState.Disconnected => 2100,
            BeaconConnectionState.Connecting => 780,
            _ => 1450
        };

        double ripplePhase = (ms % rippleDuration) / rippleDuration;
        double pulse = 0.96 + 0.08 * ReverseEasedProgress(ms, pulseDuration);
        double glow = 0.70 + 0.30 * ReverseEasedProgress(ms, glowDuration);

        // Exactly the Android canvas order, relative radii, and alpha formulas.
        Circle(drawingContext, center, outerRadius * 0.96,
            coreColor, 0.035 * glow * intensity);
        Circle(drawingContext, center, outerRadius * 0.70 * pulse,
            coreColor, 0.070 * glow * intensity);
        Circle(drawingContext, center, outerRadius * 0.48 * pulse,
            coreColor, 0.110 * glow * intensity);

        int rippleCount = ConnectionState == BeaconConnectionState.Connecting ? 4 : 3;
        for (int i = 0; i < rippleCount; i++)
        {
            double phase = (ripplePhase + (double)i / rippleCount) % 1.0;
            double startRadius = coreRadius * 1.25;
            double radius = startRadius + (outerRadius * 0.92 - startRadius) * phase;
            double alpha = (1 - phase) * (1 - phase) * 0.17 * intensity;
            double strokeWidth = (1.15 + (1 - phase) * 0.85) * scale;
            var pen = new Pen(Brush(coreColor, alpha), strokeWidth);
            pen.Freeze();
            drawingContext.DrawEllipse(null, pen, center, radius, radius);
        }

        double animatedCoreRadius = coreRadius * pulse;
        Circle(drawingContext, center, animatedCoreRadius * 1.30,
            coreColor, (0.14 + 0.05 * glow) * intensity);
        Circle(drawingContext, center, animatedCoreRadius, coreColor, 1.0);
        Circle(drawingContext, center, animatedCoreRadius * 0.12,
            Colors.White, 0.88);
    }

    private static void Circle(DrawingContext dc, Point center, double radius, Color color, double alpha)
        => dc.DrawEllipse(Brush(color, alpha), null, center, radius, radius);

    private static SolidColorBrush Brush(Color color, double alpha)
    {
        color.A = (byte)Math.Round(Math.Clamp(alpha, 0.0, 1.0) * 255.0);
        var brush = new SolidColorBrush(color);
        brush.Freeze();
        return brush;
    }

    private static double ReverseEasedProgress(double elapsedMs, double oneWayDurationMs)
    {
        double progress = (elapsedMs % (oneWayDurationMs * 2)) / oneWayDurationMs;
        if (progress > 1.0) progress = 2.0 - progress;
        return FastOutSlowIn(progress);
    }

    // Compose FastOutSlowInEasing = cubic-bezier(0.4, 0.0, 0.2, 1.0).
    private static double FastOutSlowIn(double x)
    {
        double lo = 0, hi = 1;
        for (int i = 0; i < 22; i++)
        {
            double t = (lo + hi) / 2;
            double xAtT = Cubic(t, 0.4, 0.2);
            if (xAtT < x) lo = t;
            else hi = t;
        }
        return Cubic((lo + hi) / 2, 0.0, 1.0);
    }

    private static double Cubic(double t, double control1, double control2)
    {
        double inv = 1 - t;
        return 3 * inv * inv * t * control1
             + 3 * inv * t * t * control2
             + t * t * t;
    }
}

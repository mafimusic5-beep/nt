from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_android_has_no_tracking_or_analytics_sdks():
    gradle = (ROOT / "app" / "build.gradle.kts").read_text(encoding="utf-8").lower()
    forbidden = (
        "firebase-analytics",
        "firebase-crashlytics",
        "com.google.firebase:firebase-analytics",
        "sentry-android",
        "amplitude",
        "appsflyer",
        "adjust-android",
        "facebook-android-sdk",
        "mixpanel",
    )
    assert not [marker for marker in forbidden if marker in gradle]


def test_device_identity_does_not_use_hardware_or_advertising_ids():
    source = (
        ROOT
        / "app"
        / "src"
        / "main"
        / "java"
        / "com"
        / "v2ray"
        / "ang"
        / "security"
        / "EmeryDeviceIdentity.kt"
    ).read_text(encoding="utf-8")
    forbidden = (
        "Settings.Secure.ANDROID_ID",
        "AdvertisingIdClient",
        "TelephonyManager",
        "getImei(",
        "getMeid(",
        "Build.MODEL",
        "Build.MANUFACTURER",
        "Build.BRAND",
        "Build.SERIAL",
    )
    assert not [marker for marker in forbidden if marker in source]


def test_manifest_does_not_request_identity_or_location_permissions():
    manifest = (ROOT / "app" / "src" / "main" / "AndroidManifest.xml").read_text(encoding="utf-8")
    forbidden = (
        "android.permission.ACCESS_FINE_LOCATION",
        "android.permission.ACCESS_COARSE_LOCATION",
        "android.permission.READ_CONTACTS",
        "android.permission.WRITE_CONTACTS",
        "android.permission.READ_PHONE_STATE",
        "android.permission.READ_PHONE_NUMBERS",
        "android.permission.GET_ACCOUNTS",
    )
    assert not [permission for permission in forbidden if permission in manifest]

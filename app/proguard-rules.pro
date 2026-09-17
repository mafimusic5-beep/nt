# Skryon release hardening.
# Keep only reflection/JNI-sensitive surfaces; application implementation code remains obfuscatable.

# Gson models are reflection-backed and many Kotlin DTOs do not have no-arg constructors.
-keep class com.v2ray.ang.dto.** { *; }
-keepattributes Signature,*Annotation*,InnerClasses,EnclosingMethod

# Native Go/Xray bindings and VPN service contracts may be resolved from native code or Android IPC.
-keep class libv2ray.** { *; }
-keep class go.** { *; }
-keep class com.v2ray.ang.contracts.** { *; }
-keep class com.v2ray.ang.service.** { *; }

# MMKV/Android components ship consumer rules, but preserve native methods explicitly.
-keepclasseswithmembernames,includedescriptorclasses class * {
    native <methods>;
}

# Release privacy: diagnostics must not become a device/IP/config activity trail.
# Debug builds are unaffected because R8/minification is enabled only for release.
-assumenosideeffects class android.util.Log {
    public static boolean isLoggable(java.lang.String, int);
    public static int v(...);
    public static int d(...);
    public static int i(...);
    public static int w(...);
    public static int e(...);
    public static int wtf(...);
    public static int println(...);
}

# Do not retain source file names in release mappings/APKs.
-renamesourcefileattribute SourceFile

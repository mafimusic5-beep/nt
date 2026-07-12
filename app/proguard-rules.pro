# Project-specific ProGuard/R8 rules for Skryon.

# Keep JSON DTO shapes used by Gson/reflection when release minification is enabled.
-keep class com.v2ray.ang.dto.** { *; }
-keep class com.v2ray.ang.handler.EmeryAccessProfile { *; }
-keep class com.v2ray.ang.network.EmeryBackendClient$BackendServer { *; }
-keep class com.v2ray.ang.network.EmeryBackendClient$ConnectPayload { *; }

# Keep native bridge classes required by v2ray/libxray.
-keep class go.** { *; }
-keep class libv2ray.** { *; }
-keep class com.tencent.mmkv.** { *; }

# Preserve annotations/signatures required by libraries while still obfuscating app code.
-keepattributes Signature,InnerClasses,EnclosingMethod,RuntimeVisibleAnnotations,RuntimeVisibleParameterAnnotations

# Do not print noisy warnings for optional platform/instrumentation classes.
-dontwarn de.robv.android.xposed.**
-dontwarn re.frida.**
-dontwarn com.saurik.substrate.**

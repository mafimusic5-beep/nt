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

# Do not retain source file names in release mappings/APKs.
-renamesourcefileattribute SourceFile

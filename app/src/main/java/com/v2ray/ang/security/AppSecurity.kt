package com.v2ray.ang.security

import android.content.Context
import android.content.pm.PackageManager
import android.content.pm.Signature
import android.os.Build
import android.os.Debug
import com.v2ray.ang.AngApplication
import com.v2ray.ang.BuildConfig
import java.io.File
import java.security.MessageDigest
import java.util.Locale

/**
 * Client-side hardening for premium API calls.
 *
 * This is not a replacement for backend-side access control. A modified APK can always try to
 * remove client checks, so the backend must still verify device signatures, nonces, timestamps,
 * access-key ownership, device limits and app-integrity headers before returning VPN configs.
 */
object AppSecurity {

    private val suspiciousHookClasses = listOf(
        "de.robv.android.xposed.XposedBridge",
        "de.robv.android.xposed.XC_MethodHook",
        "com.saurik.substrate.MS\$2",
        "com.android.internal.os.ZygoteInit\$MethodAndArgsCaller",
        "re.frida.server.Frida",
    )

    private val suspiciousMapTokens = listOf(
        "frida",
        "gum-js-loop",
        "gadget",
        "xposed",
        "lsposed",
        "substrate",
        "zygisk",
    )

    fun premiumApiBlockReason(context: Context = AngApplication.application): String? {
        if (!hasAllowedAppSignature(context)) {
            return "app_signature_not_allowed"
        }

        if (BuildConfig.DEBUG || !BuildConfig.SKRYON_BLOCK_TAMPERED_RUNTIME) {
            return null
        }

        if (Debug.isDebuggerConnected() || Debug.waitingForDebugger()) {
            return "debugger_detected"
        }

        if (hasHookClassLoaded() || hasHookLibraryMapped()) {
            return "runtime_hook_detected"
        }

        return null
    }

    fun securityHeaders(context: Context = AngApplication.application): Map<String, String> {
        val reason = premiumApiBlockReason(context)
        return linkedMapOf(
            "X-Skryon-App-Package" to context.packageName,
            "X-Skryon-App-Version" to BuildConfig.VERSION_NAME,
            "X-Skryon-App-Distribution" to BuildConfig.DISTRIBUTION,
            "X-Skryon-App-Debug" to BuildConfig.DEBUG.toString(),
            "X-Skryon-App-Signature-Sha256" to appCertificateSha256s(context).joinToString(","),
            "X-Skryon-App-Integrity" to (reason ?: "ok"),
        )
    }

    fun appCertificateSha256s(context: Context = AngApplication.application): List<String> {
        return runCatching {
            val signatures = packageSignatures(context)
            signatures.map { signature -> sha256Hex(signature.toByteArray()) }
                .filter { it.isNotBlank() }
                .distinct()
        }.getOrDefault(emptyList())
    }

    private fun hasAllowedAppSignature(context: Context): Boolean {
        val allowed = BuildConfig.SKRYON_ALLOWED_SIGNATURE_SHA256S
            .split(',', ';', ' ', '\n', '\t')
            .map { normalizeDigest(it) }
            .filter { it.isNotBlank() }
            .toSet()

        // Local debug builds keep this empty. Release builds should set it through Gradle:
        // -PSKRYON_ALLOWED_SIGNATURE_SHA256S=<release-cert-sha256>
        if (allowed.isEmpty()) return true

        return appCertificateSha256s(context).map(::normalizeDigest).any { it in allowed }
    }

    @Suppress("DEPRECATION")
    private fun packageSignatures(context: Context): Array<Signature> {
        val packageName = context.packageName
        val packageManager = context.packageManager
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            val info = packageManager.getPackageInfo(packageName, PackageManager.GET_SIGNING_CERTIFICATES)
            val signingInfo = info.signingInfo
            when {
                signingInfo == null -> emptyArray()
                signingInfo.hasMultipleSigners() -> signingInfo.apkContentsSigners ?: emptyArray()
                else -> signingInfo.signingCertificateHistory ?: emptyArray()
            }
        } else {
            packageManager.getPackageInfo(packageName, PackageManager.GET_SIGNATURES).signatures ?: emptyArray()
        }
    }

    private fun hasHookClassLoaded(): Boolean {
        val loaders = listOfNotNull(
            AppSecurity::class.java.classLoader,
            Thread.currentThread().contextClassLoader,
            ClassLoader.getSystemClassLoader(),
        )
        return suspiciousHookClasses.any { className ->
            loaders.any { loader ->
                runCatching { Class.forName(className, false, loader) }.isSuccess
            }
        }
    }

    private fun hasHookLibraryMapped(): Boolean {
        val maps = File("/proc/self/maps")
        if (!maps.exists() || !maps.canRead()) return false

        return runCatching {
            maps.bufferedReader().useLines { lines ->
                lines.take(2048).any { line ->
                    val lower = line.lowercase(Locale.US)
                    suspiciousMapTokens.any { token -> lower.contains(token) }
                }
            }
        }.getOrDefault(false)
    }

    private fun sha256Hex(bytes: ByteArray): String {
        val digest = MessageDigest.getInstance("SHA-256").digest(bytes)
        return digest.joinToString(separator = "") { byte ->
            String.format(Locale.US, "%02X", byte.toInt() and 0xff)
        }
    }

    private fun normalizeDigest(value: String): String {
        return value.trim()
            .replace(":", "")
            .replace("-", "")
            .uppercase(Locale.US)
    }
}

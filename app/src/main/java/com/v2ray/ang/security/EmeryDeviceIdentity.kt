package com.v2ray.ang.security

import android.os.Build
import android.provider.Settings
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import com.v2ray.ang.AngApplication
import com.v2ray.ang.handler.MmkvManager
import java.security.KeyPairGenerator
import java.security.KeyStore
import java.security.MessageDigest
import java.security.Signature
import java.security.spec.ECGenParameterSpec
import java.util.Locale
import java.util.UUID

private const val PREF_EMERY_DEVICE_ID = "pref_emery_device_id"
private const val PREF_EMERY_DEVICE_NAME = "pref_emery_device_name"
private const val BROKEN_LEGACY_ANDROID_ID = "9774d56d682e549c"
private const val DEFAULT_DEVICE_NAME = "Android-устройство"
private const val DEVICE_PROBE_PREFIX = "dp1:"
private const val DEVICE_PROBE_DOMAIN = "skryon-device-v1:"

object EmeryDeviceIdentity {

    private const val KEYSTORE_PROVIDER = "AndroidKeyStore"
    private const val LEGACY_KEY_ALIAS_PREFIX = "emery_device_key_"
    private const val KEY_ALIAS_PREFIX = "skryon_device_key_v2_"
    private const val SIGNATURE_ALGORITHM = "SHA256withECDSA"
    private const val RECOVERY_PROTOCOL = "skryon-device-recovery-v1"

    private val technicalNameMarkers = listOf(
        "sdk_gphone",
        "google sdk",
        "android sdk built for",
        "generic_x86",
        "generic x86",
        "x86_64",
        "arm64-v8a",
        "emulator",
    )

    data class ActivationProof(
        val deviceId: String,
        val deviceName: String,
        val publicKeyBase64: String,
        val timestampMillis: String,
        val nonce: String,
        val signatureBase64: String,
        val signatureAlgorithm: String = SIGNATURE_ALGORITHM,
    )

    data class SignedRequestProof(
        val deviceId: String,
        val timestampMillis: String,
        val nonce: String,
        val signatureBase64: String,
        val signatureAlgorithm: String = SIGNATURE_ALGORITHM,
    )

    data class GatewayProof(
        val deviceId: String,
        val timestampMillis: String,
        val clientNonce: String,
        val signatureBase64: String,
        val signatureAlgorithm: String = SIGNATURE_ALGORITHM,
    )

    data class RecoveryProof(
        val deviceId: String,
        val publicKeyBase64: String,
        val publicKeyFingerprintSha256: String,
        val timestampMillis: String,
        val nonce: String,
        val signatureBase64: String,
        val signatureAlgorithm: String = SIGNATURE_ALGORITHM,
    )

    /**
     * The raw ANDROID_ID never leaves this process and is never persisted by Skryon.
     * Android 8+ keeps it stable for the same Android user and Skryon signing key
     * across an ordinary uninstall/reinstall. We expose only a domain-separated
     * one-way probe so the backend can recognize the same installation target
     * without receiving the platform identifier itself.
     */
    fun deviceId(): String {
        val androidId = rawAndroidId()
        val resolved = if (androidId.isNotBlank()) {
            DEVICE_PROBE_PREFIX + sha256Hex(DEVICE_PROBE_DOMAIN + androidId.lowercase(Locale.ROOT))
        } else {
            val saved = MmkvManager.decodeSettingsString(PREF_EMERY_DEVICE_ID)?.trim().orEmpty()
            if (saved.startsWith(DEVICE_PROBE_PREFIX) && saved.length == 68) {
                saved
            } else {
                DEVICE_PROBE_PREFIX + sha256Hex("skryon-fallback-v1:" + UUID.randomUUID())
            }
        }
        MmkvManager.encodeSettings(PREF_EMERY_DEVICE_ID, resolved)
        return resolved
    }

    /** The raw platform identifier is used only locally to derive the v2 probe. */
    private fun rawAndroidId(): String {
        return runCatching {
            Settings.Secure.getString(
                AngApplication.application.contentResolver,
                Settings.Secure.ANDROID_ID,
            )
        }.getOrNull()
            ?.trim()
            ?.takeIf { value -> value.isNotBlank() && value != BROKEN_LEGACY_ANDROID_ID }
            .orEmpty()
    }

    /**
     * The server and UI only receive a friendly alias. Hardware manufacturer,
     * model and emulator architecture are never exposed as the device name.
     */
    fun deviceName(): String {
        val cached = MmkvManager.decodeSettingsString(PREF_EMERY_DEVICE_NAME)?.trim().orEmpty()
        val resolved = sanitizeDeviceName(cached)
        if (cached != resolved) {
            MmkvManager.encodeSettings(PREF_EMERY_DEVICE_NAME, resolved)
        }
        return resolved
    }

    fun currentPublicKeyBase64(): String = publicKeyBase64()

    fun currentPublicKeyFingerprintSha256(): String {
        val entry = getOrCreatePrivateKeyEntry()
        return sha256Hex(entry.certificate.publicKey.encoded)
    }

    fun buildActivationProof(path: String, accessKey: String): ActivationProof {
        val resolvedDeviceId = deviceId()
        val resolvedDeviceName = deviceName()
        val timestamp = System.currentTimeMillis().toString()
        val nonce = randomNonce()
        val canonical = listOf(
            "method=POST",
            "path=$path",
            "device_id=$resolvedDeviceId",
            "device_name=$resolvedDeviceName",
            "timestamp=$timestamp",
            "nonce=$nonce",
            "auth_sha256=${sha256Hex(accessKey.trim())}",
        ).joinToString(separator = "\n")
        return ActivationProof(
            deviceId = resolvedDeviceId,
            deviceName = resolvedDeviceName,
            publicKeyBase64 = publicKeyBase64(),
            timestampMillis = timestamp,
            nonce = nonce,
            signatureBase64 = signCanonical(canonical),
        )
    }

    fun buildRecoveryChallengeProof(accessKey: String): RecoveryProof {
        val resolvedDeviceId = deviceId()
        val publicKey = publicKeyBase64()
        val fingerprint = currentPublicKeyFingerprintSha256()
        val timestamp = System.currentTimeMillis().toString()
        val nonce = randomNonce()
        val canonical = listOf(
            "protocol=$RECOVERY_PROTOCOL",
            "stage=challenge",
            "path=/api/device/recovery/challenge",
            "device_id=$resolvedDeviceId",
            "new_key_sha256=$fingerprint",
            "timestamp=$timestamp",
            "nonce=$nonce",
            "auth_sha256=${sha256Hex(accessKey.trim())}",
        ).joinToString(separator = "\n")
        return RecoveryProof(
            deviceId = resolvedDeviceId,
            publicKeyBase64 = publicKey,
            publicKeyFingerprintSha256 = fingerprint,
            timestampMillis = timestamp,
            nonce = nonce,
            signatureBase64 = signCanonical(canonical),
        )
    }

    fun buildRecoveryConfirmProof(
        accessKey: String,
        challengeId: String,
        serverChallenge: String,
        integrityToken: String,
    ): RecoveryProof {
        val resolvedDeviceId = deviceId()
        val publicKey = publicKeyBase64()
        val fingerprint = currentPublicKeyFingerprintSha256()
        val timestamp = System.currentTimeMillis().toString()
        val nonce = randomNonce()
        val canonical = listOf(
            "protocol=$RECOVERY_PROTOCOL",
            "stage=confirm",
            "path=/api/device/recovery/confirm",
            "challenge_id=${challengeId.trim()}",
            "server_challenge_sha256=${sha256Hex(serverChallenge)}",
            "device_id=$resolvedDeviceId",
            "new_key_sha256=$fingerprint",
            "timestamp=$timestamp",
            "nonce=$nonce",
            "auth_sha256=${sha256Hex(accessKey.trim())}",
            "integrity_token_sha256=${sha256Hex(integrityToken)}",
        ).joinToString(separator = "\n")
        return RecoveryProof(
            deviceId = resolvedDeviceId,
            publicKeyBase64 = publicKey,
            publicKeyFingerprintSha256 = fingerprint,
            timestampMillis = timestamp,
            nonce = nonce,
            signatureBase64 = signCanonical(canonical),
        )
    }

    fun buildRequestProof(method: String, path: String, authSecret: String): SignedRequestProof {
        val resolvedDeviceId = deviceId()
        val timestamp = System.currentTimeMillis().toString()
        val nonce = randomNonce()
        val canonical = listOf(
            "method=${method.trim().uppercase(Locale.US)}",
            "path=$path",
            "device_id=$resolvedDeviceId",
            "timestamp=$timestamp",
            "nonce=$nonce",
            "auth_sha256=${sha256Hex(authSecret.trim())}",
        ).joinToString(separator = "\n")
        return SignedRequestProof(
            deviceId = resolvedDeviceId,
            timestampMillis = timestamp,
            nonce = nonce,
            signatureBase64 = signCanonical(canonical),
        )
    }

    fun buildGatewayProof(
        assignmentId: Long,
        nodeId: Long,
        gateServerName: String,
        gateSpkiSha256: String,
        serverIssuedAt: String,
        serverNonce: String,
    ): GatewayProof {
        require(assignmentId > 0 && nodeId > 0) { "Invalid device-gate assignment" }
        val resolvedDeviceId = deviceId()
        val normalizedServerName = gateServerName.trim().lowercase(Locale.ROOT)
        val timestamp = System.currentTimeMillis().toString()
        val clientNonce = randomNonce()
        val canonical = listOf(
            "protocol=emery-device-gate-v1",
            "assignment_id=$assignmentId",
            "node_id=$nodeId",
            "gate_server_name=$normalizedServerName",
            "gate_spki_sha256=${gateSpkiSha256.trim().lowercase(Locale.ROOT)}",
            "device_id=$resolvedDeviceId",
            "server_issued_at=$serverIssuedAt",
            "timestamp=$timestamp",
            "server_nonce=$serverNonce",
            "client_nonce=$clientNonce",
        ).joinToString(separator = "\n")
        return GatewayProof(
            deviceId = resolvedDeviceId,
            timestampMillis = timestamp,
            clientNonce = clientNonce,
            signatureBase64 = signCanonical(canonical),
        )
    }

    private fun sanitizeDeviceName(value: String): String {
        val normalized = value
            .replace('\n', ' ')
            .replace('\r', ' ')
            .trim()
            .replace(Regex("\\s+"), " ")
            .take(64)
        if (normalized.isBlank()) {
            return DEFAULT_DEVICE_NAME
        }
        val lower = normalized.lowercase(Locale.ROOT)
        return if (technicalNameMarkers.any { marker -> lower.contains(marker) }) {
            DEFAULT_DEVICE_NAME
        } else {
            normalized
        }
    }

    private fun publicKeyBase64(): String {
        val entry = getOrCreatePrivateKeyEntry()
        return Base64.encodeToString(entry.certificate.publicKey.encoded, Base64.NO_WRAP)
    }

    private fun signCanonical(canonicalPayload: String): String {
        val entry = getOrCreatePrivateKeyEntry()
        val signature = Signature.getInstance(SIGNATURE_ALGORITHM)
        signature.initSign(entry.privateKey)
        signature.update(canonicalPayload.toByteArray(Charsets.UTF_8))
        return Base64.encodeToString(signature.sign(), Base64.NO_WRAP)
    }

    private fun getOrCreatePrivateKeyEntry(): KeyStore.PrivateKeyEntry {
        val keyStore = KeyStore.getInstance(KEYSTORE_PROVIDER).apply { load(null) }

        legacyKeyAlias()?.let { legacyAlias ->
            val legacy = keyStore.getEntry(legacyAlias, null) as? KeyStore.PrivateKeyEntry
            if (legacy != null) {
                return legacy
            }
        }

        val alias = KEY_ALIAS_PREFIX + deviceId().removePrefix(DEVICE_PROBE_PREFIX)
        val existing = keyStore.getEntry(alias, null) as? KeyStore.PrivateKeyEntry
        if (existing != null) {
            return existing
        }

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            try {
                generateKey(alias, strongBox = true)
                return keyStore.getEntry(alias, null) as? KeyStore.PrivateKeyEntry
                    ?: error("Unable to read StrongBox Skryon device key")
            } catch (_: Exception) {
                // StrongBox is optional. A hardware-backed Android Keystore key is
                // still preferable to falling back to an exportable app secret.
            }
        }

        generateKey(alias, strongBox = false)
        return keyStore.getEntry(alias, null) as? KeyStore.PrivateKeyEntry
            ?: error("Unable to create Skryon device key")
    }

    private fun legacyKeyAlias(): String? {
        val rawId = rawAndroidId()
        return rawId.takeIf { it.isNotBlank() }?.let { LEGACY_KEY_ALIAS_PREFIX + it }
    }

    private fun generateKey(alias: String, strongBox: Boolean) {
        val generator = KeyPairGenerator.getInstance(KeyProperties.KEY_ALGORITHM_EC, KEYSTORE_PROVIDER)
        val builder = KeyGenParameterSpec.Builder(
            alias,
            KeyProperties.PURPOSE_SIGN or KeyProperties.PURPOSE_VERIFY,
        )
            .setAlgorithmParameterSpec(ECGenParameterSpec("secp256r1"))
            .setDigests(KeyProperties.DIGEST_SHA256, KeyProperties.DIGEST_SHA512)
            .setUserAuthenticationRequired(false)
        if (strongBox && Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            builder.setIsStrongBoxBacked(true)
        }
        generator.initialize(builder.build())
        generator.generateKeyPair()
    }

    private fun randomNonce(): String = UUID.randomUUID().toString().replace("-", "")

    private fun sha256Hex(value: String): String = sha256Hex(value.toByteArray(Charsets.UTF_8))

    private fun sha256Hex(value: ByteArray): String {
        val digest = MessageDigest.getInstance("SHA-256").digest(value)
        return digest.joinToString(separator = "") { byte -> String.format(Locale.US, "%02x", byte.toInt() and 0xff) }
    }
}

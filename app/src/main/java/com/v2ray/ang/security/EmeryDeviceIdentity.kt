package com.v2ray.ang.security

import android.os.Build
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
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

object EmeryDeviceIdentity {

    private const val KEYSTORE_PROVIDER = "AndroidKeyStore"
    private const val KEY_ALIAS_PREFIX = "emery_device_key_"
    private const val SIGNATURE_ALGORITHM = "SHA256withECDSA"

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

    fun deviceId(): String {
        val saved = MmkvManager.decodeSettingsString(PREF_EMERY_DEVICE_ID)?.trim().orEmpty()
        if (saved.isNotEmpty()) {
            return saved
        }
        val generated = UUID.randomUUID().toString()
        MmkvManager.encodeSettings(PREF_EMERY_DEVICE_ID, generated)
        return generated
    }

    fun deviceName(): String {
        val cached = MmkvManager.decodeSettingsString(PREF_EMERY_DEVICE_NAME)?.trim().orEmpty()
        if (cached.isNotEmpty()) {
            return cached
        }
        val manufacturer = Build.MANUFACTURER?.trim().orEmpty()
        val model = Build.MODEL?.trim().orEmpty()
        val raw = listOf(manufacturer, model)
            .filter { it.isNotBlank() }
            .joinToString(separator = " ")
            .replace('\n', ' ')
            .replace('\r', ' ')
            .trim()
        val resolved = raw.ifBlank { "Android Device" }.take(80)
        MmkvManager.encodeSettings(PREF_EMERY_DEVICE_NAME, resolved)
        return resolved
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
        val alias = keyAlias()
        val existing = keyStore.getEntry(alias, null) as? KeyStore.PrivateKeyEntry
        if (existing != null) {
            return existing
        }

        val generator = KeyPairGenerator.getInstance(KeyProperties.KEY_ALGORITHM_EC, KEYSTORE_PROVIDER)
        val spec = KeyGenParameterSpec.Builder(
            alias,
            KeyProperties.PURPOSE_SIGN or KeyProperties.PURPOSE_VERIFY,
        )
            .setAlgorithmParameterSpec(ECGenParameterSpec("secp256r1"))
            .setDigests(KeyProperties.DIGEST_SHA256, KeyProperties.DIGEST_SHA512)
            .setUserAuthenticationRequired(false)
            .build()
        generator.initialize(spec)
        generator.generateKeyPair()

        return keyStore.getEntry(alias, null) as? KeyStore.PrivateKeyEntry
            ?: error("Unable to create Emery device key")
    }

    private fun keyAlias(): String = KEY_ALIAS_PREFIX + deviceId()

    private fun randomNonce(): String = UUID.randomUUID().toString().replace("-", "")

    private fun sha256Hex(value: String): String {
        val digest = MessageDigest.getInstance("SHA-256").digest(value.toByteArray(Charsets.UTF_8))
        return digest.joinToString(separator = "") { byte -> String.format(Locale.US, "%02x", byte.toInt() and 0xff) }
    }
}

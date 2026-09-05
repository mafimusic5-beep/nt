package com.v2ray.ang.security

import com.v2ray.ang.dto.ProfileItem
import com.v2ray.ang.handler.MmkvManager
import org.json.JSONObject
import java.net.URI
import java.net.URLDecoder
import java.security.MessageDigest
import java.util.Locale

object EmeryDeviceGateConfig {

    const val LOCAL_HOST = "127.0.0.1"
    const val LOCAL_PORT = 17890
    private const val PREF_PREFIX = "pref_emery_device_gate_"
    private const val PROTOCOL_VERSION = 1
    private const val PUBLIC_PROFILE_NAME = "Skryon"
    private val safeHost = Regex("^[A-Za-z0-9.-]{1,255}$")
    private val safePin = Regex("^[a-f0-9]{64}$")

    data class Descriptor(
        val gatewayHost: String,
        val gatewayPort: Int,
        val serverName: String,
        val spkiSha256: String,
        val assignmentId: Long,
        val nodeId: Long,
        val localPort: Int = LOCAL_PORT,
    )

    fun prepareImportText(importText: String): String {
        val lines = importText.lineSequence().map { it.trim() }.filter { it.isNotEmpty() }.toList()
        require(lines.isNotEmpty()) { "Empty VPN configuration" }
        val gatedCount = lines.count { hasGateMarker(it) }
        require(gatedCount == 0 || gatedCount == lines.size) { "Mixed gated and direct VPN profiles" }
        return lines.joinToString(separator = "\n") { line ->
            if (hasGateMarker(line)) prepareVlessUri(line) else sanitizePublicRemark(line)
        }
    }

    fun prepareVlessUri(rawConfig: String): String {
        if (!hasGateMarker(rawConfig)) {
            return sanitizePublicRemark(rawConfig)
        }
        val uri = URI(rawConfig.trim())
        require(uri.scheme.equals("vless", ignoreCase = true)) { "Device gate requires VLESS" }
        require(uri.host == LOCAL_HOST && uri.port == LOCAL_PORT) { "Unsafe VLESS ingress endpoint" }
        val credential = uri.userInfo.orEmpty()
        require(credential.isNotBlank()) { "Missing VLESS credential" }

        val queryParts = uri.rawQuery.orEmpty().split('&').filter { it.isNotBlank() }
        val metadata = mutableMapOf<String, String>()
        val retained = mutableListOf<String>()
        queryParts.forEach { part ->
            val rawKey = part.substringBefore('=')
            val key = decodeQuery(rawKey)
            if (key.startsWith("eg_")) {
                require(key !in metadata) { "Duplicate device-gate field" }
                metadata[key] = decodeQuery(part.substringAfter('=', ""))
            } else {
                retained += part
            }
        }

        require(metadata["eg_v"] == PROTOCOL_VERSION.toString()) { "Unsupported device gate" }
        val gatewayHost = metadata.getValue("eg_host").trim()
        val serverName = metadata.getValue("eg_sni").trim().lowercase(Locale.ROOT)
        val spkiSha256 = metadata.getValue("eg_spki").trim().lowercase(Locale.ROOT)
        val gatewayPort = metadata.getValue("eg_port").toIntOrNull()
            ?.takeIf { it in 1..65535 }
            ?: error("Invalid device-gate port")
        val assignmentId = metadata.getValue("eg_assignment").toLongOrNull()
            ?.takeIf { it > 0 }
            ?: error("Invalid device-gate assignment")
        val nodeId = metadata.getValue("eg_node").toLongOrNull()
            ?.takeIf { it > 0 }
            ?: error("Invalid device-gate node")
        require(safeHost.matches(gatewayHost)) { "Invalid device-gate host" }
        require(safeHost.matches(serverName)) { "Invalid device-gate TLS name" }
        require(safePin.matches(spkiSha256)) { "Invalid device-gate certificate pin" }
        require(!gatewayHost.startsWith('.') && !gatewayHost.endsWith('.')) { "Invalid device-gate host" }
        require(!serverName.startsWith('.') && !serverName.endsWith('.')) { "Invalid device-gate TLS name" }

        saveDescriptor(
            credential,
            Descriptor(
                gatewayHost = gatewayHost,
                gatewayPort = gatewayPort,
                serverName = serverName,
                spkiSha256 = spkiSha256,
                assignmentId = assignmentId,
                nodeId = nodeId,
            ),
        )

        val userInfo = uri.rawUserInfo ?: error("Missing VLESS credential")
        val query = retained.joinToString("&")
        return buildString {
            append("vless://")
            append(userInfo)
            append('@')
            append(LOCAL_HOST)
            append(':')
            append(LOCAL_PORT)
            if (query.isNotEmpty()) {
                append('?')
                append(query)
            }
            append('#')
            append(PUBLIC_PROFILE_NAME)
        }
    }

    fun isGateProfile(profile: ProfileItem?): Boolean =
        profile?.server == LOCAL_HOST && profile.serverPort?.toIntOrNull() == LOCAL_PORT

    fun descriptorFor(profile: ProfileItem?): Descriptor? {
        if (!isGateProfile(profile)) {
            return null
        }
        val credential = profile?.password?.trim().orEmpty()
        if (credential.isEmpty()) {
            return null
        }
        val raw = MmkvManager.decodeSettingsString(prefKey(credential))?.trim().orEmpty()
        if (raw.isEmpty()) {
            return null
        }
        return runCatching {
            val json = JSONObject(raw)
            require(json.getInt("version") == PROTOCOL_VERSION)
            Descriptor(
                gatewayHost = json.getString("gateway_host"),
                gatewayPort = json.getInt("gateway_port"),
                serverName = json.getString("server_name"),
                spkiSha256 = json.getString("spki_sha256"),
                assignmentId = json.getLong("assignment_id"),
                nodeId = json.getLong("node_id"),
                localPort = json.getInt("local_port"),
            ).also { descriptor ->
                require(safeHost.matches(descriptor.gatewayHost))
                require(safeHost.matches(descriptor.serverName))
                require(safePin.matches(descriptor.spkiSha256))
                require(descriptor.gatewayPort in 1..65535)
                require(descriptor.assignmentId > 0 && descriptor.nodeId > 0)
                require(descriptor.localPort == LOCAL_PORT)
            }
        }.getOrNull()
    }

    private fun sanitizePublicRemark(value: String): String {
        val trimmed = value.trim()
        if (!trimmed.startsWith("vless://", ignoreCase = true)) {
            return trimmed
        }
        return "${trimmed.substringBefore('#')}#$PUBLIC_PROFILE_NAME"
    }

    private fun hasGateMarker(value: String): Boolean =
        value.startsWith("vless://", ignoreCase = true) &&
            URI(value.trim()).rawQuery.orEmpty().split('&').any {
                decodeQuery(it.substringBefore('=')) == "eg_v"
            }

    private fun saveDescriptor(credential: String, descriptor: Descriptor) {
        val json = JSONObject()
            .put("version", PROTOCOL_VERSION)
            .put("gateway_host", descriptor.gatewayHost)
            .put("gateway_port", descriptor.gatewayPort)
            .put("server_name", descriptor.serverName)
            .put("spki_sha256", descriptor.spkiSha256)
            .put("assignment_id", descriptor.assignmentId)
            .put("node_id", descriptor.nodeId)
            .put("local_port", descriptor.localPort)
        check(MmkvManager.encodeSettings(prefKey(credential), json.toString())) {
            "Unable to persist device-gate metadata"
        }
    }

    private fun prefKey(credential: String): String = PREF_PREFIX + sha256Hex(credential)

    private fun decodeQuery(value: String): String =
        URLDecoder.decode(value, Charsets.UTF_8.name())

    private fun sha256Hex(value: String): String {
        val digest = MessageDigest.getInstance("SHA-256").digest(value.toByteArray(Charsets.UTF_8))
        return digest.joinToString("") { byte ->
            String.format(Locale.US, "%02x", byte.toInt() and 0xff)
        }
    }
}

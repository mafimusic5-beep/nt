package com.v2ray.ang.security

import android.util.Log
import com.v2ray.ang.AppConfig
import org.json.JSONObject
import java.io.Closeable
import java.io.InputStream
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.ServerSocket
import java.net.Socket
import java.net.SocketTimeoutException
import java.security.MessageDigest
import java.security.cert.X509Certificate
import java.util.Collections
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import javax.net.ssl.SNIHostName
import javax.net.ssl.SSLContext
import javax.net.ssl.SSLSocket

class DeviceBoundVlessProxy(
    private val protectSocket: (Socket) -> Boolean,
) : Closeable {

    companion object {
        private const val PROTOCOL_VERSION = 1
        private const val CONTROL_TIMEOUT_MILLIS = 10_000
        private const val CONNECT_TIMEOUT_MILLIS = 7_000
        private const val MAX_CONTROL_LINE_BYTES = 8_192

        fun resolve(descriptor: EmeryDeviceGateConfig.Descriptor): ResolvedDescriptor =
            ResolvedDescriptor(
                descriptor = descriptor,
                // Resolution intentionally happens before Android establishes
                // the VPN interface, avoiding a DNS bootstrap loop.
                gatewayAddress = InetAddress.getAllByName(descriptor.gatewayHost).first(),
            )
    }

    data class ResolvedDescriptor(
        val descriptor: EmeryDeviceGateConfig.Descriptor,
        val gatewayAddress: InetAddress,
    )

    private val executor: ExecutorService = Executors.newCachedThreadPool()
    private val openSockets = Collections.newSetFromMap(ConcurrentHashMap<Socket, Boolean>())
    @Volatile
    private var running = false
    @Volatile
    private var serverSocket: ServerSocket? = null

    @Synchronized
    fun start(resolved: ResolvedDescriptor): Boolean {
        if (running) {
            return true
        }
        return runCatching {
            val descriptor = resolved.descriptor
            val listener = ServerSocket().apply {
                reuseAddress = false
                bind(
                    InetSocketAddress(
                        InetAddress.getByName(EmeryDeviceGateConfig.LOCAL_HOST),
                        descriptor.localPort,
                    ),
                    128,
                )
            }
            serverSocket = listener
            running = true
            executor.execute { acceptLoop(listener, resolved.gatewayAddress, descriptor) }
            true
        }.getOrElse { error ->
            Log.e(AppConfig.TAG, "Device gate failed to start", error)
            stop()
            false
        }
    }

    private fun acceptLoop(
        listener: ServerSocket,
        gatewayAddress: InetAddress,
        descriptor: EmeryDeviceGateConfig.Descriptor,
    ) {
        while (running) {
            val localSocket = try {
                listener.accept()
            } catch (_: Exception) {
                break
            }
            if (!running) {
                localSocket.closeQuietly()
                break
            }
            openSockets += localSocket
            executor.execute { handleConnection(localSocket, gatewayAddress, descriptor) }
        }
    }

    private fun handleConnection(
        localSocket: Socket,
        gatewayAddress: InetAddress,
        descriptor: EmeryDeviceGateConfig.Descriptor,
    ) {
        var gatewaySocket: SSLSocket? = null
        var rawGatewaySocket: Socket? = null
        var stage = "socket_create"
        try {
            val rawSocket = Socket()
            rawGatewaySocket = rawSocket
            openSockets += rawSocket
            stage = "socket_bind"
            rawSocket.bind(InetSocketAddress(0))
            stage = "socket_protect"
            check(protectSocket(rawSocket)) { "Unable to protect device-gate socket" }
            stage = "tcp_connect"
            rawSocket.connect(
                InetSocketAddress(gatewayAddress, descriptor.gatewayPort),
                CONNECT_TIMEOUT_MILLIS,
            )

            stage = "tls_handshake"
            val tlsSocket = (SSLContext.getDefault().socketFactory.createSocket(
                rawSocket,
                descriptor.serverName,
                descriptor.gatewayPort,
                true,
            ) as SSLSocket).apply {
                soTimeout = CONTROL_TIMEOUT_MILLIS
                sslParameters = sslParameters.apply {
                    endpointIdentificationAlgorithm = "HTTPS"
                    runCatching { serverNames = listOf(SNIHostName(descriptor.serverName)) }
                }
                startHandshake()
            }
            gatewaySocket = tlsSocket
            openSockets -= rawSocket
            rawGatewaySocket = null
            openSockets += tlsSocket
            stage = "tls_pin"
            verifyGatewayPin(tlsSocket, descriptor.spkiSha256)

            stage = "challenge"
            val challenge = JSONObject(readControlLine(tlsSocket.inputStream))
            check(challenge.length() == 3)
            check(challenge.getInt("version") == PROTOCOL_VERSION)
            val serverIssuedAt = challenge.getString("server_issued_at")
            val serverNonce = challenge.getString("server_nonce")
            check(serverNonce.length in 16..128)
            val issuedAtMillis = serverIssuedAt.toLong()
            check(kotlin.math.abs(System.currentTimeMillis() - issuedAtMillis) <= 30_000)

            val proof = EmeryDeviceIdentity.buildGatewayProof(
                assignmentId = descriptor.assignmentId,
                nodeId = descriptor.nodeId,
                gateServerName = descriptor.serverName,
                gateSpkiSha256 = descriptor.spkiSha256,
                serverIssuedAt = serverIssuedAt,
                serverNonce = serverNonce,
            )
            val proofJson = JSONObject()
                .put("version", PROTOCOL_VERSION)
                .put("assignment_id", descriptor.assignmentId)
                .put("node_id", descriptor.nodeId)
                .put("gate_server_name", descriptor.serverName)
                .put("gate_spki_sha256", descriptor.spkiSha256)
                .put("device_id", proof.deviceId)
                .put("server_issued_at", serverIssuedAt)
                .put("timestamp", proof.timestampMillis)
                .put("server_nonce", serverNonce)
                .put("client_nonce", proof.clientNonce)
                .put("signature", proof.signatureBase64)
                .put("signature_algorithm", proof.signatureAlgorithm)
            tlsSocket.outputStream.write((proofJson.toString() + "\n").toByteArray(Charsets.UTF_8))
            tlsSocket.outputStream.flush()

            stage = "authorization"
            val authorization = JSONObject(readControlLine(tlsSocket.inputStream))
            check(authorization.length() == 1 && authorization.optBoolean("ok", false))
            tlsSocket.soTimeout = 0
            localSocket.soTimeout = 0

            val upstream = executor.submit {
                runCatching { copy(localSocket.inputStream, tlsSocket.outputStream) }
                tlsSocket.closeQuietly()
            }
            runCatching { copy(tlsSocket.inputStream, localSocket.outputStream) }
            upstream.cancel(true)
        } catch (_: SocketTimeoutException) {
            Log.w(AppConfig.TAG, "Device gate connection timed out: stage=$stage")
        } catch (error: Exception) {
            Log.w(
                AppConfig.TAG,
                "Device gate connection rejected: stage=$stage error=${error.javaClass.simpleName}",
            )
        } finally {
            localSocket.closeQuietly()
            gatewaySocket?.closeQuietly()
            rawGatewaySocket?.closeQuietly()
            openSockets -= localSocket
            gatewaySocket?.let { socket -> openSockets -= socket }
            rawGatewaySocket?.let { socket -> openSockets -= socket }
        }
    }

    @Synchronized
    fun stop() {
        running = false
        serverSocket?.closeQuietly()
        serverSocket = null
        openSockets.toList().forEach { it.closeQuietly() }
        openSockets.clear()
        executor.shutdownNow()
    }

    override fun close() = stop()

    private fun copy(input: InputStream, output: java.io.OutputStream) {
        val buffer = ByteArray(64 * 1024)
        while (running) {
            val count = input.read(buffer)
            if (count < 0) {
                return
            }
            output.write(buffer, 0, count)
            output.flush()
        }
    }

    private fun readControlLine(input: InputStream): String {
        val output = java.io.ByteArrayOutputStream()
        while (output.size() <= MAX_CONTROL_LINE_BYTES) {
            val value = input.read()
            if (value < 0) {
                error("Unexpected end of control stream")
            }
            if (value == '\n'.code) {
                return output.toString(Charsets.UTF_8.name())
            }
            output.write(value)
        }
        error("Control message too large")
    }

    private fun verifyGatewayPin(socket: SSLSocket, expectedHex: String) {
        val certificate = socket.session.peerCertificates.firstOrNull() as? X509Certificate
            ?: error("Gateway certificate missing")
        val actual = MessageDigest.getInstance("SHA-256").digest(certificate.publicKey.encoded)
        val expected = expectedHex.chunked(2).map { it.toInt(16).toByte() }.toByteArray()
        check(MessageDigest.isEqual(actual, expected)) { "Gateway certificate pin mismatch" }
    }

    private fun Closeable.closeQuietly() {
        runCatching { close() }
    }
}

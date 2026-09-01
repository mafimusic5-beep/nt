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
import java.util.concurrent.ScheduledThreadPoolExecutor
import java.util.concurrent.TimeUnit
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

        fun resolve(
            descriptor: EmeryDeviceGateConfig.Descriptor,
            regionalPolicy: String = "international",
        ): ResolvedDescriptor =
            ResolvedDescriptor(
                descriptor = descriptor,
                // Resolution intentionally happens before Android establishes
                // the VPN interface, avoiding a DNS bootstrap loop.
                gatewayAddress = InetAddress.getAllByName(descriptor.gatewayHost).first(),
                regionalPolicy = regionalPolicy,
            )
    }

    data class ResolvedDescriptor(
        val descriptor: EmeryDeviceGateConfig.Descriptor,
        val gatewayAddress: InetAddress,
        val regionalPolicy: String = "international",
    ) {
        init {
            require(regionalPolicy == "international" || regionalPolicy == "russia")
        }
    }

    private val executor: ExecutorService = Executors.newCachedThreadPool()
    private val deadlines = ScheduledThreadPoolExecutor(1).apply { removeOnCancelPolicy = true }
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
            executor.execute { acceptLoop(listener, resolved) }
            true
        }.getOrElse { error ->
            Log.e(AppConfig.TAG, "Device gate failed to start", error)
            stop()
            false
        }
    }

    private fun acceptLoop(
        listener: ServerSocket,
        resolved: ResolvedDescriptor,
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
            executor.execute { handleConnection(localSocket, resolved) }
        }
    }

    private fun handleConnection(
        localSocket: Socket,
        resolved: ResolvedDescriptor,
    ) {
        var gatewaySocket: SSLSocket? = null
        try {
            val tlsSocket = openAuthorizedGateway(resolved, "connect")
            gatewaySocket = tlsSocket
            tlsSocket.soTimeout = 0
            localSocket.soTimeout = 0
            val upstream = executor.submit {
                runCatching { copy(localSocket.inputStream, tlsSocket.outputStream) }
                tlsSocket.closeQuietly()
            }
            runCatching { copy(tlsSocket.inputStream, localSocket.outputStream) }
            upstream.cancel(true)
        } catch (_: SocketTimeoutException) {
            Log.w(AppConfig.TAG, "Device gate connection timed out")
        } catch (error: Exception) {
            Log.w(AppConfig.TAG, "Device gate connection rejected: error=${error.javaClass.simpleName}")
        } finally {
            localSocket.closeQuietly()
            gatewaySocket?.closeQuietly()
            openSockets -= localSocket
            gatewaySocket?.let { socket -> openSockets -= socket }
        }
    }

    /** Small authenticated check before the VPN starts. No lists are transferred. */
    fun checkRegionalPolicy(resolved: ResolvedDescriptor): Result<Unit> = runCatching {
        check(resolved.regionalPolicy == "russia")
        openAuthorizedGateway(resolved, "check").use { socket -> openSockets -= socket }
    }

    private fun openAuthorizedGateway(resolved: ResolvedDescriptor, operation: String): SSLSocket {
        val descriptor = resolved.descriptor
        val restricted = resolved.regionalPolicy == "russia"
        val rawSocket = Socket()
        var gatewaySocket: SSLSocket? = null
        openSockets += rawSocket
        // A whole-exchange deadline also bounds TLS and slow/drip-fed control lines.
        val deadline = try {
            deadlines.schedule(
                Runnable { rawSocket.closeQuietly() }, if (operation == "check") 8L else 25L, TimeUnit.SECONDS,
            )
        } catch (error: Exception) {
            rawSocket.closeQuietly()
            openSockets -= rawSocket
            throw error
        }
        try {
            rawSocket.bind(InetSocketAddress(0))
            check(protectSocket(rawSocket)) { "Unable to protect device-gate socket" }
            rawSocket.connect(
                InetSocketAddress(resolved.gatewayAddress, descriptor.gatewayPort),
                CONNECT_TIMEOUT_MILLIS,
            )

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
            openSockets += tlsSocket
            verifyGatewayPin(tlsSocket, descriptor.spkiSha256)

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
                regionalPolicy = resolved.regionalPolicy,
                operation = operation,
            )
            val proofJson = JSONObject()
                .put("version", if (restricted) 2 else PROTOCOL_VERSION)
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
            if (restricted) {
                proofJson.put("regional_policy", "russia").put("operation", operation)
            }
            tlsSocket.outputStream.write((proofJson.toString() + "\n").toByteArray(Charsets.UTF_8))
            tlsSocket.outputStream.flush()

            val authorization = JSONObject(readControlLine(tlsSocket.inputStream))
            check(authorization.optBoolean("ok", false)) { "Device gate authorization denied" }
            if (restricted) {
                check(authorization.length() == 4 &&
                    authorization.optInt("protocol_version") == 2 &&
                    authorization.optString("regional_policy") == "russia" &&
                    authorization.optString("operation") == operation) { "Regional policy not confirmed" }
            } else {
                check(authorization.length() == 1)
            }
            return tlsSocket
        } catch (error: Exception) {
            gatewaySocket?.closeQuietly()
            rawSocket.closeQuietly()
            openSockets -= rawSocket
            gatewaySocket?.let { socket -> openSockets -= socket }
            throw error
        } finally {
            deadline.cancel(false)
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
        deadlines.shutdownNow()
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

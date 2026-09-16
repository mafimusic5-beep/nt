package com.v2ray.ang.security

import android.util.Log
import com.v2ray.ang.AppConfig
import org.json.JSONObject
import java.io.Closeable
import java.io.InputStream
import java.net.BindException
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.ServerSocket
import java.net.Socket
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
        private const val BIND_RETRY_ATTEMPTS = 8
        private const val BIND_RETRY_DELAY_MILLIS = 150L

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
            val listener = bindListener(descriptor.localPort)
            serverSocket = listener
            running = true
            // Authenticate before reporting successful startup. Keep one
            // control connection alive even when the user has no traffic.
            val session = openGateway(resolved.gatewayAddress, descriptor, sessionOnly = true)
            if (session != null) {
                executor.execute {
                    try {
                        while (running) {
                            session.outputStream.write("{\"ping\":true}\n".toByteArray(Charsets.UTF_8))
                            session.outputStream.flush()
                            check(JSONObject(readControlLine(session.inputStream)).optBoolean("ok"))
                            Thread.sleep(15_000)
                        }
                    } catch (_: Exception) {
                        if (running) stop()
                    } finally {
                        session.closeQuietly()
                        openSockets -= session
                    }
                }
            }
            executor.execute { acceptLoop(listener, resolved.gatewayAddress, descriptor) }
            true
        }.getOrElse { error ->
            Log.e(AppConfig.TAG, "Device gate failed to start", error)
            stop()
            false
        }
    }

    private fun bindListener(localPort: Int): ServerSocket {
        var lastBindError: BindException? = null
        repeat(BIND_RETRY_ATTEMPTS) { index ->
            val listener = ServerSocket()
            try {
                // Reconnects can leave accepted sockets in TIME_WAIT even after the
                // previous listener is closed. SO_REUSEADDR allows the fixed local
                // device-gate port to be rebound immediately by the next VPN session.
                listener.reuseAddress = true
                listener.bind(
                    InetSocketAddress(
                        InetAddress.getByName(EmeryDeviceGateConfig.LOCAL_HOST),
                        localPort,
                    ),
                    128,
                )
                if (index > 0) {
                    Log.i(AppConfig.TAG, "Device gate rebound after ${index + 1} attempts")
                }
                return listener
            } catch (error: BindException) {
                lastBindError = error
                listener.closeQuietly()
                if (index + 1 < BIND_RETRY_ATTEMPTS) {
                    Log.w(
                        AppConfig.TAG,
                        "Device gate port busy; retrying bind (${index + 1}/$BIND_RETRY_ATTEMPTS)",
                    )
                    try {
                        Thread.sleep(BIND_RETRY_DELAY_MILLIS)
                    } catch (interrupted: InterruptedException) {
                        Thread.currentThread().interrupt()
                        throw error
                    }
                }
            } catch (error: Exception) {
                listener.closeQuietly()
                throw error
            }
        }
        throw lastBindError ?: BindException("Device gate local port is unavailable")
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

    private fun openGateway(
        gatewayAddress: InetAddress,
        descriptor: EmeryDeviceGateConfig.Descriptor,
        sessionOnly: Boolean,
    ): SSLSocket? {
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
            val protocolVersion = challenge.getInt("version")
            check(protocolVersion == PROTOCOL_VERSION || protocolVersion == 2)
            if (sessionOnly && protocolVersion == PROTOCOL_VERSION) {
                tlsSocket.closeQuietly()
                openSockets -= tlsSocket
                return null
            }
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
                .put("version", protocolVersion)
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
            if (protocolVersion == 2) proofJson.put("session_only", sessionOnly)
            tlsSocket.outputStream.write((proofJson.toString() + "\n").toByteArray(Charsets.UTF_8))
            tlsSocket.outputStream.flush()

            stage = "authorization"
            val authorization = JSONObject(readControlLine(tlsSocket.inputStream))
            check(authorization.length() == 1 && authorization.optBoolean("ok", false))
            tlsSocket.soTimeout = if (sessionOnly) CONTROL_TIMEOUT_MILLIS else 0
            return tlsSocket
        } catch (error: Exception) {
            gatewaySocket?.closeQuietly()
            rawGatewaySocket?.closeQuietly()
            gatewaySocket?.let { openSockets -= it }
            rawGatewaySocket?.let { openSockets -= it }
            Log.w(AppConfig.TAG, "Device gate connection rejected: stage=$stage error=${error.javaClass.simpleName}")
            throw error
        }
    }

    private fun handleConnection(
        localSocket: Socket,
        gatewayAddress: InetAddress,
        descriptor: EmeryDeviceGateConfig.Descriptor,
    ) {
        var gatewaySocket: SSLSocket? = null
        try {
            val tlsSocket = checkNotNull(openGateway(gatewayAddress, descriptor, sessionOnly = false))
            gatewaySocket = tlsSocket
            localSocket.soTimeout = 0
            val upstream = executor.submit {
                runCatching { copy(localSocket.inputStream, tlsSocket.outputStream) }
                tlsSocket.closeQuietly()
            }
            runCatching { copy(tlsSocket.inputStream, localSocket.outputStream) }
            upstream.cancel(true)
        } catch (_: Exception) {
            // Failed authorization never forwards user traffic.
        } finally {
            localSocket.closeQuietly()
            gatewaySocket?.closeQuietly()
            openSockets -= localSocket
            gatewaySocket?.let { openSockets -= it }
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

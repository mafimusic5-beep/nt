package com.v2ray.ang.ui.premium

import android.util.Base64
import java.security.KeyPairGenerator
import java.security.MessageDigest
import java.security.PrivateKey
import java.security.spec.MGF1ParameterSpec
import java.util.concurrent.TimeUnit
import javax.crypto.Cipher
import javax.crypto.spec.OAEPParameterSpec
import javax.crypto.spec.PSource
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject

/**
 * TV -> short-lived encrypted relay <- phone.
 * The server sees only an RSA-OAEP ciphertext, never an activation code.
 * The TV uses its normal signed activation proof after decrypting.
 */
internal object TvPairingClient {
    private const val ORIGIN = "https://skryon.ru"
    private const val API = "$ORIGIN/api/tv/pair"
    private val http = OkHttpClient.Builder()
        .connectTimeout(12, TimeUnit.SECONDS)
        .readTimeout(12, TimeUnit.SECONDS)
        .callTimeout(15, TimeUnit.SECONDS)
        .retryOnConnectionFailure(true)
        .build()

    data class Session(
        val ticket: String,
        val pollSecret: String,
        val url: String,
        val expiresAt: Long,
        val privateKey: PrivateKey,
    )

    sealed class PollResult {
        data object Waiting : PollResult()
        data object Expired : PollResult()
        data class Received(val activationCode: String) : PollResult()
    }

    private fun call(path: String, payload: JSONObject): Pair<Int, JSONObject> {
        val request = Request.Builder()
            .url(API + path)
            .post(payload.toString().toRequestBody("application/json; charset=utf-8".toMediaType()))
            .header("Accept", "application/json")
            .header("Cache-Control", "no-store")
            .build()
        http.newCall(request).execute().use { response ->
            val body = response.body?.string().orEmpty()
            return response.code to runCatching { JSONObject(body) }.getOrDefault(JSONObject())
        }
    }

    suspend fun start(): Session = withContext(Dispatchers.IO) {
        val pair = KeyPairGenerator.getInstance("RSA").apply { initialize(2048) }.generateKeyPair()
        val publicKey = Base64.encodeToString(pair.public.encoded, Base64.NO_WRAP)
        val (status, result) = call("/start", JSONObject().put("public_key", publicKey))
        check(status == 200 && result.optBoolean("ok")) { "Сервер QR-активации недоступен" }
        val ticket = result.getString("ticket")
        val pollSecret = result.getString("poll_secret")
        val url = result.getString("url")
        check(Regex("^[A-Za-z0-9_-]{32}$").matches(ticket)) { "Некорректный QR-токен" }
        val keyHash = MessageDigest.getInstance("SHA-256")
            .digest(pair.public.encoded)
            .joinToString("") { byte -> "%02x".format(byte.toInt() and 0xff) }
        check(url == "$ORIGIN/api/tv/activate?ticket=$ticket#key=$keyHash") {
            "Некорректная ссылка активации"
        }
        check(pollSecret.length >= 32) { "Некорректный секрет сеанса" }
        Session(
            ticket = ticket,
            pollSecret = pollSecret,
            url = url,
            expiresAt = System.currentTimeMillis() + result.optLong("expires_in", 300L) * 1_000,
            privateKey = pair.private,
        )
    }

    suspend fun poll(session: Session): PollResult = withContext(Dispatchers.IO) {
        if (System.currentTimeMillis() >= session.expiresAt) return@withContext PollResult.Expired
        val (status, result) = call(
            "/poll",
            JSONObject()
                .put("ticket", session.ticket)
                .put("poll_secret", session.pollSecret),
        )
        if (status == 410 || status == 404) return@withContext PollResult.Expired
        check(status == 200 && result.optBoolean("ok")) { "Ошибка ожидания активации" }
        if (result.optString("status") != "submitted") return@withContext PollResult.Waiting
        val ciphertext = Base64.decode(result.getString("ciphertext"), Base64.DEFAULT)
        check(ciphertext.size == 256) { "Некорректный зашифрованный код" }
        val cipher = Cipher.getInstance("RSA/ECB/OAEPPadding")
        cipher.init(
            Cipher.DECRYPT_MODE,
            session.privateKey,
            OAEPParameterSpec("SHA-256", "MGF1", MGF1ParameterSpec.SHA256, PSource.PSpecified.DEFAULT),
        )
        val code = String(cipher.doFinal(ciphertext), Charsets.UTF_8)
        check(Regex("^[A-Z0-9]{11}$").matches(code)) { "Некорректный код активации" }
        PollResult.Received(code)
    }
}

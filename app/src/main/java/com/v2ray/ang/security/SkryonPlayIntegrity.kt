package com.v2ray.ang.security

import android.content.Context
import com.google.android.gms.tasks.Task
import com.google.android.play.core.integrity.IntegrityManagerFactory
import com.google.android.play.core.integrity.StandardIntegrityManager.PrepareIntegrityTokenRequest
import com.google.android.play.core.integrity.StandardIntegrityManager.StandardIntegrityTokenRequest
import com.v2ray.ang.BuildConfig
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException
import kotlinx.coroutines.suspendCancellableCoroutine

object SkryonPlayIntegrity {

    suspend fun requestRecoveryToken(context: Context, requestHash: String): String {
        val cloudProjectNumber = BuildConfig.SKRYON_PLAY_INTEGRITY_CLOUD_PROJECT_NUMBER
        require(cloudProjectNumber > 0L) { "play_integrity_not_configured" }
        require(requestHash.isNotBlank() && requestHash.length <= 500) { "play_integrity_request_hash_invalid" }

        val manager = IntegrityManagerFactory.createStandard(context.applicationContext)
        val provider = manager.prepareIntegrityToken(
            PrepareIntegrityTokenRequest.builder()
                .setCloudProjectNumber(cloudProjectNumber)
                .build(),
        ).awaitTask()

        return provider.request(
            StandardIntegrityTokenRequest.builder()
                .setRequestHash(requestHash)
                .build(),
        ).awaitTask().token()
    }

    private suspend fun <T> Task<T>.awaitTask(): T = suspendCancellableCoroutine { continuation ->
        addOnSuccessListener { value ->
            if (continuation.isActive) continuation.resume(value)
        }
        addOnFailureListener { error ->
            if (continuation.isActive) continuation.resumeWithException(error)
        }
        addOnCanceledListener {
            continuation.cancel()
        }
    }
}

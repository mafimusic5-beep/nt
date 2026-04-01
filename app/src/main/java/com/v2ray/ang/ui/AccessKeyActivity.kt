package com.v2ray.ang.ui

import android.content.Intent
import android.os.Bundle
import android.view.View
import androidx.lifecycle.lifecycleScope
import com.v2ray.ang.R
import com.v2ray.ang.databinding.ActivityAccessKeyBinding
import com.v2ray.ang.handler.EmeryAccessManager
import com.v2ray.ang.handler.EmeryVpnSync
import com.v2ray.ang.network.EmeryAuthClient
import com.v2ray.ang.util.AgentDebugNdjsonLogger
import kotlinx.coroutines.launch
import org.json.JSONObject

class AccessKeyActivity : BaseActivity() {

    private lateinit var binding: ActivityAccessKeyBinding

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityAccessKeyBinding.inflate(layoutInflater)
        setContentViewWithToolbar(binding.root, showHomeAsUp = false, title = getString(R.string.title_access_key))

        if (EmeryAccessManager.isActivated()) {
            openHubAndFinish()
            return
        }

        binding.buttonActivate.setOnClickListener { onActivateClicked() }
    }

    private fun openHubAndFinish() {
        startActivity(Intent(this, HubActivity::class.java))
        finish()
    }

    private fun onActivateClicked() {
        binding.textError.visibility = View.GONE
        val key = binding.editKey.text?.toString().orEmpty()
        // #region agent log
        AgentDebugNdjsonLogger.log(
            hypothesisId = "H3",
            location = "AccessKeyActivity.kt:onActivateClicked",
            message = "activate_clicked",
            runId = "pre-fix",
            data = JSONObject()
                .put("keyLen", key.length)
                .put("keyBlank", key.isBlank())
                .put("wasActivated", EmeryAccessManager.isActivated()),
        )
        // #endregion
        if (key.isBlank()) {
            binding.textError.setText(R.string.emery_error_bad_request)
            binding.textError.visibility = View.VISIBLE
            return
        }

        binding.buttonActivate.isEnabled = false
        showLoading()
        lifecycleScope.launch {
            val result = EmeryAuthClient.verifyAccessKey(key)
            // #region agent log
            AgentDebugNdjsonLogger.log(
                hypothesisId = "H1",
                location = "AccessKeyActivity.kt:onActivateClicked",
                message = "verify_access_key_result",
                runId = "pre-fix",
                data = JSONObject()
                    .put("success", result.isSuccess)
                    .put("error", result.exceptionOrNull()?.message ?: ""),
            )
            // #endregion
            result.fold(
                onSuccess = { profile ->
                    EmeryAccessManager.saveProfile(profile)
                    val sync = EmeryVpnSync.syncProfileAndVpnConfig(profile.accessKey)
                    hideLoading()
                    binding.buttonActivate.isEnabled = true
                    sync.fold(
                        onSuccess = { openHubAndFinish() },
                        onFailure = { e ->
                            // #region agent log
                            AgentDebugNdjsonLogger.log(
                                hypothesisId = "H3",
                                location = "AccessKeyActivity.kt:onActivateClicked",
                                message = "sync_profile_vpn_failed",
                                runId = "pre-fix",
                                data = JSONObject().put("error", e.message ?: ""),
                            )
                            // #endregion
                            val msg = when (e.message) {
                                "invalid_or_expired_key" -> getString(R.string.emery_error_invalid_key)
                                "bad_request" -> getString(R.string.emery_error_bad_request)
                                "network" -> getString(R.string.emery_error_network)
                                "parse_error" -> getString(R.string.emery_error_unknown)
                                else -> getString(R.string.emery_error_unknown)
                            }
                            binding.textError.text = msg
                            binding.textError.visibility = View.VISIBLE
                        },
                    )
                },
                onFailure = { e ->
                    hideLoading()
                    binding.buttonActivate.isEnabled = true
                    val msg = when (e.message) {
                        "invalid_or_expired_key" -> getString(R.string.emery_error_invalid_key)
                        "bad_request" -> getString(R.string.emery_error_bad_request)
                        "network" -> getString(R.string.emery_error_network)
                        "parse_error" -> getString(R.string.emery_error_unknown)
                        else -> getString(R.string.emery_error_unknown)
                    }
                    binding.textError.text = msg
                    binding.textError.visibility = View.VISIBLE
                },
            )
        }
    }
}

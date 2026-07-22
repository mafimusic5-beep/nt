package com.v2ray.ang.ui

import android.content.Intent
import android.os.Bundle
import androidx.core.view.isVisible
import androidx.lifecycle.lifecycleScope
import com.v2ray.ang.AppConfig
import com.v2ray.ang.R
import com.v2ray.ang.databinding.ActivityHubBinding
import com.v2ray.ang.handler.EmeryAccessManager
import com.v2ray.ang.handler.EmeryVpnSync
import com.v2ray.ang.handler.MmkvManager
import kotlinx.coroutines.launch

class HubActivity : BaseActivity() {

    private lateinit var binding: ActivityHubBinding

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityHubBinding.inflate(layoutInflater)
        setContentViewWithToolbar(binding.root, showHomeAsUp = false, title = getString(R.string.title_hub))

        val profile = EmeryAccessManager.loadProfile()
        if (profile == null) {
            startActivity(Intent(this, AccessKeyActivity::class.java))
            finish()
            return
        }

        binding.textPlan.text = getString(R.string.emery_plan_label) + ": " + profile.planName
        binding.textExpires.text = getString(R.string.emery_expires_label) + ": " + profile.expiresAt

        binding.cardVpn.isVisible = profile.vpnEnabled
        // Remote synchronization is opt-in and disabled by default.
        if (MmkvManager.decodeSettingsBool(AppConfig.SUBSCRIPTION_AUTO_UPDATE, false)) {
            lifecycleScope.launch {
                EmeryVpnSync.syncProfileAndVpnConfig(profile.accessKey)
            }
        }

        binding.cardVpn.setOnClickListener {
            startActivity(Intent(this, MainActivity::class.java))
        }
    }
}

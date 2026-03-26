package com.v2ray.ang.ui

import android.content.Intent
import android.os.Bundle
import androidx.lifecycle.lifecycleScope
import androidx.core.view.isVisible
import com.v2ray.ang.R
import com.v2ray.ang.databinding.ActivityHubBinding
import com.v2ray.ang.extension.toast
import com.v2ray.ang.handler.EmeryAccessManager
import com.v2ray.ang.handler.EmeryVpnSync
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
        // Keep already-activated users up-to-date with newly available nodes.
        lifecycleScope.launch {
            EmeryVpnSync.syncProfileAndVpnConfig(profile.accessKey)
        }

        binding.cardVpn.setOnClickListener {
            startActivity(Intent(this, MainActivity::class.java))
        }
    }
}

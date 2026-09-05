from __future__ import annotations

import json
from functools import cached_property

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "dev"
    log_level: str = "INFO"

    backend_host: str = "0.0.0.0"
    backend_port: int = 9330
    backend_base_url: str = "http://localhost:9330"
    public_domain: str = "localhost"
    internal_api_key: str = ""
    admin_api_key: str = ""

    bot_token: str = ""
    admin_ids: str = ""

    db_url: str = "sqlite:///./data/app.db"

    brand_name: str = "Emery VPN"
    support_url: str = "https://t.me/your_support"
    channel_url: str = "https://t.me/your_channel"

    payment_provider: str = "stub"
    invoice_expires_in: int = 1800
    invoice_check_interval: int = 12

    default_region_code: str = "moscow"
    default_region_name: str = "Moscow"
    max_devices_per_subscription: int = 5
    pool_node_capacity_devices: int = 20
    pool_family_headroom_devices: int = 5
    pool_node_bandwidth_mbps: int = 600
    pool_per_device_speed_limit_mbps: int = 30
    pool_accounting_bridge_enabled: bool = False
    unique_device_credentials_enabled: bool = False
    per_device_rate_limit_enforced: bool = False
    smtp_abuse_protection_enabled: bool = False
    # A VLESS UUID is a bearer credential. Unique-device mode is only allowed
    # when every connection is forced through the proof-of-possession gateway;
    # otherwise a copied vless:// URI would still be usable by another client.
    device_bound_gate_enabled: bool = False
    device_gate_client_loopback_port: int = 17890
    device_gate_service_name: str = "emery-device-gate"
    device_gate_api_key: str = ""
    pool_bridge_api_key: str = ""
    pool_assignment_prepare_ttl_seconds: int = 300
    pool_assignment_maintenance_interval_seconds: int = 60
    xray_config_path: str = "/usr/local/etc/xray/config.json"
    xray_client_port_start: int = 20000
    xray_client_port_end: int = 20199
    xray_credential_script: str = ""
    xray_credential_timeout_seconds: int = 90
    rate_limit_per_minute: int = 60
    min_supported_app_version_code: int = 718
    app_update_message: str = "Версия приложения устарела. Обновите приложение."
    healthcheck_interval_seconds: int = 30
    node_provision_script: str = ""
    node_provision_script_timeout_seconds: int = 900
    node_deprovision_script: str = ""
    node_healthcheck_script: str = ""
    node_renewal_script: str = ""
    auto_renewal_actions_enabled: bool = False
    renewal_planning_horizon_days: int = 14
    renewal_planning_interval_seconds: int = 21600

    # Manual VPS bootstrap from the admin bot. The supplied SSH password is
    # used only for the initial connection and is never persisted.
    manual_bootstrap_ssh_timeout_seconds: int = 30
    manual_bootstrap_vless_port: int = 443
    manual_bootstrap_reality_sni: str = "www.cloudflare.com"
    manual_bootstrap_device_gate_port: int = 8447
    manual_bootstrap_device_gate_authorize_url: str = "https://skryon.ru/api/device-gate/authorize"

    # Optional high-quality ISP egress. When enabled, /setup_server still needs
    # only the new VPS IP/password: the control backend allocates a unique
    # WireGuard tunnel IP, registers the peer on the preconfigured ISP egress,
    # and binds Xray's direct outbound to that tunnel with a kill switch.
    manual_bootstrap_isp_egress_enabled: bool = False
    manual_bootstrap_isp_egress_host: str = ""
    manual_bootstrap_isp_egress_port: int = 51820
    manual_bootstrap_isp_egress_public_key: str = ""
    manual_bootstrap_isp_egress_interface: str = "wg-skryon"
    manual_bootstrap_isp_egress_cidr: str = "10.77.0.0/16"
    manual_bootstrap_isp_egress_mtu: int = 1380
    manual_bootstrap_isp_egress_keepalive_seconds: int = 25
    manual_bootstrap_isp_egress_routing_table: int = 51820
    manual_bootstrap_isp_egress_ssh_host: str = ""
    manual_bootstrap_isp_egress_ssh_port: int = 22
    manual_bootstrap_isp_egress_ssh_user: str = "root"
    manual_bootstrap_isp_egress_ssh_private_key_path: str = ""
    manual_bootstrap_isp_egress_ssh_known_hosts_path: str = ""
    manual_bootstrap_isp_egress_ssh_timeout_seconds: int = 15
    manual_bootstrap_isp_egress_allow_unknown_host_keys: bool = False

    # Dedicated recovery-agent. It probes the current VLESS listener and first
    # repairs the existing VPS; replacement capacity is a later fallback.
    recovery_probe_interval_seconds: int = 15
    recovery_max_parallel_nodes: int = 32
    recovery_probe_timeout_seconds: float = 3.0
    recovery_failure_threshold: int = 3
    recovery_restart_grace_seconds: int = 10
    recovery_reboot_grace_seconds: int = 45
    recovery_reboot_probe_interval_seconds: int = 10
    recovery_reboot_probe_attempts: int = 12
    recovery_lock_seconds: int = 600
    recovery_cooldown_seconds: int = 300
    recovery_ssh_user: str = "root"
    recovery_ssh_private_key_path: str = ""
    recovery_ssh_connect_timeout_seconds: int = 10
    recovery_ssh_known_hosts_path: str = ""
    recovery_allow_unknown_host_keys: bool = False
    recovery_provider_reboot_script: str = ""
    recovery_heartbeat_file: str = "/tmp/emery-recovery-agent.heartbeat"

    # Scale-out guardrails. Automatic purchases stay disabled until a provider
    # contract and a real, documented ordering API are configured.
    auto_provision_enabled: bool = False
    auto_provision_provider: str = "unconfigured"
    auto_provision_server_monthly_cost_eur: int = 0
    auto_provision_max_servers_per_hour: int = 1
    auto_provision_max_servers_per_day: int = 2
    auto_provision_monthly_budget_eur: int = 0
    auto_provision_retry_seconds: int = 300

    # Legacy placeholders kept for compatibility.
    firstvds_api_url: str = "https://api.firstvds.ru"
    firstvds_api_token: str = ""
    firstvds_project_id: str = ""

    # Real BILLmanager auth flow.
    firstvds_billmgr_url: str = "https://my.firstvds.ru/billmgr"
    firstvds_login: str = ""
    firstvds_password: str = ""
    firstvds_allowed_ip: str = ""
    firstvds_verify_ssl: bool = True
    firstvds_timeout_seconds: float = 20.0

    # Product profile for automated VDS ordering.
    firstvds_order_datacenter: str = ""
    firstvds_order_period: str = "1"
    firstvds_order_pricelist: str = ""
    firstvds_order_ostempl: str = ""
    firstvds_order_recipe: str = "null"
    firstvds_order_itemtype: str = "3"
    firstvds_order_domain_suffix: str = "vpn.local"
    firstvds_order_skipbasket: bool = True
    firstvds_order_addons_json: str = "{}"
    firstvds_auto_configure_xray: bool = True
    firstvds_ssh_user: str = "root"
    firstvds_ssh_private_key_path: str = ""
    firstvds_ssh_connect_timeout_seconds: int = 25
    firstvds_vless_port: int = 443
    firstvds_reality_sni: str = "www.cloudflare.com"
    firstvds_password_bootstrap_enabled: bool = True
    firstvds_node_ssh_key_autogenerate: bool = True
    firstvds_node_ssh_key_bits: int = 4096
    firstvds_node_ssh_key_comment_prefix: str = "emery-node"

    @cached_property
    def admin_id_list(self) -> list[int]:
        raw = [item.strip() for item in self.admin_ids.split(",") if item.strip()]
        return [int(item) for item in raw]

    @cached_property
    def firstvds_order_addons(self) -> dict[str, str]:
        try:
            data = json.loads(self.firstvds_order_addons_json or "{}")
            return {str(k): str(v) for k, v in data.items()}
        except json.JSONDecodeError:
            return {}

    @property
    def firstvds_enabled(self) -> bool:
        return bool(self.firstvds_login and self.firstvds_password and self.firstvds_billmgr_url)


settings = Settings()

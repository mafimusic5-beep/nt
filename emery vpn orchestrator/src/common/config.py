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
    rate_limit_per_minute: int = 60
    healthcheck_interval_seconds: int = 30
    node_provision_script: str = ""
    node_deprovision_script: str = ""
    node_healthcheck_script: str = ""

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
    firstvds_reality_sni: str = "apple.com"
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

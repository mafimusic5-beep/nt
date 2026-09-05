from src.backend.services.manual_node_bootstrap_service import ManualNodeBootstrapService
from src.backend.services.traffic_policy_service import TrafficPolicyService


def test_assignment_id_is_read_from_device_bound_uri():
    uri = (
        "vless://abc@127.0.0.1:17890?security=reality"
        "&eg_assignment=42&eg_node=7#Server"
    )
    assert TrafficPolicyService.assignment_id_from_import_text(uri) == 42


def test_shared_legacy_uri_has_no_server_side_policy_identity():
    uri = "vless://abc@1.2.3.4:443?security=reality#Server"
    assert TrafficPolicyService.assignment_id_from_import_text(uri) is None


def test_remote_policy_rules_are_scoped_to_one_assignment_inbound():
    script = TrafficPolicyService._remote_script(
        '{"assignment_id":42,"traffic_policy":"russia","config_path":"/usr/local/etc/xray/config.json"}'
    )
    assert 'tag_prefix = "emery-device-%d-" % assignment_id' in script
    assert '"inboundTag": [inbound_tag]' in script
    assert '"geosite:antifilter-download"' in script
    assert '"geoip:ru-blocked"' in script
    assert '"geosite:ru-blocked-all"' not in script
    assert '"geoip:ru-blocked-community"' not in script
    assert 'elif policy != "international"' in script


def test_russia_policy_has_explicit_major_blocked_service_fallbacks():
    script = TrafficPolicyService._remote_script(
        '{"assignment_id":42,"traffic_policy":"russia","config_path":"/usr/local/etc/xray/config.json"}'
    )
    for domain in (
        "domain:facebook.com",
        "domain:instagram.com",
        "domain:x.com",
        "domain:twitter.com",
        "domain:linkedin.com",
        "domain:discord.com",
        "domain:signal.org",
        "domain:viber.com",
        "domain:youtube.com",
    ):
        assert f'"{domain}"' in script


def test_manual_vps_bootstrap_has_no_server_wide_regional_filter():
    script = ManualNodeBootstrapService._bootstrap_script(
        port=443,
        server_name="www.cloudflare.com",
        node_public_key="",
        neutral_hostname="server-1",
    )
    assert "ru-blocked" not in script
    assert "re-filter" not in script
    assert '"emery-blocked"' in script
    assert '"25,465,587"' in script
    assert '"geoip:private"' in script
    assert '"www.cloudflare.com:443"' in script
    assert '"www.cloudflare.com"' in script


def test_manual_vps_direct_bootstrap_uses_single_ipv4_egress_family():
    script = ManualNodeBootstrapService._bootstrap_script(
        port=443,
        server_name="www.cloudflare.com",
        node_public_key="",
        neutral_hostname="server-1",
    )
    assert '"ip":["::/0"],"outboundTag":"emery-blocked"' in script
    assert '"settings":{"domainStrategy":"UseIPv4"}' in script
    assert "sendThrough" not in script
    assert "wg-quick@" not in script

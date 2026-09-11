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


def test_remote_policy_groups_all_assignment_inbounds():
    script = TrafficPolicyService._remote_script(
        '{"assignment_id":42,"traffic_policy":"russia","config_path":"/usr/local/etc/xray/config.json"}'
    )
    assert 'managed_by_assignment = {}' in script
    assert 're.match(r"^emery-device-(\\d+)-", tag)' in script
    assert 'all_tags = sorted(managed_by_assignment.values())' in script
    assert 'russia_tags = sorted(' in script
    assert '"inboundTag": all_tags' in script
    assert '"inboundTag": russia_tags' in script
    assert '"ext:ru-geosite.dat:antifilter-download"' not in script
    assert '"ext:ru-geoip.dat:ru-blocked"' in script


def test_generated_remote_policy_script_is_valid_python():
    for policy in ("international", "russia"):
        script = TrafficPolicyService._remote_script(
            f'{{"assignment_id":42,"traffic_policy":"{policy}","config_path":"/usr/local/etc/xray/config.json"}}'
        )
        compile(script, "<traffic-policy-remote>", "exec")


def test_policy_state_is_persisted_on_vpn_node():
    script = TrafficPolicyService._remote_script(
        '{"assignment_id":42,"traffic_policy":"russia","config_path":"/usr/local/etc/xray/config.json"}'
    )
    assert '.emery-traffic-policies.json' in script
    assert 'policies[str(assignment_id)] = policy' in script
    assert 'write_policy_state(policies)' in script
    assert 'active_ids = set(managed_by_assignment)' in script


def test_policy_uses_xray_routing_service_for_hot_reload():
    script = TrafficPolicyService._remote_script(
        '{"assignment_id":42,"traffic_policy":"russia","config_path":"/usr/local/etc/xray/config.json"}'
    )
    assert 'API_LISTEN = "127.0.0.1:10085"' in script
    assert '"RoutingService"' in script
    assert '["xray", "api", "adrules", "--server=" + API_LISTEN, routing_candidate]' in script
    assert 'if static_changed:' in script
    assert 'hot_reloaded = True' in script


def test_grouped_policy_keeps_constant_rule_families():
    script = TrafficPolicyService._remote_script(
        '{"assignment_id":42,"traffic_policy":"russia","config_path":"/usr/local/etc/xray/config.json"}'
    )
    for suffix in (
        "smtp",
        "private",
        "russia-domains",
        "russia-ips",
        "direct",
    ):
        assert f'POLICY_RULE_PREFIX + "{suffix}"' in script
    assert 'POLICY_RULE_PREFIX = "skryon-policy-"' in script
    assert 'touches_managed' in script


def test_international_policy_has_explicit_direct_terminal_route():
    script = TrafficPolicyService._remote_script(
        '{"assignment_id":42,"traffic_policy":"international","config_path":"/usr/local/etc/xray/config.json"}'
    )
    assert 'managed_rules.append(' in script
    assert '"outboundTag": "direct"' in script
    assert '"port": "25,465,587"' in script
    assert '"10.0.0.0/8"' in script
    assert '"192.168.0.0/16"' in script
    assert '"ip": ["::/0"]' not in script


def test_policy_private_network_block_uses_literal_cidrs():
    script = TrafficPolicyService._remote_script(
        '{"assignment_id":42,"traffic_policy":"russia","config_path":"/usr/local/etc/xray/config.json"}'
    )
    assert 'PRIVATE_NETWORKS = [' in script
    assert '"127.0.0.0/8"' in script
    assert '"172.16.0.0/12"' in script
    assert '"fc00::/7"' in script
    assert 'if value == "geoip:private":' in script
    assert 'migrated.extend(PRIVATE_NETWORKS)' in script


def test_russia_policy_avoids_large_geosite_asset_on_low_memory_nodes():
    script = TrafficPolicyService._remote_script(
        '{"assignment_id":42,"traffic_policy":"russia","config_path":"/usr/local/etc/xray/config.json"}'
    )
    assert 'install_asset("geosite.dat", "ru-geosite.dat")' not in script
    assert 'install_asset("geoip.dat", "ru-geoip.dat")' in script
    assert 'target = os.path.join(asset_dir, target_name)' in script
    assert 'os.unlink("/usr/local/share/xray/ru-geosite.dat")' in script
    assert '"ext:ru-geosite.dat:antifilter-download"' not in script
    assert '"ext:ru-geoip.dat:ru-blocked"' in script


def test_assignment_policy_does_not_blackhole_dual_stack_domains():
    for policy in ("international", "russia"):
        script = TrafficPolicyService._remote_script(
            f'{{"assignment_id":42,"traffic_policy":"{policy}","config_path":"/usr/local/etc/xray/config.json"}}'
        )
        assert 'routing["domainStrategy"] = "IPIfNonMatch"' in script
        assert '"ip": ["::/0"]' not in script
        assert '"outboundTag": "direct"' in script


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

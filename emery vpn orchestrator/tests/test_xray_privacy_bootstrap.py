from src.backend.services.manual_node_bootstrap_service import ManualNodeBootstrapService


def test_manual_node_bootstrap_disables_xray_activity_logs():
    script = ManualNodeBootstrapService._bootstrap_script(
        port=443,
        server_name="example.com",
        node_public_key="",
        neutral_hostname="server-test",
        isp_egress=None,
    )

    assert '"access": "none"' in script
    assert '"error": "none"' in script
    assert '"loglevel": "none"' in script
    assert '"dnsLog": false' in script
    assert '/var/log/xray' not in script.lower()
    assert 'access.log' not in script.lower()

"""Regression tests for /setup_server fail-closed REALITY provisioning."""

import shutil
import subprocess

import pytest

from src.backend.services.manual_node_bootstrap_service import ManualNodeBootstrapService
from src.backend.services.reality_probe import reality_probe_shell


def test_reality_probe_checks_actual_https_through_vless():
    script = reality_probe_shell()
    assert "xray x25519 -i" in script
    assert '"$DERIVED_PUBLIC" != "$PUBLIC_KEY"' in script
    assert '"listen": "127.0.0.1"' in script
    assert '"security": "reality"' in script
    assert '"flow": "xtls-rprx-vision"' in script
    assert "--socks5-hostname" in script
    assert "https://skryon.ru/robots.txt" in script
    assert "reality_data_plane_probe_failed" in script
    assert script.index("if [[ \"$PROBE_OK\" != 1 ]]") < script.index(
        "XRAY_REALITY_VERIFIED=1"
    )
    assert "trap probe_cleanup EXIT" in script


def test_bootstrap_only_announces_node_after_reality_probe():
    script = ManualNodeBootstrapService._bootstrap_script(
        port=443,
        server_name="www.cloudflare.com",
        node_public_key="",
        neutral_hostname="server-5",
    )
    assert "XRAY_LISTEN_PORT=443" in script
    assert "REALITY_SNI=www.cloudflare.com" in script
    assert script.index("systemctl is-active xray") < script.index(
        "reality_data_plane_probe_failed"
    )
    assert script.index("XRAY_REALITY_VERIFIED=1") < script.index(
        "printf 'XRAY_UUID="
    )
    assert "XRAY_REALITY_VERIFIED" in script


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash unavailable")
def test_generated_bootstrap_is_valid_bash():
    script = ManualNodeBootstrapService._bootstrap_script(
        port=443,
        server_name="www.cloudflare.com",
        node_public_key="",
        neutral_hostname="server-5",
    )
    result = subprocess.run(
        ["bash", "-n"],
        input=script,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr

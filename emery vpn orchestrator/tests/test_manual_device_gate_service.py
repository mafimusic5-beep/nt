from __future__ import annotations

import subprocess

from src.backend.services.manual_device_gate_service import ManualDeviceGateService


def test_generated_device_gate_bootstrap_shell_is_valid_and_automatic():
    secret = "gate-secret-" + "x" * 40
    script = ManualDeviceGateService._remote_script(
        node_id=7,
        node_ip="203.0.113.77",
        gate_port=8447,
        authorize_url="https://skryon.ru/api/device-gate/authorize",
        authorize_key=secret,
    )

    checked = subprocess.run(
        ["bash", "-n"],
        input=script,
        text=True,
        capture_output=True,
        check=False,
    )
    assert checked.returncode == 0, checked.stderr
    assert "--preferred-profile shortlived" in script
    assert "--standalone" in script
    assert '--ip-address "$NODE_IP"' in script
    assert "--reuse-key" in script
    assert "emery-gate-cert-renew.timer" in script
    assert "https://skryon.ru/api/device-gate/authorize" in script
    assert "emery-control-tunnel" not in script
    assert secret not in script

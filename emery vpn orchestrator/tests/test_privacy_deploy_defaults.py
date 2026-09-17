from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_public_api_deploy_has_no_request_history():
    nginx = _read("deploy/nginx/emery-backend.conf.example")
    systemd = _read("deploy/systemd/emery-backend.service.example")

    assert "access_log off;" in nginx
    assert "error_log /dev/null crit;" in nginx
    assert "--no-access-log" in systemd


def test_device_gate_connection_logging_is_opt_in():
    env_example = _read("deploy/device-gate/device-gate.env.example")
    bootstrap = _read("deploy/device-gate/bootstrap_node.sh")
    gate = _read("deploy/device-gate/emery_device_gate.py")
    manual = _read("src/backend/services/manual_device_gate_service.py")

    assert "EMERY_GATE_LOG_LEVEL=CRITICAL" in env_example
    assert "EMERY_GATE_LOG_LEVEL=CRITICAL" in bootstrap
    assert 'os.getenv("EMERY_GATE_LOG_LEVEL", "CRITICAL")' in gate
    assert "EMERY_GATE_LOG_LEVEL=CRITICAL" in manual

    for text in (env_example, bootstrap, manual):
        assert "EMERY_GATE_LOG_LEVEL=INFO" not in text

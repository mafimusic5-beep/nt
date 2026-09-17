from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def _read(relative: str) -> str:
    return (REPO_ROOT / relative).read_text(encoding="utf-8")


def test_legacy_device_auth_does_not_build_activity_history():
    source = _read("orchestrator/device_auth.py")
    assert "SET first_seen_at = COALESCE(first_seen_at, activated_at)" not in source
    assert "ORDER BY active DESC, last_seen_at DESC" not in source
    assert "ORDER BY last_seen_at DESC" not in source
    assert "UPDATE code_devices SET last_seen_at = ? WHERE id = ?" not in source
    assert "safe_device_name = DEFAULT_DEVICE_NAME" in source
    assert "first_seen_at = NULL" in source
    assert "last_seen_at = NULL" in source
    assert "app_version = ''" in source


def test_recovery_does_not_record_recent_use():
    legacy = _read("orchestrator/device_recovery_routes.py")
    current = _read("orchestrator/device_recovery_routes_v2.py")
    assert "UPDATE code_devices SET last_seen_at = ? WHERE id = ?" not in legacy
    assert "last_seen_at = ?" not in current
    assert "ORDER BY COALESCE(last_seen_at" not in current


def test_rate_limit_bucket_never_contains_raw_ip_or_device_id():
    source = _read("orchestrator/api.py")
    assert "_RATE_LIMIT_KEY = secrets.token_bytes(32)" in source
    assert "hmac.new(_RATE_LIMIT_KEY, material, hashlib.sha256).hexdigest()" in source
    assert "return ip + ':' + device" not in source


def test_xray_mutations_reassert_no_activity_logging():
    source = _read("emery vpn orchestrator/src/backend/services/xray_credential_service.py")
    assert 'config["log"] = {' in source
    assert '"access": "none"' in source
    assert '"error": "none"' in source
    assert '"loglevel": "none"' in source
    assert '"dnsLog": False' in source
    assert '"email": tag' not in source

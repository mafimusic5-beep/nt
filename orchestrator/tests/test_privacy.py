import sys
from pathlib import Path

ORCHESTRATOR_DIR = Path(__file__).resolve().parents[1]
if str(ORCHESTRATOR_DIR) not in sys.path:
    sys.path.insert(0, str(ORCHESTRATOR_DIR))

from api import ActivationRequest, client_key  # noqa: E402
from checkout_routes import privacy_rate_key  # noqa: E402


def test_activation_rate_key_uses_only_hashed_request_data() -> None:
    payload = ActivationRequest(code="ABC-123", deviceId="installation-123", appVersionCode=1)
    key = client_key(payload)
    assert key == client_key(payload)
    assert "ABC-123" not in key
    assert "installation-123" not in key
    assert len(key) == 64


def test_checkout_rate_key_is_opaque() -> None:
    key = privacy_rate_key("find-code", "ABC-123")
    assert "ABC-123" not in key
    assert len(key) == 64

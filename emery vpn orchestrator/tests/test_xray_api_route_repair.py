"""The live repair must preserve credentials, routing policy and roll back safely."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "repair-xray-api-route.py"
spec = importlib.util.spec_from_file_location("skryon_xray_route_repair", SCRIPT)
repair = importlib.util.module_from_spec(spec)
spec.loader.exec_module(repair)


def make_config():
    global_private_block = {
        "type": "field",
        "ip": ["127.0.0.0/8", "192.168.0.0/16"],
        "outboundTag": "emery-blocked",
    }
    return {
        "api": {"tag": "api", "listen": "127.0.0.1:10085", "services": ["RoutingService"]},
        "inbounds": [{"tag": "emery-device-67-50", "settings": {"clients": [{"id": "secret"}]}}],
        "routing": {"rules": [
            {"type": "field", "inboundTag": ["emery-device-67-50"], "outboundTag": "direct"},
            *(global_private_block.copy() for _ in range(289)),
            {"type": "field", "ip": ["::/0"], "outboundTag": "emery-blocked"},
            {"type": "field", "inboundTag": ["api"], "outboundTag": "api"},
        ]},
    }


def test_294_rules_cleaned_idempotently_without_touching_credentials():
    config = make_config()
    fixed, before, after = repair.repaired(config)
    assert before == 292
    assert after == 4
    assert fixed["routing"]["rules"][0] == repair.API_RULE
    assert fixed["routing"]["rules"][1] == config["routing"]["rules"][0]
    assert fixed["inbounds"] == config["inbounds"]
    assert config["routing"]["rules"][0] != repair.API_RULE
    again, second_before, second_after = repair.repaired(fixed)
    assert again == fixed
    assert (second_before, second_after) == (4, 4)


@pytest.mark.parametrize("invalid", [
    {},
    {"tag": "api", "listen": "0.0.0.0:10085", "services": ["RoutingService"]},
    {"tag": "api", "listen": "127.0.0.1:10085", "services": []},
    {"tag": "wrong", "listen": "127.0.0.1:10085", "services": ["RoutingService"]},
])
def test_refuse_unexpected_api_before_any_mutation(invalid):
    config = make_config()
    config["api"] = invalid
    with pytest.raises(RuntimeError):
        repair.repaired(config)


def test_preserve_unrelated_rules_even_if_similar():
    config = make_config()
    config["routing"]["rules"].extend([
        {"type": "field", "inboundTag": ["unmanaged"], "ip": ["127.0.0.0/8"], "outboundTag": "direct"},
        {"type": "field", "ip": ["127.0.0.0/8"], "outboundTag": "emery-blocked"},
    ])
    fixed, _, _ = repair.repaired(config)
    assert fixed["routing"]["rules"][-2:] == config["routing"]["rules"][-2:]

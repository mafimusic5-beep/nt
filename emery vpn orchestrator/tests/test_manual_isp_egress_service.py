from __future__ import annotations

from src.backend.services.manual_isp_egress_service import ManualIspEgressService
from src.backend.services.manual_node_bootstrap_service import ManualNodeBootstrapService
from src.common.config import settings


WG_KEY = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="


def _enable_egress(monkeypatch) -> None:
    monkeypatch.setattr(settings, "manual_bootstrap_isp_egress_enabled", True)
    monkeypatch.setattr(settings, "manual_bootstrap_isp_egress_host", "198.51.100.20")
    monkeypatch.setattr(settings, "manual_bootstrap_isp_egress_port", 51820)
    monkeypatch.setattr(settings, "manual_bootstrap_isp_egress_public_key", WG_KEY)
    monkeypatch.setattr(settings, "manual_bootstrap_isp_egress_interface", "wg-skryon")
    monkeypatch.setattr(settings, "manual_bootstrap_isp_egress_cidr", "10.77.0.0/16")
    monkeypatch.setattr(settings, "manual_bootstrap_isp_egress_mtu", 1380)
    monkeypatch.setattr(settings, "manual_bootstrap_isp_egress_keepalive_seconds", 25)
    monkeypatch.setattr(settings, "manual_bootstrap_isp_egress_routing_table", 51820)


def test_manual_isp_egress_allocates_unique_node_address(monkeypatch):
    _enable_egress(monkeypatch)

    plan = ManualIspEgressService().plan_for_node(7)

    assert plan is not None
    assert plan.gateway_ip == "10.77.0.1"
    assert plan.client_ip == "10.77.0.8"
    assert plan.client_cidr == "10.77.0.8/32"
    assert plan.peer_endpoint == "198.51.100.20:51820"
    assert plan.mtu == 1380


def test_bootstrap_without_isp_egress_keeps_direct_vps_outbound(monkeypatch):
    monkeypatch.setattr(settings, "manual_bootstrap_isp_egress_enabled", False)

    script = ManualNodeBootstrapService._bootstrap_script(
        port=443,
        server_name="www.cloudflare.com",
        node_public_key="",
        neutral_hostname="server-1",
        isp_egress=None,
    )

    assert '"tag":"direct","protocol":"freedom"' in script
    assert "wireguard-tools" not in script
    assert "skryon_egress_killswitch" not in script
    assert "sendThrough" not in script


def test_bootstrap_with_isp_egress_fails_closed_through_wireguard(monkeypatch):
    _enable_egress(monkeypatch)
    plan = ManualIspEgressService().plan_for_node(7)
    assert plan is not None

    script = ManualNodeBootstrapService._bootstrap_script(
        port=443,
        server_name="www.cloudflare.com",
        node_public_key="ssh-ed25519 AAAATEST orchestrator",
        neutral_hostname="server-7",
        isp_egress=plan,
    )

    assert "wireguard-tools" in script
    assert "AllowedIPs = 0.0.0.0/0" in script
    assert "skryon_egress_killswitch" in script
    assert "Requires=wg-quick@$WG_IFACE.service" in script
    assert '"sendThrough":"10.77.0.8"' in script
    assert '"domainStrategy":"UseIPv4"' in script
    assert '"ip":["::/0"],"outboundTag":"emery-blocked"' in script
    assert "WG_PUBLIC_KEY=" in script
    assert "WG_ADDRESS=" in script


def test_egress_registration_persists_peer_and_nat(monkeypatch):
    _enable_egress(monkeypatch)
    plan = ManualIspEgressService().plan_for_node(1)
    assert plan is not None

    script = ManualIspEgressService._registration_script(plan, WG_KEY)

    assert 'wg set "$IFACE" peer "$CLIENT_KEY" allowed-ips "$CLIENT_IP/32"' in script
    assert 'wg-quick save "$IFACE"' in script
    assert "net.ipv4.ip_forward=1" in script
    assert "masquerade" in script
    assert "skryon-isp-egress-nat.service" in script

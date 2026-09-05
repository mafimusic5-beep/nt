from __future__ import annotations

from datetime import datetime, timedelta, timezone
import uuid

import pytest
from fastapi import HTTPException

from src.backend.schemas.admin import ManualNodeBootstrapRequest, VpnNodeUpsertRequest
from src.backend.services.admin_service import AdminService
from src.backend.services.manual_device_gate_service import ManualDeviceGateService
from src.backend.services.manual_node_admin_service import ManualNodeAdminService
from src.backend.services.manual_node_bootstrap_service import ManualNodeBootstrapService
from src.backend.services.xray_credential_service import (
    CredentialMutationResult,
    ScriptOrSshXrayCredentialTransport,
)
from src.common.config import settings
from src.common.models import VpnAssignment, VpnNode


VALID_CONFIG = (
    "vless://00000000-0000-0000-0000-000000000000@203.0.113.10:443"
    "?encryption=none&flow=xtls-rprx-vision&security=reality&sni=www.cloudflare.com"
    "&fp=chrome&pbk=public-key&sid=abcd&type=tcp#Server"
)


def _create_assignment(db_session, node_id: int, index: int) -> VpnAssignment:
    assignment = VpnAssignment(
        subject_type="device",
        subject_key=f"device-{index}",
        entitlement_hash=f"hash-{index}",
        entitlement_expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        node_id=node_id,
        client_uuid=str(uuid.uuid4()),
        client_port=20000 + index,
        speed_limit_mbps=30,
        status="active",
        config_revision=1,
        device_gate_enforced=True,
    )
    db_session.add(assignment)
    return assignment


def _existing_gated_node(db_session, monkeypatch) -> VpnNode:
    monkeypatch.setattr(settings, "device_bound_gate_enabled", True)
    created = AdminService(db_session).create_node(
        VpnNodeUpsertRequest(
            name="Old server",
            region_code="de",
            endpoint="203.0.113.10",
            config_payload=VALID_CONFIG,
            device_gate_host="gate.example.com",
            device_gate_port=8447,
            device_gate_server_name="gate.example.com",
            device_gate_spki_sha256="a" * 64,
            status="active",
            health_status="healthy",
            capacity_clients=20,
            current_clients=2,
            per_device_speed_limit_mbps=30,
        )
    )
    node = db_session.get(VpnNode, created.id)
    assert node is not None
    first = _create_assignment(db_session, node.id, 0)
    second = _create_assignment(db_session, node.id, 1)
    db_session.commit()
    db_session.refresh(first)
    db_session.refresh(second)
    return node


def test_gate_enabled_allows_empty_gate_while_node_is_provisioning(db_session, monkeypatch):
    monkeypatch.setattr(settings, "device_bound_gate_enabled", True)
    service = AdminService(db_session)

    draft = service.create_node(
        VpnNodeUpsertRequest(
            name="Provisioning",
            region_code="de",
            endpoint="203.0.113.20",
            config_payload="",
            status="provisioning",
            health_status="unknown",
        )
    )
    assert draft.status == "provisioning"
    assert draft.device_gate_host == ""

    with pytest.raises(HTTPException) as error:
        service.create_node(
            VpnNodeUpsertRequest(
                name="Unsafe active",
                region_code="de",
                endpoint="203.0.113.21",
                config_payload=VALID_CONFIG.replace("203.0.113.10", "203.0.113.21"),
                status="active",
                health_status="healthy",
            )
        )
    assert error.value.detail == "device_gate_endpoint_required"


def test_rebootstrap_restores_active_assignments_before_enabling_node(db_session, monkeypatch):
    node = _existing_gated_node(db_session, monkeypatch)
    original = {
        row.id: (row.client_uuid, row.client_port, row.config_revision)
        for row in db_session.query(VpnAssignment).filter(VpnAssignment.node_id == node.id).all()
    }
    installed: list[int] = []

    def fake_base(self, target, *, ssh_user, ssh_password):
        assert target.status == "provisioning"
        assert target.health_status == "unknown"
        assert target.capacity_clients == 2
        assert ssh_user == "root"
        assert ssh_password == "new-password"
        target.config_payload = VALID_CONFIG
        target.ssh_private_key = "private-key"
        target.ssh_public_key = "public-key"
        target.ssh_host_key = "ssh-ed25519 AAAATEST"
        target.ssh_key_status = "installed"
        return {"status": "ok", "isp_egress_enabled": False}

    def fake_gate(self, target):
        assert target.status == "provisioning"
        return {
            "status": "ok",
            "host": "203.0.113.10",
            "port": 8447,
            "server_name": "203.0.113.10",
            "spki_sha256": "b" * 64,
        }

    def fake_install(self, target, assignment):
        assert target.status == "provisioning"
        assert target.device_gate_spki_sha256 == "b" * 64
        installed.append(assignment.id)
        return CredentialMutationResult(
            True,
            "restored",
            rate_limit_enforced=True,
            smtp_block_enforced=True,
            shared_credential_disabled=True,
            direct_ingress_blocked=True,
            device_gate_ready=True,
        )

    monkeypatch.setattr(ManualNodeBootstrapService, "bootstrap_with_password", fake_base)
    monkeypatch.setattr(ManualDeviceGateService, "bootstrap", fake_gate)
    monkeypatch.setattr(ScriptOrSshXrayCredentialTransport, "install", fake_install)

    result = ManualNodeAdminService(db_session).bootstrap(
        ManualNodeBootstrapRequest(
            name="Germany",
            region_code="de",
            endpoint="203.0.113.10",
            ssh_user="root",
            ssh_password="new-password",
            capacity_clients=1,
            bandwidth_limit_mbps=1000,
            per_device_speed_limit_mbps=30,
        )
    )

    assert result.node.id == node.id
    assert result.node.status == "active"
    assert result.node.health_status == "healthy"
    assert result.node.capacity_clients == 2
    assert result.node.current_clients == 2
    assert result.node.device_gate_host == "203.0.113.10"
    assert result.node.device_gate_server_name == "203.0.113.10"
    assert result.node.device_gate_spki_sha256 == "b" * 64
    assert installed == sorted(original)

    rows = db_session.query(VpnAssignment).filter(VpnAssignment.node_id == node.id).all()
    for row in rows:
        old_uuid, old_port, old_revision = original[row.id]
        assert row.status == "active"
        assert row.client_uuid == old_uuid
        assert row.client_port == old_port
        assert row.config_revision == old_revision + 1
        assert row.device_gate_enforced is True
        assert row.installed_at is not None


def test_rebootstrap_stays_down_when_assignment_restore_fails(db_session, monkeypatch):
    node = _existing_gated_node(db_session, monkeypatch)
    old_revisions = {
        row.id: row.config_revision
        for row in db_session.query(VpnAssignment).filter(VpnAssignment.node_id == node.id).all()
    }

    def fake_base(self, target, *, ssh_user, ssh_password):
        target.config_payload = VALID_CONFIG
        target.ssh_private_key = "private-key"
        target.ssh_public_key = "public-key"
        target.ssh_host_key = "ssh-ed25519 AAAATEST"
        target.ssh_key_status = "installed"
        return {"status": "ok", "isp_egress_enabled": False}

    def fake_gate(self, target):
        return {
            "status": "ok",
            "host": "203.0.113.10",
            "port": 8447,
            "server_name": "203.0.113.10",
            "spki_sha256": "b" * 64,
        }

    calls = 0

    def fake_install(self, target, assignment):
        nonlocal calls
        calls += 1
        if calls == 2:
            return CredentialMutationResult(False, "simulated_restore_failure")
        return CredentialMutationResult(
            True,
            "restored",
            rate_limit_enforced=True,
            smtp_block_enforced=True,
            shared_credential_disabled=True,
            direct_ingress_blocked=True,
            device_gate_ready=True,
        )

    monkeypatch.setattr(ManualNodeBootstrapService, "bootstrap_with_password", fake_base)
    monkeypatch.setattr(ManualDeviceGateService, "bootstrap", fake_gate)
    monkeypatch.setattr(ScriptOrSshXrayCredentialTransport, "install", fake_install)

    with pytest.raises(HTTPException) as error:
        ManualNodeAdminService(db_session).bootstrap(
            ManualNodeBootstrapRequest(
                name="Germany",
                region_code="de",
                endpoint="203.0.113.10",
                ssh_user="root",
                ssh_password="new-password",
                capacity_clients=5,
                bandwidth_limit_mbps=1000,
                per_device_speed_limit_mbps=30,
            )
        )
    assert "assignment_restore_failed" in str(error.value.detail)

    db_session.expire_all()
    failed_node = db_session.get(VpnNode, node.id)
    assert failed_node is not None
    assert failed_node.status == "provision_failed"
    assert failed_node.health_status == "down"
    rows = db_session.query(VpnAssignment).filter(VpnAssignment.node_id == node.id).all()
    assert {row.id: row.config_revision for row in rows} == old_revisions
    assert all(row.status == "active" for row in rows)

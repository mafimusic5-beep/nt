from __future__ import annotations

import base64
import hashlib
import sqlite3
import uuid

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

import config
import device_auth
import device_gate_session_guard
import storage


GATE_SPKI_SHA256 = "a" * 64


def _public_key(private_key: ec.EllipticCurvePrivateKey) -> tuple[str, str]:
    der = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return base64.b64encode(der).decode("ascii"), hashlib.sha256(der).hexdigest()


def _prepared_assignment(tmp_path, monkeypatch):
    database_path = str(tmp_path / "gate-session.sqlite3")
    monkeypatch.setattr(config, "DATABASE_PATH", database_path)
    monkeypatch.setattr(storage, "DATABASE_PATH", database_path)
    monkeypatch.setattr(device_auth, "DATABASE_PATH", database_path)
    storage.init_storage()
    device_auth.ensure_device_auth_storage()

    code = storage.create_checkout_code(
        "personal",
        1,
        external_id="gate-session-" + uuid.uuid4().hex,
    )["code"]
    device_id = "registered-device"
    private_key = ec.generate_private_key(ec.SECP256R1())
    public_key, fingerprint = _public_key(private_key)

    with sqlite3.connect(database_path) as con:
        con.execute(
            """
            INSERT INTO code_devices(
                code, device_id, activated_at, device_name, public_key,
                public_key_fingerprint, platform, app_version,
                first_seen_at, last_seen_at, active,
                pool_assignment_id, pool_status, pool_node_id,
                pool_client_port, pool_gate_server_name, pool_gate_spki_sha256
            ) VALUES (?, ?, ?, ?, ?, ?, 'android', 'test', ?, ?, 1, 17, 'active', 4, 20000, ?, ?)
            """,
            (
                code,
                device_id,
                storage.now_iso(),
                "Test Android",
                public_key,
                fingerprint,
                storage.now_iso(),
                storage.now_iso(),
                "gate.example.com",
                GATE_SPKI_SHA256,
            ),
        )
        con.commit()

    return database_path, code, device_id, fingerprint


def test_key_replacement_revokes_only_old_live_gate_epoch(tmp_path, monkeypatch):
    database_path, _, device_id, fingerprint1 = _prepared_assignment(tmp_path, monkeypatch)
    epoch1 = device_gate_session_guard._credential_epoch_from_fingerprint(fingerprint1)

    first = device_gate_session_guard.check_gateway_session(
        assignment_id=17,
        node_id=4,
        gate_server_name="gate.example.com",
        gate_spki_sha256=GATE_SPKI_SHA256,
        device_id=device_id,
        credential_epoch=epoch1,
    )
    assert first["allowed"] is True

    key2 = ec.generate_private_key(ec.SECP256R1())
    public2, fingerprint2 = _public_key(key2)
    with sqlite3.connect(database_path) as con:
        con.execute(
            "UPDATE code_devices SET public_key = ?, public_key_fingerprint = ? WHERE device_id = ?",
            (public2, fingerprint2, device_id),
        )
        con.commit()

    replaced = device_gate_session_guard.check_gateway_session(
        assignment_id=17,
        node_id=4,
        gate_server_name="gate.example.com",
        gate_spki_sha256=GATE_SPKI_SHA256,
        device_id=device_id,
        credential_epoch=epoch1,
    )
    assert replaced == {
        "allowed": False,
        "reason": "device_gate_session_replaced",
    }

    epoch2 = device_gate_session_guard._credential_epoch_from_fingerprint(fingerprint2)
    current = device_gate_session_guard.check_gateway_session(
        assignment_id=17,
        node_id=4,
        gate_server_name="gate.example.com",
        gate_spki_sha256=GATE_SPKI_SHA256,
        device_id=device_id,
        credential_epoch=epoch2,
    )
    assert current["allowed"] is True


def test_other_device_state_does_not_revoke_current_slot(tmp_path, monkeypatch):
    database_path, code, device_id, fingerprint = _prepared_assignment(tmp_path, monkeypatch)
    epoch = device_gate_session_guard._credential_epoch_from_fingerprint(fingerprint)

    other_key = ec.generate_private_key(ec.SECP256R1())
    other_public, other_fingerprint = _public_key(other_key)
    with sqlite3.connect(database_path) as con:
        con.execute(
            """
            INSERT INTO code_devices(
                code, device_id, activated_at, device_name, public_key,
                public_key_fingerprint, platform, app_version,
                first_seen_at, last_seen_at, active
            ) VALUES (?, ?, ?, ?, ?, ?, 'android', 'test', ?, ?, 0)
            """,
            (
                code,
                "other-device",
                storage.now_iso(),
                "Other Android",
                other_public,
                other_fingerprint,
                storage.now_iso(),
                storage.now_iso(),
            ),
        )
        con.commit()

    result = device_gate_session_guard.check_gateway_session(
        assignment_id=17,
        node_id=4,
        gate_server_name="gate.example.com",
        gate_spki_sha256=GATE_SPKI_SHA256,
        device_id=device_id,
        credential_epoch=epoch,
    )
    assert result["allowed"] is True


def test_explicit_subscription_disable_ends_gate_epoch(tmp_path, monkeypatch):
    database_path, code, device_id, fingerprint = _prepared_assignment(tmp_path, monkeypatch)
    epoch = device_gate_session_guard._credential_epoch_from_fingerprint(fingerprint)

    with sqlite3.connect(database_path) as con:
        con.execute("UPDATE activation_codes SET status = 'disabled' WHERE code = ?", (code,))
        con.commit()

    result = device_gate_session_guard.check_gateway_session(
        assignment_id=17,
        node_id=4,
        gate_server_name="gate.example.com",
        gate_spki_sha256=GATE_SPKI_SHA256,
        device_id=device_id,
        credential_epoch=epoch,
    )
    assert result == {
        "allowed": False,
        "reason": "device_gate_session_revoked",
    }

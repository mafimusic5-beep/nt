from __future__ import annotations

import base64
import hashlib
import tempfile
import time
import uuid
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

import config
import checkout_routes
import device_auth
import storage


def _key():
    return ec.generate_private_key(ec.SECP256R1())


def _public(key) -> str:
    der = key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return base64.b64encode(der).decode("ascii")


def _sign(key, canonical: str) -> str:
    signature = key.sign(canonical.encode("utf-8"), ec.ECDSA(hashes.SHA256()))
    return base64.b64encode(signature).decode("ascii")


def _probe(seed: str) -> str:
    return "dp1:" + hashlib.sha256(("skryon-device-v1:" + seed).encode()).hexdigest()


@pytest.fixture()
def database(monkeypatch):
    temp_dir = tempfile.TemporaryDirectory()
    db_path = str(Path(temp_dir.name) / "identifier-slots.sqlite3")
    monkeypatch.setattr(config, "DATABASE_PATH", db_path)
    monkeypatch.setattr(storage, "DATABASE_PATH", db_path)
    monkeypatch.setattr(device_auth, "DATABASE_PATH", db_path)
    monkeypatch.setattr(device_auth, "pool_bridge_enabled", lambda: False)
    storage.init_storage()
    device_auth.ensure_device_auth_storage()
    yield db_path
    temp_dir.cleanup()


def _create_code(plan: str, limit: int) -> str:
    return str(
        storage.create_checkout_code(
            plan=plan,
            max_devices=limit,
            days=30,
            customer="identifier-slot-test",
            external_id="identifier-" + uuid.uuid4().hex,
        )["code"]
    )


def _activation_args(code: str, device_id: str, key, *, path: str = "/api/activate"):
    timestamp = str(int(time.time() * 1000))
    nonce = uuid.uuid4().hex
    device_name = "Android-устройство"
    canonical = "\n".join(
        (
            "method=POST",
            f"path={path}",
            f"device_id={device_id}",
            f"device_name={device_name}",
            f"timestamp={timestamp}",
            f"nonce={nonce}",
            f"auth_sha256={hashlib.sha256(code.strip().encode()).hexdigest()}",
        )
    )
    return {
        "raw_code": code,
        "path": path,
        "device_id": device_id,
        "device_name": device_name,
        "public_key_base64": _public(key),
        "timestamp": timestamp,
        "nonce": nonce,
        "signature_base64": _sign(key, canonical),
        "signature_algorithm": "SHA256withECDSA",
    }


def _register(code: str, device_id: str, key):
    args = _activation_args(code, device_id, key)
    return device_auth.register_device(
        **args,
        platform="android",
        app_version="test",
    )


def _validate(code: str, device_id: str, key):
    args = _activation_args(code, device_id, key, path="/api/activate/validate")
    return device_auth.validate_device_registration(**args)


def _authenticate(code: str, device_id: str, key):
    timestamp = str(int(time.time() * 1000))
    nonce = uuid.uuid4().hex
    canonical = "\n".join(
        (
            "method=GET",
            "path=/api/device/profile",
            f"device_id={device_id}",
            f"timestamp={timestamp}",
            f"nonce={nonce}",
            f"auth_sha256={hashlib.sha256(code.strip().encode()).hexdigest()}",
        )
    )
    return device_auth.authenticate_registered_device(
        raw_code=code,
        method="GET",
        path="/api/device/profile",
        device_id=device_id,
        timestamp=timestamp,
        nonce=nonce,
        signature_base64=_sign(key, canonical),
        signature_algorithm="SHA256withECDSA",
    )


def test_same_identifier_reinstall_reuses_one_slot_and_rotates_key(database):
    code = _create_code("personal", 1)
    device_id = _probe("0123456789abcdef")
    key1 = _key()
    key2 = _key()

    first = _register(code, device_id, key1)
    assert first["devices_used"] == 1

    validation = _validate(code, device_id, key2)
    assert validation["already_registered"] is True
    assert validation["devices_used"] == 1

    second = _register(code, device_id, key2)
    assert second["devices_used"] == 1

    with storage.connect() as con:
        count = con.execute(
            "SELECT COUNT(*) FROM code_devices WHERE code = ? AND device_id = ?",
            (storage.format_code(code), device_id),
        ).fetchone()[0]
    assert count == 1

    with pytest.raises(device_auth.DeviceAuthError) as old_error:
        _authenticate(code, device_id, key1)
    assert old_error.value.reason == "device_signature_invalid"
    assert _authenticate(code, device_id, key2)["device_id"] == device_id


def test_new_identifiers_consume_tariff_slots(database):
    code = _create_code("personal_plus", 2)
    device1 = _probe("1111111111111111")
    device2 = _probe("2222222222222222")
    device3 = _probe("3333333333333333")

    assert _register(code, device1, _key())["devices_used"] == 1
    assert _register(code, device2, _key())["devices_used"] == 2

    with pytest.raises(device_auth.DeviceAuthError) as error:
        _register(code, device3, _key())
    assert error.value.reason == "device_limit_reached"


def test_identifier_recovery_path_is_noop(database):
    result = checkout_routes.identifier_slot_recovery_challenge({})
    assert result["ok"] is True
    assert result["status"] == "not_needed"
    assert result["integrity_required"] is False


def test_malformed_dp1_identifier_is_rejected(database):
    code = _create_code("personal", 1)
    with pytest.raises(device_auth.DeviceAuthError) as error:
        _register(code, "dp1:not-a-valid-hash", _key())
    assert error.value.reason == "device_identifier_invalid"

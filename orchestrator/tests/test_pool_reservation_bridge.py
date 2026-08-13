from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
import time
import uuid

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

import config
import device_auth
import pool_reservation_bridge as bridge
import storage


DEVICE_CONFIG = (
    "vless://14aec1f1-bf97-47d0-896c-c553a18e2282@203.0.113.10:20000"
    "?type=tcp&security=reality&pbk=public-key&sid=0123456789abcdef#Germany"
)


class FakeResponse:
    status_code = 200
    content = b'{}'

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


def _signed_registration(code: str, device_id: str):
    key = ec.generate_private_key(ec.SECP256R1())
    public_der = key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    timestamp = str(int(time.time() * 1000))
    nonce = uuid.uuid4().hex
    device_name = 'Test Android'
    canonical = '\n'.join(
        (
            'method=POST',
            'path=/api/activate',
            f'device_id={device_id}',
            f'device_name={device_name}',
            f'timestamp={timestamp}',
            f'nonce={nonce}',
            f'auth_sha256={hashlib.sha256(code.encode()).hexdigest()}',
        )
    )
    signature = key.sign(canonical.encode(), ec.ECDSA(hashes.SHA256()))
    return device_auth.register_device(
        raw_code=code,
        path='/api/activate',
        device_id=device_id,
        device_name=device_name,
        public_key_base64=base64.b64encode(public_der).decode(),
        timestamp=timestamp,
        nonce=nonce,
        signature_base64=base64.b64encode(signature).decode(),
        signature_algorithm='SHA256withECDSA',
        platform='android',
        app_version='test',
    )


@pytest.fixture()
def legacy_db(tmp_path, monkeypatch):
    path = str(tmp_path / 'legacy.sqlite3')
    monkeypatch.setattr(config, 'DATABASE_PATH', path)
    monkeypatch.setattr(storage, 'DATABASE_PATH', path)
    monkeypatch.setattr(device_auth, 'DATABASE_PATH', path)
    storage.init_storage()
    device_auth.ensure_device_auth_storage()
    return path


def test_bridge_sends_only_hmac_pseudonyms(monkeypatch):
    monkeypatch.setattr(bridge, 'POOL_BRIDGE_ENABLED', True)
    monkeypatch.setattr(bridge, 'POOL_BRIDGE_URL', 'https://pool.example.test')
    monkeypatch.setattr(bridge, 'POOL_BRIDGE_API_KEY', 'bridge-api-secret')
    monkeypatch.setattr(bridge, 'POOL_BRIDGE_PSEUDONYM_KEY', 'pseudonym-secret')
    captured = []

    def fake_post(url, *, headers, json, timeout):
        captured.append({'url': url, 'headers': headers, 'json': json})
        return FakeResponse(
            {
                'assignment_id': 17,
                'status': 'pending',
                'confirmation_required': True,
                'confirmation_token': 't' * 43,
                'node_id': 4,
                'node_name': 'Germany 1',
                'region_code': 'de',
                'config': DEVICE_CONFIG,
                'config_revision': 1,
                'speed_limit_mbps': 30,
                'entitlement_expires_at': '2026-09-10T00:00:00+00:00',
            }
        )

    monkeypatch.setattr(bridge.httpx, 'post', fake_post)
    bridge.prepare_assignment(
        formatted_code='A-ABC-DE-FG-HI-J',
        device_id='android-secret-device-id',
        plan='family',
        expires_at='2026-09-10T00:00:00+00:00',
    )

    wire = json.dumps(captured[0]['json'])
    assert 'A-ABC-DE-FG-HI-J' not in wire
    assert 'android-secret-device-id' not in wire
    assert len(captured[0]['json']['subject_key']) == 64
    assert len(captured[0]['json']['entitlement_hash']) == 64


def test_registration_rolls_back_when_pool_has_no_real_slot(legacy_db, monkeypatch):
    code = storage.create_checkout_code('personal', 1, external_id='pool-full')['code']
    monkeypatch.setattr(device_auth, 'pool_bridge_enabled', lambda: True)

    def fail_prepare(**kwargs):
        raise bridge.PoolBridgeError('server_capacity_unavailable', 409)

    monkeypatch.setattr(device_auth, 'prepare_assignment', fail_prepare)

    with pytest.raises(device_auth.DeviceAuthError) as error:
        _signed_registration(code, 'device-full-pool')

    assert error.value.reason == 'server_capacity_unavailable'
    with sqlite3.connect(legacy_db) as con:
        assert con.execute('SELECT COUNT(*) FROM code_devices').fetchone()[0] == 0
        assert con.execute('SELECT COUNT(*) FROM device_request_nonces').fetchone()[0] == 0


def test_registration_persists_then_confirms_personal_config(legacy_db, monkeypatch):
    code = storage.create_checkout_code('personal', 1, external_id='pool-ok')['code']
    monkeypatch.setattr(device_auth, 'pool_bridge_enabled', lambda: True)
    prepared = {
        'pool_assignment_id': 17,
        'pool_status': 'pending',
        'pool_confirmation_token': 't' * 43,
        'pool_node_id': 4,
        'pool_node_name': 'Germany 1',
        'pool_region': 'de',
        'pool_config': DEVICE_CONFIG,
        'pool_config_revision': 1,
        'pool_speed_limit_mbps': 30,
        'pool_entitlement_hash': 'a' * 64,
        'pool_entitlement_expires_at': '2026-09-10T00:00:00+00:00',
        'confirmation_required': True,
    }
    monkeypatch.setattr(device_auth, 'prepare_assignment', lambda **kwargs: dict(prepared))
    monkeypatch.setattr(
        bridge,
        'confirm_assignment',
        lambda assignment_id, token: {
            'assignment_id': assignment_id,
            'status': 'active',
            'confirmed_at': '2026-08-10T00:00:00+00:00',
        },
    )

    result = _signed_registration(code, 'device-with-personal-vless')
    stored = storage.get_device_pool_assignment(code, 'device-with-personal-vless')

    assert result['vpn_assignment']['pool_status'] == 'active'
    assert result['vpn_assignment']['pool_config'] == DEVICE_CONFIG
    assert stored['pool_status'] == 'active'
    assert stored['pool_confirmation_token'] == ''

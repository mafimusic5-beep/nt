from concurrent.futures import ThreadPoolExecutor
import sqlite3
import time
import uuid

import pytest

import concurrent_sessions as sessions
import config
import device_auth
import storage
from test_device_gate_auth import registered_assignment, _authorize


def add_device(base, device_id, assignment=None, node=4):
    assignment = assignment or (100 + uuid.uuid4().int % 100000000)
    from cryptography.hazmat.primitives.asymmetric import ec
    from test_device_gate_auth import _sign, _public_key_base64, _activation_canonical, GATE_SPKI_SHA256
    key = ec.generate_private_key(ec.SECP256R1())
    timestamp, nonce = str(int(time.time() * 1000)), uuid.uuid4().hex
    profile = device_auth.register_device(raw_code=base['code'], path='/api/activate',
        device_id=device_id, device_name='Test Android', public_key_base64=_public_key_base64(key),
        timestamp=timestamp, nonce=nonce,
        signature_base64=_sign(key, _activation_canonical(base['code'], device_id, timestamp, nonce)),
        signature_algorithm='SHA256withECDSA', platform='android', app_version='test')
    assert profile['limit_mode'] == 'concurrent'
    storage.save_device_pool_assignment(base['code'], device_id, {
        'pool_assignment_id': assignment, 'pool_status': 'active', 'pool_node_id': node,
        'pool_client_port': 20000, 'pool_gate_server_name': 'gate.example.com',
        'pool_gate_spki_sha256': GATE_SPKI_SHA256})
    return {**base, 'device_id': device_id, 'private_key': key, 'node_id': node, 'assignment_id': assignment}


def acquire(base):
    # Use the real signed gate admission, including nonce consumption.
    kwargs, _ = _authorize_without_call(base)
    return device_auth.authorize_gateway_connection(**kwargs, lease_protocol=1)


def _authorize_without_call(base):
    from test_device_gate_auth import _gateway_canonical, _sign, GATE_SPKI_SHA256
    now = str(int(time.time() * 1000))
    values = dict(assignment_id=base.get('assignment_id', 17), node_id=base.get('node_id', 4), gate_server_name='gate.example.com',
        gate_spki_sha256=GATE_SPKI_SHA256, device_id=base['device_id'],
        server_issued_at=now, timestamp=now, server_nonce=uuid.uuid4().hex,
        client_nonce=uuid.uuid4().hex)
    return {**values, 'signature_base64': _sign(base['private_key'], _gateway_canonical(**values)),
        'signature_algorithm': 'SHA256withECDSA'}, None


@pytest.fixture()
def online_mode(registered_assignment, monkeypatch):
    monkeypatch.setattr(config, 'CONCURRENT_SESSIONS_ENABLED', True)
    sessions.purge()
    return registered_assignment


@pytest.mark.parametrize('limit,plan', [(1, 'personal'), (2, 'personal_plus'), (5, 'family')])
def test_limit_counts_installations_not_streams(online_mode, limit, plan):
    base = online_mode
    with sqlite3.connect(base['database_path']) as con:
        con.execute('UPDATE activation_codes SET max_devices=?, plan=?', (limit, plan))
    devices = [base] + [add_device(base, f'device-{i}') for i in range(1, limit + 1)]
    tokens = []
    for d in devices[:limit]:
        tokens.append(acquire(d)['lease_token'])
        tokens.append(acquire(d)['lease_token'])
    with pytest.raises(device_auth.DeviceAuthError, match='concurrent_limit_reached'):
        acquire(devices[-1])
    sessions.update(tokens[0], release=True)
    with pytest.raises(device_auth.DeviceAuthError, match='concurrent_limit_reached'):
        acquire(devices[-1])
    sessions.update(tokens[1], release=True)
    assert acquire(devices[-1])['allowed']


def test_simultaneous_admission_is_atomic(online_mode):
    devices = [online_mode] + [add_device(online_mode, f'device-{i}') for i in range(10)]

    def attempt(d):
        try:
            return acquire(d)['allowed']
        except device_auth.DeviceAuthError as exc:
            assert exc.reason == 'concurrent_limit_reached'
            return False

    with ThreadPoolExecutor(max_workers=11) as pool:
        assert sum(pool.map(attempt, devices)) == 1


def test_expired_lease_cannot_be_resurrected(online_mode):
    lease = acquire(online_mode)['lease_token']
    with sqlite3.connect(online_mode['database_path']) as con:
        con.execute('UPDATE vpn_live_leases SET expires_at = 0')
    other = add_device(online_mode, 'another-installation')
    assert acquire(other)['allowed']
    with pytest.raises(device_auth.DeviceAuthError, match='session_expired'):
        sessions.update(lease)


@pytest.mark.parametrize('change', ["active=0", "public_key='changed'", "pool_assignment_id=1234"])
def test_revocation_closes_renewal(online_mode, change):
    lease = acquire(online_mode)['lease_token']
    with sqlite3.connect(online_mode['database_path']) as con:
        con.execute('UPDATE code_devices SET ' + change)
    with pytest.raises(device_auth.DeviceAuthError, match='session_revoked'):
        sessions.update(lease)


def test_expired_subscription_cannot_renew(online_mode):
    lease = acquire(online_mode)['lease_token']
    with sqlite3.connect(online_mode['database_path']) as con:
        con.execute("UPDATE activation_codes SET expires_at='2000-01-01T00:00:00+00:00'")
    with pytest.raises(device_auth.DeviceAuthError):
        sessions.update(lease)


def test_tariff_downgrade_keeps_oldest_sessions_only(online_mode):
    with sqlite3.connect(online_mode['database_path']) as con:
        con.execute("UPDATE activation_codes SET max_devices=5, plan='family'")

    second = add_device(online_mode, 'downgrade-second')
    third = add_device(online_mode, 'downgrade-third')
    first_token = acquire(online_mode)['lease_token']
    second_token = acquire(second)['lease_token']
    third_token = acquire(third)['lease_token']

    with sqlite3.connect(online_mode['database_path']) as con:
        con.execute("UPDATE activation_codes SET max_devices=1, plan='personal'")

    assert sessions.update(first_token)['ok']
    with pytest.raises(device_auth.DeviceAuthError, match='concurrent_limit_reached'):
        sessions.update(second_token)
    with pytest.raises(device_auth.DeviceAuthError, match='concurrent_limit_reached'):
        sessions.update(third_token)

    with sqlite3.connect(online_mode['database_path']) as con:
        con.row_factory = sqlite3.Row
        assert sessions.count(con, online_mode['code']) == 1


def test_tariff_downgrade_blocks_new_stream_from_extra_live_session(online_mode):
    with sqlite3.connect(online_mode['database_path']) as con:
        con.execute("UPDATE activation_codes SET max_devices=2, plan='personal_plus'")

    second = add_device(online_mode, 'downgrade-present')
    first_token = acquire(online_mode)['lease_token']
    second_token = acquire(second)['lease_token']

    with sqlite3.connect(online_mode['database_path']) as con:
        con.execute("UPDATE activation_codes SET max_devices=1, plan='personal'")

    assert sessions.update(first_token)['ok']
    with pytest.raises(device_auth.DeviceAuthError, match='concurrent_limit_reached'):
        acquire(second)
    with pytest.raises(device_auth.DeviceAuthError, match='concurrent_limit_reached'):
        sessions.update(second_token)
    with pytest.raises(device_auth.DeviceAuthError, match='session_expired'):
        sessions.update(second_token)


def test_lease_is_minimal_and_removed_on_release(online_mode):
    token = acquire(online_mode)['lease_token']
    with sqlite3.connect(online_mode['database_path']) as con:
        names = [r[1] for r in con.execute('PRAGMA table_info(vpn_live_leases)')]
        assert names == ['token_hash', 'device_row_id', 'key_hash', 'assignment_id', 'node_id', 'expires_at']
        assert con.execute('SELECT token_hash FROM vpn_live_leases').fetchone()[0] != token
    assert sessions.update(token)['ok']
    sessions.update(token, release=True)
    sessions.update(token, release=True)
    with sqlite3.connect(online_mode['database_path']) as con:
        assert con.execute('SELECT COUNT(*) FROM vpn_live_leases').fetchone()[0] == 0


def test_old_gateway_is_rejected_in_new_mode(online_mode):
    with pytest.raises(device_auth.DeviceAuthError, match='gateway_upgrade_required'):
        _authorize(online_mode, online_mode['private_key'])


def test_reinstall_registration_does_not_evict_online_installation(online_mode, monkeypatch):
    from test_access_code_roaming import AccessCodeRoamingTests
    import device_recovery_routes as legacy
    import device_recovery_routes_v2 as recovery
    # Reuse the existing signing helper, exercising the real recovery route.
    helper = AccessCodeRoamingTests()
    monkeypatch.setattr(legacy, 'DATABASE_PATH', online_mode['database_path'])
    lease = acquire(online_mode)['lease_token']
    payload = helper.challenge_payload(code=online_mode['code'],
        device_id='dp1:' + 'b' * 64, key=helper.new_private_key())
    result = recovery.recovery_challenge(payload)
    assert result['status'] == 'not_needed'
    assert sessions.update(lease)['ok']


def test_limit_is_shared_between_nodes(online_mode):
    acquire(online_mode)
    other = add_device(online_mode, 'other-node-device', assignment=99, node=9)
    with pytest.raises(device_auth.DeviceAuthError, match='concurrent_limit_reached'):
        acquire(other)

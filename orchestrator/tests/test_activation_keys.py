import base64
import hashlib
import os
import sqlite3
import sys
import tempfile
import time
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

ORCHESTRATOR_DIR = Path(__file__).resolve().parents[1]
if str(ORCHESTRATOR_DIR) not in sys.path:
    sys.path.insert(0, str(ORCHESTRATOR_DIR))

os.environ.setdefault('DATABASE_PATH', ':memory:')

import api  # noqa: E402
import checkout_routes  # noqa: E402
import config  # noqa: E402
import device_auth  # noqa: E402
import storage  # noqa: E402


class ActivationKeyLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / 'activation-tests.sqlite3')
        config.DATABASE_PATH = self.db_path
        storage.DATABASE_PATH = self.db_path
        device_auth.DATABASE_PATH = self.db_path
        checkout_routes.CHECKOUT_SECRET = 'test-checkout-secret'
        checkout_routes._attempts.clear()
        storage.init_storage()
        device_auth.ensure_device_auth_storage()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    @staticmethod
    def new_private_key():
        return ec.generate_private_key(ec.SECP256R1())

    @staticmethod
    def public_key_base64(private_key) -> str:
        der = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        return base64.b64encode(der).decode('ascii')

    @staticmethod
    def sign(private_key, canonical: str) -> str:
        signature = private_key.sign(canonical.encode('utf-8'), ec.ECDSA(hashes.SHA256()))
        return base64.b64encode(signature).decode('ascii')

    @staticmethod
    def activation_canonical(
        *,
        path: str,
        raw_code: str,
        device_id: str,
        device_name: str,
        timestamp: str,
        nonce: str,
    ) -> str:
        auth_hash = hashlib.sha256(raw_code.strip().encode('utf-8')).hexdigest()
        return '\n'.join(
            (
                'method=POST',
                f'path={path}',
                f'device_id={device_id}',
                f'device_name={device_name}',
                f'timestamp={timestamp}',
                f'nonce={nonce}',
                f'auth_sha256={auth_hash}',
            )
        )

    @staticmethod
    def request_canonical(
        *,
        method: str,
        path: str,
        raw_code: str,
        device_id: str,
        timestamp: str,
        nonce: str,
    ) -> str:
        auth_hash = hashlib.sha256(raw_code.strip().encode('utf-8')).hexdigest()
        return '\n'.join(
            (
                f'method={method.upper()}',
                f'path={path}',
                f'device_id={device_id}',
                f'timestamp={timestamp}',
                f'nonce={nonce}',
                f'auth_sha256={auth_hash}',
            )
        )

    def create_code(self, plan: str, limit: int) -> str:
        result = storage.create_checkout_code(
            plan=plan,
            max_devices=limit,
            days=30,
            customer='tests',
            external_id='test-' + uuid.uuid4().hex,
        )
        return str(result['code'])

    def register(
        self,
        *,
        code: str,
        device_id: str,
        private_key=None,
        device_name: str = 'Test Android',
        nonce: str | None = None,
        timestamp: str | None = None,
        path: str = '/api/activate',
    ):
        key = private_key or self.new_private_key()
        resolved_nonce = nonce or uuid.uuid4().hex
        resolved_timestamp = timestamp or str(int(time.time() * 1000))
        canonical = self.activation_canonical(
            path=path,
            raw_code=code,
            device_id=device_id,
            device_name=device_name,
            timestamp=resolved_timestamp,
            nonce=resolved_nonce,
        )
        return device_auth.register_device(
            raw_code=code,
            path=path,
            device_id=device_id,
            device_name=device_name,
            public_key_base64=self.public_key_base64(key),
            timestamp=resolved_timestamp,
            nonce=resolved_nonce,
            signature_base64=self.sign(key, canonical),
            signature_algorithm='SHA256withECDSA',
            platform='android',
            app_version='test',
        )

    def validate_registration(
        self,
        *,
        code: str,
        device_id: str,
        private_key=None,
        device_name: str = 'Test Android',
    ):
        key = private_key or self.new_private_key()
        path = '/api/activate/validate'
        nonce = uuid.uuid4().hex
        timestamp = str(int(time.time() * 1000))
        canonical = self.activation_canonical(
            path=path,
            raw_code=code,
            device_id=device_id,
            device_name=device_name,
            timestamp=timestamp,
            nonce=nonce,
        )
        return device_auth.validate_device_registration(
            raw_code=code,
            path=path,
            device_id=device_id,
            device_name=device_name,
            public_key_base64=self.public_key_base64(key),
            timestamp=timestamp,
            nonce=nonce,
            signature_base64=self.sign(key, canonical),
            signature_algorithm='SHA256withECDSA',
        )

    def authenticate(self, *, code: str, device_id: str, private_key, path: str = '/profile'):
        timestamp = str(int(time.time() * 1000))
        nonce = uuid.uuid4().hex
        canonical = self.request_canonical(
            method='GET',
            path=path,
            raw_code=code,
            device_id=device_id,
            timestamp=timestamp,
            nonce=nonce,
        )
        return device_auth.authenticate_registered_device(
            raw_code=code,
            method='GET',
            path=path,
            device_id=device_id,
            timestamp=timestamp,
            nonce=nonce,
            signature_base64=self.sign(private_key, canonical),
            signature_algorithm='SHA256withECDSA',
        )

    def assert_concurrency_limit(self, plan: str, limit: int) -> None:
        code = self.create_code(plan, limit)
        device_ids = [f'device-{index:02d}' for index in range(limit + 1)]
        for device_id in device_ids:
            result = self.register(code=code, device_id=device_id)
            self.assertEqual(0, result['devices_used'])
            self.assertEqual(limit, result['devices_limit'])

        sessions = []
        for index, device_id in enumerate(device_ids[:limit]):
            session = device_auth.acquire_vpn_session(code, device_id)
            sessions.append(session)
            self.assertEqual(index + 1, session['active_connections'])
            self.assertEqual(limit, session['connections_limit'])

        with self.assertRaises(device_auth.DeviceAuthError) as caught:
            device_auth.acquire_vpn_session(code, device_ids[-1])
        self.assertEqual('concurrent_limit_reached', caught.exception.reason)

        device_auth.release_vpn_session(code, device_ids[0], sessions[0]['session_id'])
        replacement = device_auth.acquire_vpn_session(code, device_ids[-1])
        self.assertEqual(limit, replacement['active_connections'])

    def test_audit_generated_codes_overdevice_concurrency_matrix(self) -> None:
        cases = (
            ('personal', 1, 4),
            ('personal_plus', 2, 5),
            ('family', 5, 8),
        )
        for plan, limit, device_count in cases:
            code = self.create_code(plan, limit)
            device_ids = [f'audit-{plan}-{index:02d}' for index in range(device_count)]

            for device_id in device_ids:
                result = self.register(code=code, device_id=device_id)
                self.assertEqual(0, result['devices_used'])
                self.assertEqual(limit, result['devices_limit'])

            def acquire(device_id: str):
                try:
                    lease = device_auth.acquire_vpn_session(code, device_id)
                    return (device_id, 'success', lease)
                except device_auth.DeviceAuthError as error:
                    return (device_id, error.reason, None)

            with ThreadPoolExecutor(max_workers=device_count) as executor:
                outcomes = list(executor.map(acquire, device_ids))

            successes = [item for item in outcomes if item[1] == 'success']
            rejected = [item for item in outcomes if item[1] == 'concurrent_limit_reached']

            print(
                f'AUDIT plan={plan} code={code} registered={device_count} '
                f'limit={limit} success={len(successes)} rejected={len(rejected)}'
            )
            print(
                'AUDIT outcomes='
                + ','.join(f'{device_id}:{status}' for device_id, status, _ in outcomes)
            )

            self.assertEqual(limit, len(successes))
            self.assertEqual(device_count - limit, len(rejected))

            released_device, _, released_lease = successes[0]
            device_auth.release_vpn_session(
                code,
                released_device,
                released_lease['session_id'],
            )
            replacement_device = rejected[0][0]
            replacement = device_auth.acquire_vpn_session(code, replacement_device)
            print(
                f'AUDIT replacement plan={plan} device={replacement_device} '
                f'active={replacement["active_connections"]}/{replacement["connections_limit"]}'
            )
            self.assertEqual(limit, replacement['active_connections'])
            self.assertEqual(limit, replacement['connections_limit'])

    def test_audit_http_acquire_route_enforces_same_limits(self) -> None:
        from fastapi.testclient import TestClient

        storage.save_server(
            'Audit server',
            'audit',
            'vless://00000000-0000-0000-0000-000000000001@127.0.0.1:443#Audit',
        )

        with TestClient(api.app) as client:
            for plan, limit, device_count in (
                ('personal', 1, 3),
                ('personal_plus', 2, 4),
                ('family', 5, 7),
            ):
                code = self.create_code(plan, limit)
                keys = {}
                device_ids = [f'http-{plan}-{index:02d}' for index in range(device_count)]
                for device_id in device_ids:
                    key = self.new_private_key()
                    keys[device_id] = key
                    self.register(code=code, device_id=device_id, private_key=key)

                statuses = []
                for device_id in device_ids:
                    timestamp = str(int(time.time() * 1000))
                    nonce = uuid.uuid4().hex
                    canonical = self.request_canonical(
                        method='POST',
                        path='/api/vpn/session/acquire',
                        raw_code=code,
                        device_id=device_id,
                        timestamp=timestamp,
                        nonce=nonce,
                    )
                    response = client.post(
                        '/api/vpn/session/acquire',
                        json={
                            'access_key': code,
                            'server_id': 1,
                            'traffic_policy': 'international',
                        },
                        headers={
                            'Authorization': f'Bearer {code}',
                            'x-emery-device-id': device_id,
                            'x-emery-timestamp': timestamp,
                            'x-emery-nonce': nonce,
                            'x-emery-signature': self.sign(keys[device_id], canonical),
                            'x-emery-signature-algorithm': 'SHA256withECDSA',
                        },
                    )
                    body = response.json()
                    statuses.append((device_id, response.status_code, body.get('reason') or 'success'))

                accepted = [item for item in statuses if item[1] == 200]
                rejected = [item for item in statuses if item[2] == 'concurrent_limit_reached']
                print(
                    f'HTTP_AUDIT plan={plan} code={code} registered={device_count} '
                    f'accepted={len(accepted)} rejected={len(rejected)} limit={limit}'
                )
                print(
                    'HTTP_AUDIT outcomes='
                    + ','.join(f'{device}:{status}:{reason}' for device, status, reason in statuses)
                )
                self.assertEqual(limit, len(accepted))
                self.assertEqual(device_count - limit, len(rejected))
                self.assertTrue(all(item[1] == 409 for item in rejected))

    def test_tariff_limits_are_exact(self) -> None:
        self.assert_concurrency_limit('personal', 1)
        self.assert_concurrency_limit('personal_plus', 2)
        self.assert_concurrency_limit('family', 5)

    def test_validation_does_not_consume_slot_until_registration(self) -> None:
        code = self.create_code('personal', 1)
        first_key = self.new_private_key()
        second_key = self.new_private_key()

        first = self.validate_registration(
            code=code,
            device_id='validated-device-1',
            private_key=first_key,
        )
        second = self.validate_registration(
            code=code,
            device_id='validated-device-2',
            private_key=second_key,
        )
        self.assertEqual(0, first['devices_used'])
        self.assertEqual(0, second['devices_used'])

        with sqlite3.connect(self.db_path) as con:
            registered = con.execute(
                'SELECT COUNT(*) FROM code_devices WHERE code = ? AND active = 1',
                (storage.format_code(code),),
            ).fetchone()[0]
            used_at = con.execute(
                'SELECT used_at FROM activation_codes WHERE code = ?',
                (storage.format_code(code),),
            ).fetchone()[0]
        self.assertEqual(0, registered)
        self.assertIsNone(used_at)

        self.register(
            code=code,
            device_id='validated-device-1',
            private_key=first_key,
        )
        after_first = self.validate_registration(
            code=code,
            device_id='validated-device-2',
            private_key=second_key,
        )
        self.assertEqual(0, after_first['devices_used'])
        self.register(
            code=code,
            device_id='validated-device-2',
            private_key=second_key,
        )
        with sqlite3.connect(self.db_path) as con:
            registered_after = con.execute(
                'SELECT COUNT(*) FROM code_devices WHERE code = ? AND active = 1',
                (storage.format_code(code),),
            ).fetchone()[0]
        self.assertEqual(2, registered_after)

    def test_parallel_registration_is_not_tariff_limited(self) -> None:
        code = self.create_code('personal', 1)

        def attempt(index: int) -> str:
            try:
                self.register(code=code, device_id=f'parallel-device-{index}')
                return 'success'
            except device_auth.DeviceAuthError as error:
                return error.reason

        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(attempt, range(8)))

        self.assertEqual(8, results.count('success'))
        with sqlite3.connect(self.db_path) as con:
            count = con.execute(
                'SELECT COUNT(*) FROM code_devices WHERE code = ? AND active = 1',
                (storage.format_code(code),),
            ).fetchone()[0]
        self.assertEqual(8, count)

    def test_parallel_session_acquire_only_one_wins_for_personal(self) -> None:
        code = self.create_code('personal', 1)
        device_ids = [f'parallel-session-{index}' for index in range(8)]
        for device_id in device_ids:
            self.register(code=code, device_id=device_id)

        def acquire(device_id: str) -> str:
            try:
                device_auth.acquire_vpn_session(code, device_id)
                return 'success'
            except device_auth.DeviceAuthError as error:
                return error.reason

        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(acquire, device_ids))

        self.assertEqual(1, results.count('success'))
        self.assertEqual(7, results.count('concurrent_limit_reached'))

    def test_same_device_reuses_one_active_session(self) -> None:
        code = self.create_code('personal', 1)
        key = self.new_private_key()
        first = self.register(code=code, device_id='stable-device', private_key=key)
        second = self.register(code=code, device_id='stable-device', private_key=key)
        self.assertEqual(0, first['devices_used'])
        self.assertEqual(0, second['devices_used'])

        first_session = device_auth.acquire_vpn_session(code, 'stable-device')
        second_session = device_auth.acquire_vpn_session(code, 'stable-device')
        self.assertEqual(1, first_session['active_connections'])
        self.assertEqual(1, second_session['active_connections'])
        self.assertEqual(first_session['session_id'], second_session['session_id'])

    def test_expired_session_frees_concurrency_slot(self) -> None:
        code = self.create_code('personal', 1)
        self.register(code=code, device_id='first-device')
        self.register(code=code, device_id='second-device')
        device_auth.acquire_vpn_session(code, 'first-device')
        with sqlite3.connect(self.db_path) as con:
            con.execute(
                'UPDATE code_devices SET vpn_session_expires_at_epoch = ? WHERE code = ? AND device_id = ?',
                (int(time.time()) - 1, storage.format_code(code), 'first-device'),
            )
            con.commit()

        replacement = device_auth.acquire_vpn_session(code, 'second-device')
        self.assertEqual(1, replacement['active_connections'])

    def test_old_session_id_cannot_release_new_session(self) -> None:
        code = self.create_code('personal', 1)
        self.register(code=code, device_id='stable-device')
        old_session = device_auth.acquire_vpn_session(code, 'stable-device')
        device_auth.release_vpn_session(code, 'stable-device', old_session['session_id'])
        new_session = device_auth.acquire_vpn_session(code, 'stable-device')
        self.assertNotEqual(old_session['session_id'], new_session['session_id'])

        with self.assertRaises(device_auth.DeviceAuthError) as caught:
            device_auth.release_vpn_session(code, 'stable-device', old_session['session_id'])
        self.assertEqual('vpn_session_mismatch', caught.exception.reason)
        self.assertTrue(device_auth.is_vpn_session_active(code, 'stable-device'))

    def test_activation_code_cannot_replace_registered_device_key(self) -> None:
        code = self.create_code('personal', 1)
        old_key = self.new_private_key()
        new_key = self.new_private_key()
        self.register(code=code, device_id='stable-device', private_key=old_key)

        with self.assertRaises(device_auth.DeviceAuthError) as caught:
            self.register(code=code, device_id='stable-device', private_key=new_key)
        self.assertEqual('device_key_rotation_requires_reset', caught.exception.reason)

        profile = self.authenticate(code=code, device_id='stable-device', private_key=old_key)
        self.assertEqual(0, profile['devices_used'])
        self.assertEqual('stable-device', profile['device_id'])

    def test_revoked_device_cannot_reactivate_itself(self) -> None:
        code = self.create_code('personal', 1)
        key = self.new_private_key()
        self.register(code=code, device_id='revoked-device', private_key=key)
        with sqlite3.connect(self.db_path) as con:
            con.execute(
                'UPDATE code_devices SET active = 0 WHERE code = ? AND device_id = ?',
                (storage.format_code(code), 'revoked-device'),
            )
            con.commit()

        with self.assertRaises(device_auth.DeviceAuthError) as caught:
            self.register(code=code, device_id='revoked-device', private_key=key)

        self.assertEqual('device_revoked', caught.exception.reason)

    def test_replayed_nonce_is_rejected(self) -> None:
        code = self.create_code('personal', 1)
        key = self.new_private_key()
        nonce = uuid.uuid4().hex
        timestamp = str(int(time.time() * 1000))
        self.register(
            code=code,
            device_id='nonce-device',
            private_key=key,
            nonce=nonce,
            timestamp=timestamp,
        )
        with self.assertRaises(device_auth.DeviceAuthError) as caught:
            self.register(
                code=code,
                device_id='nonce-device',
                private_key=key,
                nonce=nonce,
                timestamp=timestamp,
            )
        self.assertEqual('device_replay_detected', caught.exception.reason)

    def test_invalid_signature_is_rejected(self) -> None:
        code = self.create_code('personal', 1)
        signing_key = self.new_private_key()
        published_key = self.new_private_key()
        timestamp = str(int(time.time() * 1000))
        nonce = uuid.uuid4().hex
        canonical = self.activation_canonical(
            path='/api/activate',
            raw_code=code,
            device_id='signature-device',
            device_name='Test Android',
            timestamp=timestamp,
            nonce=nonce,
        )
        with self.assertRaises(device_auth.DeviceAuthError) as caught:
            device_auth.register_device(
                raw_code=code,
                path='/api/activate',
                device_id='signature-device',
                device_name='Test Android',
                public_key_base64=self.public_key_base64(published_key),
                timestamp=timestamp,
                nonce=nonce,
                signature_base64=self.sign(signing_key, canonical),
                signature_algorithm='SHA256withECDSA',
                platform='android',
                app_version='test',
            )
        self.assertEqual('device_signature_invalid', caught.exception.reason)

    def test_expired_and_banned_codes_are_rejected(self) -> None:
        expired_code = self.create_code('personal', 1)
        expired_at = (datetime.now(timezone.utc) - timedelta(minutes=1)).replace(microsecond=0).isoformat()
        with sqlite3.connect(self.db_path) as con:
            con.execute(
                'UPDATE activation_codes SET expires_at = ? WHERE code = ?',
                (expired_at, storage.format_code(expired_code)),
            )
            con.commit()
        with self.assertRaises(device_auth.DeviceAuthError) as expired:
            self.register(code=expired_code, device_id='expired-device')
        self.assertEqual('expired', expired.exception.reason)
        self.assertEqual('expired', storage.get_activation_code(expired_code)['status'])

        banned_code = self.create_code('personal', 1)
        self.assertTrue(storage.revoke_activation_code(banned_code))
        with self.assertRaises(device_auth.DeviceAuthError) as banned:
            self.register(code=banned_code, device_id='banned-device')
        self.assertEqual('banned', banned.exception.reason)

    def test_code_format_is_normalized_consistently(self) -> None:
        code = self.create_code('personal', 1)
        unformatted_lowercase = ''.join(ch for ch in code if ch.isalnum()).lower()
        result = self.register(code=unformatted_lowercase, device_id='normalized-device')
        self.assertEqual(0, result['devices_used'])
        self.assertEqual(1, result['devices_limit'])

    def test_unknown_plan_or_nonstandard_limit_is_rejected(self) -> None:
        code = storage.create_activation_code(days=30, max_devices=3, plan='manual')
        with self.assertRaises(device_auth.DeviceAuthError) as caught:
            self.register(code=code, device_id='unsupported-plan-device')
        self.assertEqual('plan_limit_mismatch', caught.exception.reason)

    def test_checkout_code_issuance_requires_secret(self) -> None:
        self.assertFalse(checkout_routes.checkout_secret_valid(''))
        self.assertFalse(checkout_routes.checkout_secret_valid('wrong'))
        self.assertTrue(checkout_routes.checkout_secret_valid('test-checkout-secret'))

    def test_renewal_rejects_banned_key_and_unsafe_plan_downgrade(self) -> None:
        family_code = self.create_code('family', 5)
        for index in range(3):
            device_id = f'family-device-{index}'
            self.register(code=family_code, device_id=device_id)
            device_auth.acquire_vpn_session(family_code, device_id)
        conflict = checkout_routes.issue_renewal(
            family_code,
            'personal',
            customer='tests',
            months=1,
        )
        self.assertFalse(conflict['ok'])
        self.assertEqual('device_limit_conflict', conflict['reason'])
        self.assertEqual(3, conflict['usedDevices'])
        self.assertEqual(1, conflict['maxDevices'])

        banned_code = self.create_code('personal', 1)
        storage.revoke_activation_code(banned_code)
        banned = checkout_routes.issue_renewal(
            banned_code,
            'personal',
            customer='tests',
            months=1,
        )
        self.assertFalse(banned['ok'])
        self.assertEqual('banned', banned['reason'])

    def test_valid_renewal_can_upgrade_plan(self) -> None:
        code = self.create_code('personal', 1)
        self.register(code=code, device_id='upgrade-device')
        before = storage.get_activation_code(code)
        result = checkout_routes.issue_renewal(
            code,
            'family',
            customer='tests',
            months=2,
        )
        after = storage.get_activation_code(code)
        self.assertTrue(result['ok'])
        self.assertEqual(5, result['maxDevices'])
        self.assertEqual('family', after['plan'])
        self.assertEqual(5, after['max_devices'])
        self.assertGreater(
            storage.parse_iso(after['expires_at']),
            storage.parse_iso(before['expires_at']),
        )

    def test_checkout_marks_refund_ineligible_after_first_use(self) -> None:
        external_id = 'refund-' + uuid.uuid4().hex
        created = storage.create_checkout_code(
            plan='personal',
            max_devices=1,
            days=30,
            customer='tests',
            external_id=external_id,
        )

        unused = checkout_routes.order(external_id)
        self.assertFalse(unused['usageStarted'])
        self.assertTrue(unused['refundEligible'])

        self.register(code=created['code'], device_id='used-device')
        used = checkout_routes.order(external_id)
        self.assertTrue(used['usageStarted'])
        self.assertFalse(used['refundEligible'])
        self.assertTrue(used['usedAt'])


if __name__ == '__main__':
    unittest.main()

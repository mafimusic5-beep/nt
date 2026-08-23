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

    def assert_limit(self, plan: str, limit: int) -> None:
        code = self.create_code(plan, limit)
        for index in range(limit):
            result = self.register(code=code, device_id=f'device-{index:02d}')
            self.assertEqual(index + 1, result['devices_used'])
            self.assertEqual(limit, result['devices_limit'])
        with self.assertRaises(device_auth.DeviceAuthError) as caught:
            self.register(code=code, device_id='device-over-limit')
        self.assertEqual('device_limit_reached', caught.exception.reason)

    def test_tariff_limits_are_exact(self) -> None:
        self.assert_limit('personal', 1)
        self.assert_limit('personal_plus', 2)
        self.assert_limit('family', 5)

    def test_parallel_registration_cannot_exceed_limit(self) -> None:
        code = self.create_code('personal', 1)

        def attempt(index: int) -> str:
            try:
                self.register(code=code, device_id=f'parallel-device-{index}')
                return 'success'
            except device_auth.DeviceAuthError as error:
                return error.reason

        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(attempt, range(8)))

        self.assertEqual(1, results.count('success'))
        self.assertEqual(7, results.count('device_limit_reached'))
        with sqlite3.connect(self.db_path) as con:
            count = con.execute(
                'SELECT COUNT(*) FROM code_devices WHERE code = ? AND active = 1',
                (storage.format_code(code),),
            ).fetchone()[0]
        self.assertEqual(1, count)

    def test_same_device_does_not_consume_second_slot(self) -> None:
        code = self.create_code('personal', 1)
        key = self.new_private_key()
        first = self.register(code=code, device_id='stable-device', private_key=key)
        second = self.register(code=code, device_id='stable-device', private_key=key)
        self.assertEqual(1, first['devices_used'])
        self.assertEqual(1, second['devices_used'])

    def test_activation_code_cannot_replace_registered_device_key(self) -> None:
        code = self.create_code('personal', 1)
        old_key = self.new_private_key()
        new_key = self.new_private_key()
        self.register(code=code, device_id='stable-device', private_key=old_key)

        with self.assertRaises(device_auth.DeviceAuthError) as caught:
            self.register(code=code, device_id='stable-device', private_key=new_key)
        self.assertEqual('device_key_rotation_requires_reset', caught.exception.reason)

        profile = self.authenticate(code=code, device_id='stable-device', private_key=old_key)
        self.assertEqual(1, profile['devices_used'])
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
        self.assertEqual(1, result['devices_used'])

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
            self.register(code=family_code, device_id=f'family-device-{index}')
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

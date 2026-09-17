import base64
import hashlib
import hmac
import json
import os
import sqlite3
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.responses import JSONResponse

ORCHESTRATOR_DIR = Path(__file__).resolve().parents[1]
if str(ORCHESTRATOR_DIR) not in sys.path:
    sys.path.insert(0, str(ORCHESTRATOR_DIR))

os.environ.setdefault('DATABASE_PATH', ':memory:')

import config  # noqa: E402
import device_auth  # noqa: E402
import device_identity_aliases  # noqa: E402
import device_recovery_routes  # noqa: E402
import device_recovery_routes_v2  # noqa: E402
import storage  # noqa: E402


class AccessCodeRoamingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / 'roaming-tests.sqlite3')
        config.DATABASE_PATH = self.db_path
        storage.DATABASE_PATH = self.db_path
        device_auth.DATABASE_PATH = self.db_path
        device_identity_aliases.DATABASE_PATH = self.db_path
        device_recovery_routes.DATABASE_PATH = self.db_path
        device_recovery_routes._attempts.clear()
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
    def public_key_fingerprint(private_key) -> str:
        der = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        return hashlib.sha256(der).hexdigest()

    @staticmethod
    def sign(private_key, canonical: str) -> str:
        signature = private_key.sign(canonical.encode('utf-8'), ec.ECDSA(hashes.SHA256()))
        return base64.b64encode(signature).decode('ascii')

    @staticmethod
    def response_payload(value):
        if isinstance(value, JSONResponse):
            return value.status_code, json.loads(value.body.decode('utf-8'))
        return 200, value

    @staticmethod
    def installation_id(label: str) -> str:
        return 'dp1:' + hashlib.sha256(('test-installation:' + label).encode('utf-8')).hexdigest()

    def create_code(self, plan: str, limit: int) -> str:
        row = storage.create_checkout_code(
            plan=plan,
            max_devices=limit,
            days=30,
            customer='roaming-tests',
            external_id='roaming-' + uuid.uuid4().hex,
        )
        return str(row['code'])

    def activation_canonical(
        self,
        *,
        path: str,
        code: str,
        device_id: str,
        nonce: str,
        timestamp: str,
    ) -> str:
        return '\n'.join(
            (
                'method=POST',
                f'path={path}',
                f'device_id={device_id}',
                'device_name=Android-устройство',
                f'timestamp={timestamp}',
                f'nonce={nonce}',
                f'auth_sha256={hashlib.sha256(code.strip().encode()).hexdigest()}',
            )
        )

    def register(self, *, code: str, device_id: str, key) -> dict:
        timestamp = str(int(time.time() * 1000))
        nonce = uuid.uuid4().hex
        canonical = self.activation_canonical(
            path='/api/activate',
            code=code,
            device_id=device_id,
            nonce=nonce,
            timestamp=timestamp,
        )
        return device_auth.register_device(
            raw_code=code,
            path='/api/activate',
            device_id=device_id,
            device_name='Android-устройство',
            public_key_base64=self.public_key_base64(key),
            timestamp=timestamp,
            nonce=nonce,
            signature_base64=self.sign(key, canonical),
            signature_algorithm='SHA256withECDSA',
            platform='android',
            app_version='test',
        )

    def challenge_payload(self, *, code: str, device_id: str, key):
        timestamp = str(int(time.time() * 1000))
        nonce = uuid.uuid4().hex
        fingerprint = self.public_key_fingerprint(key)
        canonical = '\n'.join(
            (
                'protocol=skryon-device-recovery-v1',
                'stage=challenge',
                'path=/api/device/recovery/challenge',
                f'device_id={device_id}',
                f'new_key_sha256={fingerprint}',
                f'timestamp={timestamp}',
                f'nonce={nonce}',
                f'auth_sha256={hashlib.sha256(storage.format_code(code).encode()).hexdigest()}',
            )
        )
        return device_recovery_routes.RecoveryChallengeRequest(
            code=code,
            device_id=device_id,
            client_public_key=self.public_key_base64(key),
            timestamp=timestamp,
            nonce=nonce,
            signature=self.sign(key, canonical),
            signature_algorithm='SHA256withECDSA',
        )

    def roam(self, *, code: str, device_id: str, key) -> tuple[int, dict]:
        return self.response_payload(
            device_recovery_routes_v2.recovery_challenge(
                self.challenge_payload(code=code, device_id=device_id, key=key)
            )
        )

    def authenticate(self, *, code: str, device_id: str, key):
        timestamp = str(int(time.time() * 1000))
        nonce = uuid.uuid4().hex
        canonical = '\n'.join(
            (
                'method=GET',
                'path=/api/device/profile',
                f'device_id={device_id}',
                f'timestamp={timestamp}',
                f'nonce={nonce}',
                f'auth_sha256={hashlib.sha256(code.strip().encode()).hexdigest()}',
            )
        )
        return device_auth.authenticate_registered_device(
            raw_code=code,
            method='GET',
            path='/api/device/profile',
            device_id=device_id,
            timestamp=timestamp,
            nonce=nonce,
            signature_base64=self.sign(key, canonical),
            signature_algorithm='SHA256withECDSA',
        )

    def active_device_ids(self, code: str) -> list[str]:
        with sqlite3.connect(self.db_path) as con:
            return [
                row[0]
                for row in con.execute(
                    'SELECT device_id FROM code_devices WHERE code = ? AND active = 1 ORDER BY id',
                    (storage.format_code(code),),
                ).fetchall()
            ]

    def test_personal_new_installation_does_not_rebind_existing_registration(self) -> None:
        code = self.create_code('personal', 1)
        old_id = self.installation_id('personal-old')
        new_id = self.installation_id('personal-new')
        old_key = self.new_private_key()
        new_key = self.new_private_key()
        self.register(code=code, device_id=old_id, key=old_key)

        status, body = self.roam(code=code, device_id=new_id, key=new_key)
        self.assertEqual(200, status)
        self.assertEqual('not_needed', body['status'])
        self.assertFalse(body['integrity_required'])
        self.assertEqual([old_id], self.active_device_ids(code))

        registered = self.register(code=code, device_id=new_id, key=new_key)
        self.assertEqual(0, registered['devices_used'])
        self.assertEqual(1, registered['devices_limit'])
        self.assertCountEqual([old_id, new_id], self.active_device_ids(code))

        old_profile = self.authenticate(code=code, device_id=old_id, key=old_key)
        new_profile = self.authenticate(code=code, device_id=new_id, key=new_key)
        self.assertEqual(0, old_profile['devices_used'])
        self.assertEqual(0, new_profile['devices_used'])

    def test_personal_plus_registration_is_unlimited(self) -> None:
        code = self.create_code('personal_plus', 2)
        first_id = self.installation_id('plus-a')
        second_id = self.installation_id('plus-b')
        first_key = self.new_private_key()
        second_key = self.new_private_key()
        self.register(code=code, device_id=first_id, key=first_key)

        status, body = self.roam(code=code, device_id=second_id, key=second_key)
        self.assertEqual(200, status)
        self.assertEqual('not_needed', body['status'])
        self.assertEqual([first_id], self.active_device_ids(code))

        registered = self.register(code=code, device_id=second_id, key=second_key)
        self.assertEqual(0, registered['devices_used'])
        self.assertEqual(2, registered['devices_limit'])
        self.assertCountEqual([first_id, second_id], self.active_device_ids(code))

    def test_personal_plus_does_not_evict_old_registration(self) -> None:
        code = self.create_code('personal_plus', 2)
        first_id = self.installation_id('plus-oldest')
        second_id = self.installation_id('plus-newer')
        third_id = self.installation_id('plus-third')
        first_key = self.new_private_key()
        second_key = self.new_private_key()
        third_key = self.new_private_key()
        self.register(code=code, device_id=first_id, key=first_key)
        self.register(code=code, device_id=second_id, key=second_key)

        status, body = self.roam(code=code, device_id=third_id, key=third_key)
        self.assertEqual(200, status)
        self.assertEqual('not_needed', body['status'])
        self.register(code=code, device_id=third_id, key=third_key)

        self.assertCountEqual(
            [first_id, second_id, third_id],
            self.active_device_ids(code),
        )

    def test_family_limits_only_five_concurrent_sessions(self) -> None:
        code = self.create_code('family', 5)
        ids = []
        sessions = []
        for index in range(6):
            device_id = self.installation_id(f'family-{index}')
            ids.append(device_id)
            result = self.register(code=code, device_id=device_id, key=self.new_private_key())
            self.assertEqual(0, result['devices_used'])
            self.assertEqual(5, result['devices_limit'])

        self.assertEqual(6, len(self.active_device_ids(code)))
        for index, device_id in enumerate(ids[:5]):
            session = device_auth.acquire_vpn_session(code, device_id)
            sessions.append(session)
            self.assertEqual(index + 1, session['active_connections'])
            self.assertEqual(5, session['connections_limit'])

        with self.assertRaises(device_auth.DeviceAuthError) as caught:
            device_auth.acquire_vpn_session(code, ids[5])
        self.assertEqual('concurrent_limit_reached', caught.exception.reason)

        device_auth.release_vpn_session(code, ids[0], sessions[0]['session_id'])
        replacement = device_auth.acquire_vpn_session(code, ids[5])
        self.assertEqual(5, replacement['active_connections'])

    def test_same_installation_can_rotate_lost_key_without_integrity(self) -> None:
        code = self.create_code('personal', 1)
        device_id = self.installation_id('same-installation')
        old_key = self.new_private_key()
        new_key = self.new_private_key()
        self.register(code=code, device_id=device_id, key=old_key)

        status, body = self.roam(code=code, device_id=device_id, key=new_key)
        self.assertEqual(200, status)
        self.assertEqual('key_rotated', body['status'])
        self.assertFalse(body['integrity_required'])
        self.assertEqual([device_id], self.active_device_ids(code))

        self.authenticate(code=code, device_id=device_id, key=new_key)
        with self.assertRaises(device_auth.DeviceAuthError) as old_key_error:
            self.authenticate(code=code, device_id=device_id, key=old_key)
        self.assertEqual('device_signature_invalid', old_key_error.exception.reason)

    def test_manually_revoked_exact_installation_stays_revoked(self) -> None:
        code = self.create_code('personal', 1)
        device_id = self.installation_id('revoked')
        old_key = self.new_private_key()
        self.register(code=code, device_id=device_id, key=old_key)
        with sqlite3.connect(self.db_path) as con:
            con.execute(
                'UPDATE code_devices SET active = 0 WHERE code = ? AND device_id = ?',
                (storage.format_code(code), device_id),
            )
            con.commit()

        status, body = self.roam(code=code, device_id=device_id, key=self.new_private_key())
        self.assertEqual(403, status)
        self.assertEqual('device_revoked', body['reason'])

    def test_new_registration_does_not_inherit_existing_pool_assignment(self) -> None:
        code = self.create_code('personal', 1)
        old_id = self.installation_id('pool-old')
        new_id = self.installation_id('pool-new')
        old_key = self.new_private_key()
        new_key = self.new_private_key()
        self.register(code=code, device_id=old_id, key=old_key)

        with sqlite3.connect(self.db_path) as con:
            con.execute(
                'UPDATE code_devices SET pool_assignment_id = 4242 WHERE code = ? AND device_id = ?',
                (storage.format_code(code), old_id),
            )
            con.commit()

        status, body = self.roam(code=code, device_id=new_id, key=new_key)
        self.assertEqual(200, status)
        self.assertEqual('not_needed', body['status'])
        self.register(code=code, device_id=new_id, key=new_key)

        with sqlite3.connect(self.db_path) as con:
            old_assignment = con.execute(
                'SELECT pool_assignment_id FROM code_devices WHERE code = ? AND device_id = ?',
                (storage.format_code(code), old_id),
            ).fetchone()[0]
            new_assignment = con.execute(
                'SELECT pool_assignment_id FROM code_devices WHERE code = ? AND device_id = ?',
                (storage.format_code(code), new_id),
            ).fetchone()[0]
        self.assertEqual(4242, old_assignment)
        self.assertIsNone(new_assignment)


if __name__ == '__main__':
    unittest.main()

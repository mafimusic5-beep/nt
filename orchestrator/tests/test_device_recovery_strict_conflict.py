import base64
import hashlib
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
import play_integrity  # noqa: E402
import storage  # noqa: E402


class StrictDeviceConflictTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / 'strict-recovery.sqlite3')
        config.DATABASE_PATH = self.db_path
        storage.DATABASE_PATH = self.db_path
        device_auth.DATABASE_PATH = self.db_path
        device_identity_aliases.DATABASE_PATH = self.db_path
        device_recovery_routes._attempts.clear()
        storage.init_storage()
        device_auth.ensure_device_auth_storage()
        self.original_integrity_verifier = play_integrity.verify_standard_token

    def tearDown(self) -> None:
        play_integrity.verify_standard_token = self.original_integrity_verifier
        self.temp_dir.cleanup()

    @staticmethod
    def key():
        return ec.generate_private_key(ec.SECP256R1())

    @staticmethod
    def public(key) -> str:
        der = key.public_key().public_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        return base64.b64encode(der).decode('ascii')

    @staticmethod
    def fingerprint(key) -> str:
        der = key.public_key().public_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        return hashlib.sha256(der).hexdigest()

    @staticmethod
    def sign(key, canonical: str) -> str:
        signature = key.sign(canonical.encode('utf-8'), ec.ECDSA(hashes.SHA256()))
        return base64.b64encode(signature).decode('ascii')

    @staticmethod
    def response(value):
        if isinstance(value, JSONResponse):
            return value.status_code, json.loads(value.body.decode('utf-8'))
        return 200, value

    @staticmethod
    def probe(android_id: str) -> str:
        return 'dp1:' + hashlib.sha256(('skryon-device-v1:' + android_id).encode()).hexdigest()

    def create_code(self) -> str:
        row = storage.create_checkout_code(
            plan='personal',
            max_devices=1,
            days=30,
            customer='strict-conflict-tests',
            external_id='strict-' + uuid.uuid4().hex,
        )
        return str(row['code'])

    def register(self, code: str, device_id: str, key) -> None:
        timestamp = str(int(time.time() * 1000))
        nonce = uuid.uuid4().hex
        canonical = '\n'.join((
            'method=POST',
            'path=/api/activate',
            f'device_id={device_id}',
            'device_name=Android-устройство',
            f'timestamp={timestamp}',
            f'nonce={nonce}',
            f'auth_sha256={hashlib.sha256(code.strip().encode()).hexdigest()}',
        ))
        device_auth.register_device(
            raw_code=code,
            path='/api/activate',
            device_id=device_id,
            device_name='Android-устройство',
            public_key_base64=self.public(key),
            timestamp=timestamp,
            nonce=nonce,
            signature_base64=self.sign(key, canonical),
            signature_algorithm='SHA256withECDSA',
            platform='android',
            app_version='test',
        )

    def challenge(self, code: str, device_id: str, key):
        timestamp = str(int(time.time() * 1000))
        nonce = uuid.uuid4().hex
        fingerprint = self.fingerprint(key)
        canonical = '\n'.join((
            'protocol=skryon-device-recovery-v1',
            'stage=challenge',
            'path=/api/device/recovery/challenge',
            f'device_id={device_id}',
            f'new_key_sha256={fingerprint}',
            f'timestamp={timestamp}',
            f'nonce={nonce}',
            f'auth_sha256={hashlib.sha256(storage.format_code(code).encode()).hexdigest()}',
        ))
        request = device_recovery_routes.RecoveryChallengeRequest(
            code=code,
            device_id=device_id,
            client_public_key=self.public(key),
            timestamp=timestamp,
            nonce=nonce,
            signature=self.sign(key, canonical),
            signature_algorithm='SHA256withECDSA',
        )
        return self.response(device_recovery_routes.recovery_challenge(request))

    def confirm(self, code: str, device_id: str, key, challenge: dict):
        token = 'integrity-' + uuid.uuid4().hex
        timestamp = str(int(time.time() * 1000))
        nonce = uuid.uuid4().hex
        fingerprint = self.fingerprint(key)
        canonical = '\n'.join((
            'protocol=skryon-device-recovery-v1',
            'stage=confirm',
            'path=/api/device/recovery/confirm',
            f'challenge_id={challenge["challenge_id"]}',
            'server_challenge_sha256=' + hashlib.sha256(challenge['server_challenge'].encode()).hexdigest(),
            f'device_id={device_id}',
            f'new_key_sha256={fingerprint}',
            f'timestamp={timestamp}',
            f'nonce={nonce}',
            f'auth_sha256={hashlib.sha256(storage.format_code(code).encode()).hexdigest()}',
            f'integrity_token_sha256={hashlib.sha256(token.encode()).hexdigest()}',
        ))
        request = device_recovery_routes.RecoveryConfirmRequest(
            code=code,
            device_id=device_id,
            client_public_key=self.public(key),
            timestamp=timestamp,
            nonce=nonce,
            signature=self.sign(key, canonical),
            signature_algorithm='SHA256withECDSA',
            challenge_id=challenge['challenge_id'],
            server_challenge=challenge['server_challenge'],
            integrity_token=token,
        )
        play_integrity.verify_standard_token = lambda supplied, expected: {'verified': True}
        return self.response(device_recovery_routes.recovery_confirm(request))

    def trusted_return(self, code: str, device_id: str, key):
        timestamp = str(int(time.time() * 1000))
        nonce = uuid.uuid4().hex
        fingerprint = self.fingerprint(key)
        canonical = '\n'.join((
            'protocol=skryon-device-recovery-v1',
            'stage=trusted-return',
            'path=/api/device/recovery/trusted-return',
            f'device_id={device_id}',
            f'trusted_key_sha256={fingerprint}',
            f'timestamp={timestamp}',
            f'nonce={nonce}',
            f'auth_sha256={hashlib.sha256(storage.format_code(code).encode()).hexdigest()}',
        ))
        request = device_recovery_routes.TrustedReturnRequest(
            code=code,
            device_id=device_id,
            client_public_key=self.public(key),
            timestamp=timestamp,
            nonce=nonce,
            signature=self.sign(key, canonical),
            signature_algorithm='SHA256withECDSA',
        )
        return self.response(device_recovery_routes.trusted_return(request))

    def authenticate(self, code: str, device_id: str, key):
        timestamp = str(int(time.time() * 1000))
        nonce = uuid.uuid4().hex
        canonical = '\n'.join((
            'method=GET',
            'path=/api/device/profile',
            f'device_id={device_id}',
            f'timestamp={timestamp}',
            f'nonce={nonce}',
            f'auth_sha256={hashlib.sha256(code.strip().encode()).hexdigest()}',
        ))
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

    def test_no_time_window_and_original_key_wins_if_it_returns(self) -> None:
        code = self.create_code()
        device_id = self.probe('0123456789abcdef')
        key1 = self.key()
        key2 = self.key()
        self.register(code, device_id, key1)

        status, challenge = self.challenge(code, device_id, key2)
        self.assertEqual(200, status)
        self.assertTrue(challenge['integrity_required'])

        status, confirmed = self.confirm(code, device_id, key2, challenge)
        self.assertEqual(200, status)
        self.assertTrue(confirmed['trusted_key_watch'])
        self.assertEqual(device_id, self.authenticate(code, device_id, key2)['device_id'])

        with self.assertRaises(device_auth.DeviceAuthError):
            self.authenticate(code, device_id, key1)

        status, returned = self.trusted_return(code, device_id, key1)
        self.assertEqual(200, status)
        self.assertTrue(returned['security_conflict'])
        self.assertTrue(returned['recovery_locked'])
        self.assertEqual(device_id, self.authenticate(code, device_id, key1)['device_id'])

        with self.assertRaises(device_auth.DeviceAuthError):
            self.authenticate(code, device_id, key2)

        status, locked = self.challenge(code, device_id, key2)
        self.assertEqual(423, status)
        self.assertEqual('device_recovery_security_lock', locked['reason'])

        with sqlite3.connect(self.db_path) as con:
            state = con.execute(
                'SELECT state FROM device_recovery_watch WHERE code = ? AND device_id = ?',
                (storage.format_code(code), device_id),
            ).fetchone()[0]
        self.assertEqual('conflict', state)


if __name__ == '__main__':
    unittest.main()

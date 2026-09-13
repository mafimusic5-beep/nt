import base64
import hashlib
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

ORCHESTRATOR_DIR = Path(__file__).resolve().parents[1]
if str(ORCHESTRATOR_DIR) not in sys.path:
    sys.path.insert(0, str(ORCHESTRATOR_DIR))

os.environ.setdefault('DATABASE_PATH', ':memory:')

import config  # noqa: E402
import device_auth  # noqa: E402
import device_recovery_routes  # noqa: E402,F401
import play_integrity  # noqa: E402
import storage  # noqa: E402


class DeviceSecurityGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / 'guard.sqlite3')
        config.DATABASE_PATH = self.db_path
        storage.DATABASE_PATH = self.db_path
        device_auth.DATABASE_PATH = self.db_path
        device_recovery_routes._attempts.clear()
        storage.init_storage()
        device_auth.ensure_device_auth_storage()
        self.original_integrity = play_integrity.verify_standard_token

    def tearDown(self) -> None:
        play_integrity.verify_standard_token = self.original_integrity
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
        return base64.b64encode(
            key.sign(canonical.encode(), ec.ECDSA(hashes.SHA256()))
        ).decode('ascii')

    @staticmethod
    def probe(raw: str) -> str:
        return 'dp1:' + hashlib.sha256(('skryon-device-v1:' + raw).encode()).hexdigest()

    def create_code(self) -> str:
        return str(storage.create_checkout_code(
            plan='personal', max_devices=1, days=30,
            customer='guard-test', external_id='guard-' + uuid.uuid4().hex,
        )['code'])

    def register(self, code: str, device_id: str, key) -> None:
        timestamp = str(int(time.time() * 1000))
        nonce = uuid.uuid4().hex
        canonical = '\n'.join((
            'method=POST', 'path=/api/activate', f'device_id={device_id}',
            'device_name=Android-устройство', f'timestamp={timestamp}', f'nonce={nonce}',
            f'auth_sha256={hashlib.sha256(code.strip().encode()).hexdigest()}',
        ))
        device_auth.register_device(
            raw_code=code, path='/api/activate', device_id=device_id,
            device_name='Android-устройство', public_key_base64=self.public(key),
            timestamp=timestamp, nonce=nonce, signature_base64=self.sign(key, canonical),
            signature_algorithm='SHA256withECDSA', platform='android', app_version='test',
        )

    def replace_with_recovery_key(self, code: str, device_id: str, old_key, new_key) -> None:
        timestamp = str(int(time.time() * 1000))
        nonce = uuid.uuid4().hex
        fingerprint = self.fingerprint(new_key)
        challenge_canonical = '\n'.join((
            'protocol=skryon-device-recovery-v1', 'stage=challenge',
            'path=/api/device/recovery/challenge', f'device_id={device_id}',
            f'new_key_sha256={fingerprint}', f'timestamp={timestamp}', f'nonce={nonce}',
            f'auth_sha256={hashlib.sha256(storage.format_code(code).encode()).hexdigest()}',
        ))
        challenge_request = device_recovery_routes.RecoveryChallengeRequest(
            code=code, device_id=device_id, client_public_key=self.public(new_key),
            timestamp=timestamp, nonce=nonce, signature=self.sign(new_key, challenge_canonical),
            signature_algorithm='SHA256withECDSA',
        )
        challenge = device_recovery_routes.recovery_challenge(challenge_request)
        self.assertTrue(challenge['integrity_required'])

        token = 'integrity-' + uuid.uuid4().hex
        timestamp = str(int(time.time() * 1000))
        nonce = uuid.uuid4().hex
        confirm_canonical = '\n'.join((
            'protocol=skryon-device-recovery-v1', 'stage=confirm',
            'path=/api/device/recovery/confirm', f'challenge_id={challenge["challenge_id"]}',
            'server_challenge_sha256=' + hashlib.sha256(challenge['server_challenge'].encode()).hexdigest(),
            f'device_id={device_id}', f'new_key_sha256={fingerprint}',
            f'timestamp={timestamp}', f'nonce={nonce}',
            f'auth_sha256={hashlib.sha256(storage.format_code(code).encode()).hexdigest()}',
            f'integrity_token_sha256={hashlib.sha256(token.encode()).hexdigest()}',
        ))
        request = device_recovery_routes.RecoveryConfirmRequest(
            code=code, device_id=device_id, client_public_key=self.public(new_key),
            timestamp=timestamp, nonce=nonce, signature=self.sign(new_key, confirm_canonical),
            signature_algorithm='SHA256withECDSA', challenge_id=challenge['challenge_id'],
            server_challenge=challenge['server_challenge'], integrity_token=token,
        )
        play_integrity.verify_standard_token = lambda supplied, expected: {'verified': True}
        confirmed = device_recovery_routes.recovery_confirm(request)
        self.assertTrue(confirmed['recovered'])
        self.assertTrue(confirmed['trusted_key_watch'])

    def sync(self, code: str, device_id: str, key):
        path = '/api/config/sync'
        timestamp = str(int(time.time() * 1000))
        nonce = uuid.uuid4().hex
        canonical = '\n'.join((
            'method=POST', f'path={path}', f'device_id={device_id}',
            f'timestamp={timestamp}', f'nonce={nonce}',
            f'auth_sha256={hashlib.sha256(code.strip().encode()).hexdigest()}',
        ))
        return device_auth.authenticate_registered_device(
            raw_code=code, method='POST', path=path, device_id=device_id,
            timestamp=timestamp, nonce=nonce, signature_base64=self.sign(key, canonical),
            signature_algorithm='SHA256withECDSA',
        )

    def test_original_key_sync_reclaims_slot_and_ejects_replacement(self) -> None:
        code = self.create_code()
        device_id = self.probe('0123456789abcdef')
        key1 = self.key()
        key2 = self.key()
        self.register(code, device_id, key1)
        self.replace_with_recovery_key(code, device_id, key1, key2)

        self.assertEqual(device_id, self.sync(code, device_id, key2)['device_id'])
        self.assertEqual(device_id, self.sync(code, device_id, key1)['device_id'])

        with self.assertRaises(device_auth.DeviceAuthError) as blocked:
            self.sync(code, device_id, key2)
        self.assertEqual('not_bound', blocked.exception.reason)
        self.assertEqual(200, blocked.exception.status_code)

        with sqlite3.connect(self.db_path) as con:
            state = con.execute(
                'SELECT state FROM device_recovery_watch WHERE code = ? AND device_id = ?',
                (storage.format_code(code), device_id),
            ).fetchone()[0]
        self.assertEqual('conflict', state)


if __name__ == '__main__':
    unittest.main()

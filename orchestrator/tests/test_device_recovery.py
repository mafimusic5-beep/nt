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
from datetime import datetime, timedelta, timezone
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


class DeviceRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / 'recovery-tests.sqlite3')
        config.DATABASE_PATH = self.db_path
        storage.DATABASE_PATH = self.db_path
        device_auth.DATABASE_PATH = self.db_path
        device_identity_aliases.DATABASE_PATH = self.db_path
        device_recovery_routes.DATABASE_PATH = self.db_path
        device_recovery_routes._attempts.clear()
        storage.init_storage()
        device_auth.ensure_device_auth_storage()
        self.original_integrity_verifier = play_integrity.verify_standard_token

    def tearDown(self) -> None:
        play_integrity.verify_standard_token = self.original_integrity_verifier
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
    def probe(android_id: str) -> str:
        return 'dp1:' + hashlib.sha256(('skryon-device-v1:' + android_id.lower()).encode('utf-8')).hexdigest()

    def create_code(self, limit: int = 1) -> str:
        row = storage.create_checkout_code(
            plan='personal',
            max_devices=limit,
            days=30,
            customer='recovery-tests',
            external_id='recovery-' + uuid.uuid4().hex,
        )
        return str(row['code'])

    def age_trusted_key(self, *, code: str, device_id: str) -> None:
        aged = (
            datetime.now(timezone.utc)
            - timedelta(seconds=device_recovery_routes.TRUSTED_KEY_ACTIVITY_WINDOW_SECONDS + 5)
        ).isoformat()
        with sqlite3.connect(self.db_path) as con:
            con.execute(
                'UPDATE code_devices SET last_seen_at = ? WHERE code = ? AND device_id = ?',
                (aged, storage.format_code(code), device_id),
            )
            con.commit()

    def activation_canonical(self, *, path: str, code: str, device_id: str, key, nonce: str, timestamp: str) -> str:
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

    def register(self, *, code: str, device_id: str, key) -> None:
        timestamp = str(int(time.time() * 1000))
        nonce = uuid.uuid4().hex
        canonical = self.activation_canonical(
            path='/api/activate',
            code=code,
            device_id=device_id,
            key=key,
            nonce=nonce,
            timestamp=timestamp,
        )
        device_auth.register_device(
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

    def confirm_payload(self, *, code: str, device_id: str, key, challenge: dict, token: str):
        timestamp = str(int(time.time() * 1000))
        nonce = uuid.uuid4().hex
        fingerprint = self.public_key_fingerprint(key)
        canonical = '\n'.join(
            (
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
            )
        )
        return device_recovery_routes.RecoveryConfirmRequest(
            code=code,
            device_id=device_id,
            client_public_key=self.public_key_base64(key),
            timestamp=timestamp,
            nonce=nonce,
            signature=self.sign(key, canonical),
            signature_algorithm='SHA256withECDSA',
            challenge_id=challenge['challenge_id'],
            server_challenge=challenge['server_challenge'],
            integrity_token=token,
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

    def test_active_trusted_key_blocks_recovery_key_rotation(self) -> None:
        code = self.create_code()
        device_id = self.probe('0123456789abcdef')
        trusted_key = self.new_private_key()
        untrusted_key = self.new_private_key()
        self.register(code=code, device_id=device_id, key=trusted_key)

        status, body = self.response_payload(
            device_recovery_routes.recovery_challenge(
                self.challenge_payload(code=code, device_id=device_id, key=untrusted_key)
            )
        )
        self.assertEqual(409, status)
        self.assertEqual('device_recovery_trusted_key_active', body['reason'])
        self.assertGreater(body['retry_after_seconds'], 0)

        profile = self.authenticate(code=code, device_id=device_id, key=trusted_key)
        self.assertEqual(device_id, profile['device_id'])
        with self.assertRaises(device_auth.DeviceAuthError) as untrusted_error:
            self.authenticate(code=code, device_id=device_id, key=untrusted_key)
        self.assertEqual('device_signature_invalid', untrusted_error.exception.reason)

    def test_reinstall_rotates_key_only_after_integrity_and_keeps_slot(self) -> None:
        code = self.create_code()
        device_id = self.probe('0123456789abcdef')
        old_key = self.new_private_key()
        new_key = self.new_private_key()
        self.register(code=code, device_id=device_id, key=old_key)
        self.age_trusted_key(code=code, device_id=device_id)

        status, challenge = self.response_payload(
            device_recovery_routes.recovery_challenge(
                self.challenge_payload(code=code, device_id=device_id, key=new_key)
            )
        )
        self.assertEqual(200, status)
        self.assertTrue(challenge['integrity_required'])

        seen = {}
        def fake_verify(token, expected_hash):
            seen['token'] = token
            seen['request_hash'] = expected_hash
            self.assertEqual(challenge['request_hash'], expected_hash)
            return {'verified': True}
        play_integrity.verify_standard_token = fake_verify

        confirm_request = self.confirm_payload(
            code=code,
            device_id=device_id,
            key=new_key,
            challenge=challenge,
            token='test-integrity-token-' + uuid.uuid4().hex,
        )
        status, confirmed = self.response_payload(device_recovery_routes.recovery_confirm(confirm_request))
        self.assertEqual(200, status)
        self.assertTrue(confirmed['recovered'])
        self.assertTrue(seen['token'].startswith('test-integrity-token-'))

        profile = self.authenticate(code=code, device_id=device_id, key=new_key)
        self.assertEqual(device_id, profile['device_id'])
        with self.assertRaises(device_auth.DeviceAuthError) as old_key_error:
            self.authenticate(code=code, device_id=device_id, key=old_key)
        self.assertEqual('device_signature_invalid', old_key_error.exception.reason)

        with sqlite3.connect(self.db_path) as con:
            count = con.execute('SELECT COUNT(*) FROM code_devices WHERE code = ? AND active = 1', (code,)).fetchone()[0]
        self.assertEqual(1, count)

        replay = self.confirm_payload(
            code=code,
            device_id=device_id,
            key=new_key,
            challenge=challenge,
            token='another-integrity-token-' + uuid.uuid4().hex,
        )
        status, body = self.response_payload(device_recovery_routes.recovery_confirm(replay))
        self.assertEqual(401, status)
        self.assertEqual('device_recovery_replay_detected', body['reason'])

    def test_different_device_cannot_claim_existing_slot_via_recovery(self) -> None:
        code = self.create_code()
        old_key = self.new_private_key()
        self.register(code=code, device_id=self.probe('0123456789abcdef'), key=old_key)

        other_device = self.probe('fedcba9876543210')
        status, body = self.response_payload(
            device_recovery_routes.recovery_challenge(
                self.challenge_payload(code=code, device_id=other_device, key=self.new_private_key())
            )
        )
        self.assertEqual(200, status)
        self.assertEqual('not_needed', body['status'])
        self.assertFalse(body['recovered'])

        with sqlite3.connect(self.db_path) as con:
            rows = con.execute('SELECT device_id FROM code_devices WHERE code = ?', (code,)).fetchall()
        self.assertEqual([(self.probe('0123456789abcdef'),)], rows)

    def test_upgrade_with_old_key_migrates_legacy_android_id_without_integrity(self) -> None:
        code = self.create_code()
        legacy_android_id = '0123456789abcdef'
        device_id = self.probe(legacy_android_id)
        old_key = self.new_private_key()
        self.register(code=code, device_id=legacy_android_id, key=old_key)

        status, body = self.response_payload(
            device_recovery_routes.recovery_challenge(
                self.challenge_payload(code=code, device_id=device_id, key=old_key)
            )
        )
        self.assertEqual(200, status)
        self.assertEqual('migrated', body['status'])
        self.assertFalse(body['integrity_required'])

        with sqlite3.connect(self.db_path) as con:
            row = con.execute('SELECT device_id FROM code_devices WHERE code = ?', (code,)).fetchone()
            activation = con.execute('SELECT device_id FROM activation_codes WHERE code = ?', (code,)).fetchone()
        self.assertEqual(device_id, row[0])
        self.assertNotEqual(legacy_android_id, row[0])
        if activation[0] is not None:
            self.assertNotEqual(legacy_android_id, activation[0])

        profile = self.authenticate(code=code, device_id=device_id, key=old_key)
        self.assertEqual(device_id, profile['device_id'])

    def test_integrity_failure_keeps_old_key_and_challenge_unconsumed(self) -> None:
        code = self.create_code()
        device_id = self.probe('0123456789abcdef')
        old_key = self.new_private_key()
        new_key = self.new_private_key()
        self.register(code=code, device_id=device_id, key=old_key)
        self.age_trusted_key(code=code, device_id=device_id)

        status, challenge = self.response_payload(
            device_recovery_routes.recovery_challenge(
                self.challenge_payload(code=code, device_id=device_id, key=new_key)
            )
        )
        self.assertEqual(200, status)

        def reject_integrity(token, expected_hash):
            raise play_integrity.PlayIntegrityError('play_integrity_device_failed')
        play_integrity.verify_standard_token = reject_integrity

        request = self.confirm_payload(
            code=code,
            device_id=device_id,
            key=new_key,
            challenge=challenge,
            token='rejected-integrity-token-' + uuid.uuid4().hex,
        )
        status, body = self.response_payload(device_recovery_routes.recovery_confirm(request))
        self.assertEqual(403, status)
        self.assertEqual('play_integrity_device_failed', body['reason'])

        profile = self.authenticate(code=code, device_id=device_id, key=old_key)
        self.assertEqual(device_id, profile['device_id'])
        with sqlite3.connect(self.db_path) as con:
            consumed = con.execute(
                'SELECT consumed_at_epoch FROM device_recovery_challenges WHERE challenge_id = ?',
                (challenge['challenge_id'],),
            ).fetchone()[0]
        self.assertIsNone(consumed)

    def test_legacy_pool_subject_alias_is_preserved_during_id_scrub(self) -> None:
        code = self.create_code()
        legacy_android_id = '0123456789abcdef'
        device_id = self.probe(legacy_android_id)
        old_key = self.new_private_key()
        self.register(code=code, device_id=legacy_android_id, key=old_key)

        original_secret = device_identity_aliases.POOL_BRIDGE_PSEUDONYM_KEY
        device_identity_aliases.POOL_BRIDGE_PSEUDONYM_KEY = 'test-pool-pseudonym-secret'
        try:
            with sqlite3.connect(self.db_path) as con:
                con.execute(
                    'UPDATE code_devices SET pool_assignment_id = 4242 WHERE code = ? AND device_id = ?',
                    (code, legacy_android_id),
                )
                con.commit()

            status, body = self.response_payload(
                device_recovery_routes.recovery_challenge(
                    self.challenge_payload(code=code, device_id=device_id, key=old_key)
                )
            )
            self.assertEqual(200, status)
            self.assertEqual('migrated', body['status'])

            expected = hmac.new(
                b'test-pool-pseudonym-secret',
                ('legacy-device-v1\0' + code + '\0' + legacy_android_id).encode('utf-8'),
                hashlib.sha256,
            ).hexdigest()
            with sqlite3.connect(self.db_path) as con:
                row = con.execute(
                    'SELECT subject_key FROM device_pool_subject_aliases WHERE code = ? AND device_id = ?',
                    (code, device_id),
                ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(expected, row[0])
        finally:
            device_identity_aliases.POOL_BRIDGE_PSEUDONYM_KEY = original_secret


if __name__ == '__main__':
    unittest.main()

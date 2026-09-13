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
os.environ.setdefault("DATABASE_PATH", ":memory:")

import config  # noqa: E402
import device_auth  # noqa: E402
import device_identity_aliases  # noqa: E402
import device_recovery_routes  # noqa: E402
import play_integrity  # noqa: E402
import storage  # noqa: E402


class UserSafeDeviceRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.tmp.name) / "user-safe.sqlite3")
        config.DATABASE_PATH = self.db
        storage.DATABASE_PATH = self.db
        device_auth.DATABASE_PATH = self.db
        device_identity_aliases.DATABASE_PATH = self.db
        device_recovery_routes._attempts.clear()
        storage.init_storage()
        device_auth.ensure_device_auth_storage()
        self.old_integrity = play_integrity.verify_standard_token
        play_integrity.verify_standard_token = lambda token, expected: {"verified": True}

    def tearDown(self):
        play_integrity.verify_standard_token = self.old_integrity
        self.tmp.cleanup()

    @staticmethod
    def key():
        return ec.generate_private_key(ec.SECP256R1())

    @staticmethod
    def public(key):
        raw = key.public_key().public_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        return base64.b64encode(raw).decode("ascii")

    @staticmethod
    def fingerprint(key):
        raw = key.public_key().public_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        return hashlib.sha256(raw).hexdigest()

    @staticmethod
    def sign(key, canonical):
        raw = key.sign(canonical.encode(), ec.ECDSA(hashes.SHA256()))
        return base64.b64encode(raw).decode("ascii")

    @staticmethod
    def probe(raw):
        return "dp1:" + hashlib.sha256(("skryon-device-v1:" + raw).encode()).hexdigest()

    @staticmethod
    def response(value):
        if isinstance(value, JSONResponse):
            return value.status_code, json.loads(value.body.decode())
        return 200, value

    def code(self, plan="personal", limit=1):
        return str(storage.create_checkout_code(
            plan=plan, max_devices=limit, days=30,
            customer="user-safe-tests", external_id="safe-" + uuid.uuid4().hex,
        )["code"])

    def register(self, code, device_id, key):
        ts = str(int(time.time() * 1000))
        nonce = uuid.uuid4().hex
        canonical = "\n".join((
            "method=POST", "path=/api/activate", f"device_id={device_id}",
            "device_name=Android-устройство", f"timestamp={ts}", f"nonce={nonce}",
            f"auth_sha256={hashlib.sha256(code.strip().encode()).hexdigest()}",
        ))
        return device_auth.register_device(
            raw_code=code, path="/api/activate", device_id=device_id,
            device_name="Android-устройство", public_key_base64=self.public(key),
            timestamp=ts, nonce=nonce, signature_base64=self.sign(key, canonical),
            signature_algorithm="SHA256withECDSA", platform="android", app_version="test",
        )

    def challenge(self, code, device_id, key):
        ts = str(int(time.time() * 1000))
        nonce = uuid.uuid4().hex
        fp = self.fingerprint(key)
        canonical = "\n".join((
            "protocol=skryon-device-recovery-v1", "stage=challenge",
            "path=/api/device/recovery/challenge", f"device_id={device_id}",
            f"new_key_sha256={fp}", f"timestamp={ts}", f"nonce={nonce}",
            f"auth_sha256={hashlib.sha256(storage.format_code(code).encode()).hexdigest()}",
        ))
        req = device_recovery_routes.RecoveryChallengeRequest(
            code=code, device_id=device_id, client_public_key=self.public(key),
            timestamp=ts, nonce=nonce, signature=self.sign(key, canonical),
            signature_algorithm="SHA256withECDSA",
        )
        return self.response(device_recovery_routes.recovery_challenge(req))

    def confirm(self, code, device_id, key, challenge):
        token = "integrity-" + uuid.uuid4().hex
        ts = str(int(time.time() * 1000))
        nonce = uuid.uuid4().hex
        fp = self.fingerprint(key)
        canonical = "\n".join((
            "protocol=skryon-device-recovery-v1", "stage=confirm",
            "path=/api/device/recovery/confirm", f"challenge_id={challenge['challenge_id']}",
            "server_challenge_sha256=" + hashlib.sha256(challenge["server_challenge"].encode()).hexdigest(),
            f"device_id={device_id}", f"new_key_sha256={fp}", f"timestamp={ts}",
            f"nonce={nonce}", f"auth_sha256={hashlib.sha256(storage.format_code(code).encode()).hexdigest()}",
            f"integrity_token_sha256={hashlib.sha256(token.encode()).hexdigest()}",
        ))
        req = device_recovery_routes.RecoveryConfirmRequest(
            code=code, device_id=device_id, client_public_key=self.public(key),
            timestamp=ts, nonce=nonce, signature=self.sign(key, canonical),
            signature_algorithm="SHA256withECDSA", challenge_id=challenge["challenge_id"],
            server_challenge=challenge["server_challenge"], integrity_token=token,
        )
        return self.response(device_recovery_routes.recovery_confirm(req))

    def sync(self, code, device_id, key, nonce=None):
        ts = str(int(time.time() * 1000))
        nonce = nonce or uuid.uuid4().hex
        path = "/api/config/sync"
        canonical = "\n".join((
            "method=POST", f"path={path}", f"device_id={device_id}",
            f"timestamp={ts}", f"nonce={nonce}",
            f"auth_sha256={hashlib.sha256(code.strip().encode()).hexdigest()}",
        ))
        return device_auth.authenticate_registered_device(
            raw_code=code, method="POST", path=path, device_id=device_id,
            timestamp=ts, nonce=nonce, signature_base64=self.sign(key, canonical),
            signature_algorithm="SHA256withECDSA",
        )

    def recover(self, code, device_id, key):
        status, challenge = self.challenge(code, device_id, key)
        self.assertEqual(200, status)
        self.assertTrue(challenge["integrity_required"])
        status, result = self.confirm(code, device_id, key, challenge)
        self.assertEqual(200, status)
        self.assertTrue(result["recovered"])

    def test_old_trusted_key_silently_wins_and_only_replacement_is_revoked(self):
        code = self.code()
        device_id = self.probe("0123456789abcdef")
        key1, key2, key3 = self.key(), self.key(), self.key()
        self.register(code, device_id, key1)
        self.recover(code, device_id, key2)
        self.assertEqual(device_id, self.sync(code, device_id, key2)["device_id"])

        self.assertEqual(device_id, self.sync(code, device_id, key1)["device_id"])
        with self.assertRaises(device_auth.DeviceAuthError) as blocked:
            self.sync(code, device_id, key2)
        self.assertEqual("not_bound", blocked.exception.reason)

        status, denied = self.challenge(code, device_id, key2)
        self.assertEqual(403, status)
        self.assertEqual("device_recovery_key_revoked", denied["reason"])

        self.recover(code, device_id, key3)
        self.assertEqual(device_id, self.sync(code, device_id, key3)["device_id"])
        with sqlite3.connect(self.db) as con:
            count = con.execute(
                "SELECT COUNT(*) FROM code_devices WHERE code = ? AND active = 1",
                (storage.format_code(code),),
            ).fetchone()[0]
        self.assertEqual(1, count)

    def test_candidate_rate_limit_does_not_block_current_owner(self):
        code = self.code()
        device_id = self.probe("1111111111111111")
        owner, candidate = self.key(), self.key()
        self.register(code, device_id, owner)
        seen_429 = False
        for _ in range(8):
            status, _ = self.challenge(code, device_id, candidate)
            seen_429 = seen_429 or status == 429
        self.assertTrue(seen_429)
        self.assertEqual(device_id, self.sync(code, device_id, owner)["device_id"])

    def test_conflict_is_scoped_to_one_family_slot(self):
        code = self.code(plan="personal_plus", limit=2)
        dev1, dev2 = self.probe("2222222222222222"), self.probe("3333333333333333")
        key1, key2, replacement = self.key(), self.key(), self.key()
        self.register(code, dev1, key1)
        self.register(code, dev2, key2)
        self.recover(code, dev1, replacement)
        self.assertEqual(dev1, self.sync(code, dev1, key1)["device_id"])
        self.assertEqual(dev2, self.sync(code, dev2, key2)["device_id"])
        with sqlite3.connect(self.db) as con:
            active = con.execute(
                "SELECT COUNT(*) FROM code_devices WHERE code = ? AND active = 1",
                (storage.format_code(code),),
            ).fetchone()[0]
        self.assertEqual(2, active)

    def test_security_events_do_not_store_plain_activation_code(self):
        code = self.code()
        device_id = self.probe("4444444444444444")
        key1, key2 = self.key(), self.key()
        self.register(code, device_id, key1)
        self.recover(code, device_id, key2)
        self.sync(code, device_id, key1)
        with sqlite3.connect(self.db) as con:
            columns = [row[1] for row in con.execute("PRAGMA table_info(device_security_events)")]
            hashes = [row[0] for row in con.execute("SELECT code_hash FROM device_security_events")]
        self.assertNotIn("code", columns)
        self.assertTrue(hashes)
        self.assertNotIn(storage.format_code(code), hashes)


if __name__ == "__main__":
    unittest.main()

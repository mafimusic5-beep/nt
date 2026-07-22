import asyncio
import base64
import hashlib
import json
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from starlette.requests import Request


ORCHESTRATOR_DIR = Path(__file__).resolve().parents[1]
if str(ORCHESTRATOR_DIR) not in sys.path:
    sys.path.insert(0, str(ORCHESTRATOR_DIR))

import api  # noqa: E402
import device_auth  # noqa: E402
import storage  # noqa: E402


UPDATE_MESSAGE = 'Версия приложения устарела. Обновите приложение.'
SYNC_PATH = '/api/config/sync'


class ConfigSyncApiTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_database_path = storage.DATABASE_PATH
        self.original_device_auth_database_path = device_auth.DATABASE_PATH
        self.original_wait_seconds = api.CONFIG_SYNC_WAIT_SECONDS
        self.original_poll_interval_seconds = api.CONFIG_SYNC_POLL_INTERVAL_SECONDS
        self.original_min_supported_version = api.MIN_SUPPORTED_APP_VERSION_CODE
        self.original_update_message = api.APP_UPDATE_MESSAGE
        database_path = str(Path(self.temp_dir.name) / 'skryon-api-test.db')
        storage.DATABASE_PATH = database_path
        device_auth.DATABASE_PATH = database_path
        api.CONFIG_SYNC_WAIT_SECONDS = 0
        storage.init_storage()
        device_auth.ensure_device_auth_storage()

    def tearDown(self) -> None:
        storage.DATABASE_PATH = self.original_database_path
        device_auth.DATABASE_PATH = self.original_device_auth_database_path
        api.CONFIG_SYNC_WAIT_SECONDS = self.original_wait_seconds
        api.CONFIG_SYNC_POLL_INTERVAL_SECONDS = self.original_poll_interval_seconds
        api.MIN_SUPPORTED_APP_VERSION_CODE = self.original_min_supported_version
        api.APP_UPDATE_MESSAGE = self.original_update_message
        self.temp_dir.cleanup()

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
    def auth_hash(code: str) -> str:
        return hashlib.sha256(code.strip().encode('utf-8')).hexdigest()

    def register_device(self, code: str, device_id: str):
        private_key = ec.generate_private_key(ec.SECP256R1())
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
                f'auth_sha256={self.auth_hash(code)}',
            )
        )
        device_auth.register_device(
            raw_code=code,
            path='/api/activate',
            device_id=device_id,
            device_name=device_name,
            public_key_base64=self.public_key_base64(private_key),
            timestamp=timestamp,
            nonce=nonce,
            signature_base64=self.sign(private_key, canonical),
            signature_algorithm='SHA256withECDSA',
            platform='android',
            app_version='test',
        )
        return private_key

    def signed_request(self, code: str, device_id: str, private_key) -> Request:
        timestamp = str(int(time.time() * 1000))
        nonce = uuid.uuid4().hex
        canonical = '\n'.join(
            (
                'method=POST',
                f'path={SYNC_PATH}',
                f'device_id={device_id}',
                f'timestamp={timestamp}',
                f'nonce={nonce}',
                f'auth_sha256={self.auth_hash(code)}',
            )
        )
        signature = self.sign(private_key, canonical)
        headers = [
            (b'x-emery-device-id', device_id.encode('utf-8')),
            (b'x-emery-timestamp', timestamp.encode('ascii')),
            (b'x-emery-nonce', nonce.encode('ascii')),
            (b'x-emery-signature', signature.encode('ascii')),
            (b'x-emery-signature-algorithm', b'SHA256withECDSA'),
        ]
        return Request(
            {
                'type': 'http',
                'http_version': '1.1',
                'method': 'POST',
                'scheme': 'https',
                'path': SYNC_PATH,
                'raw_path': SYNC_PATH.encode('ascii'),
                'query_string': b'',
                'headers': headers,
                'client': ('127.0.0.1', 12345),
                'server': ('testserver', 443),
            }
        )

    async def test_outdated_client_receives_update_message(self) -> None:
        api.MIN_SUPPORTED_APP_VERSION_CODE = 717
        api.APP_UPDATE_MESSAGE = UPDATE_MESSAGE
        code = storage.create_activation_code()

        result = await api.sync_config(
            api.ConfigSyncRequest(
                code=code,
                deviceId='device-1',
                revision=-1,
                appVersionCode=716,
            ),
            Request(
                {
                    'type': 'http',
                    'method': 'POST',
                    'path': SYNC_PATH,
                    'headers': [],
                    'query_string': b'',
                }
            ),
        )

        self.assertEqual(
            result,
            {
                'ok': False,
                'reason': 'upgrade_required',
                'message': UPDATE_MESSAGE,
                'minVersionCode': 717,
            },
        )

    async def test_deleted_server_is_returned_to_the_bound_device(self) -> None:
        code = storage.create_activation_code()
        private_key = self.register_device(code, 'device-1')
        server_id = storage.save_server('DE-1', 'DE', 'vless://first')

        first = await api.sync_config(
            api.ConfigSyncRequest(code=code, deviceId='device-1', revision=-1),
            self.signed_request(code, 'device-1', private_key),
        )
        self.assertTrue(first['ok'])
        self.assertTrue(first['changed'])
        self.assertEqual(first['server']['id'], server_id)

        storage.delete_server(server_id)
        deleted = await api.sync_config(
            api.ConfigSyncRequest(
                code=code,
                deviceId='device-1',
                revision=first['revision'],
            ),
            self.signed_request(code, 'device-1', private_key),
        )
        self.assertTrue(deleted['ok'])
        self.assertTrue(deleted['changed'])
        self.assertIsNone(deleted['server'])

    async def test_waiting_sync_returns_as_soon_as_the_bot_deletes(self) -> None:
        code = storage.create_activation_code()
        private_key = self.register_device(code, 'device-1')
        server_id = storage.save_server('DE-1', 'DE', 'vless://first')
        revision = storage.get_server_snapshot()['revision']
        api.CONFIG_SYNC_WAIT_SECONDS = 1
        api.CONFIG_SYNC_POLL_INTERVAL_SECONDS = 0.01

        waiting_sync = asyncio.create_task(
            api.sync_config(
                api.ConfigSyncRequest(
                    code=code,
                    deviceId='device-1',
                    revision=revision,
                ),
                self.signed_request(code, 'device-1', private_key),
            ),
        )
        await asyncio.sleep(0.05)
        storage.delete_server(server_id)

        deleted = await asyncio.wait_for(waiting_sync, timeout=0.5)
        self.assertTrue(deleted['changed'])
        self.assertIsNone(deleted['server'])

    async def test_unbound_device_cannot_read_the_server_snapshot(self) -> None:
        code = storage.create_activation_code()
        self.register_device(code, 'device-1')
        storage.save_server('DE-1', 'DE', 'vless://first')
        unbound_key = ec.generate_private_key(ec.SECP256R1())

        denied = await api.sync_config(
            api.ConfigSyncRequest(code=code, deviceId='device-2', revision=-1),
            self.signed_request(code, 'device-2', unbound_key),
        )
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(
            json.loads(denied.body),
            {
                'ok': False,
                'valid': False,
                'reason': 'device_not_registered',
                'error': 'device_not_registered',
            },
        )


if __name__ == '__main__':
    unittest.main()

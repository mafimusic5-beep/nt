import asyncio
import sys
import tempfile
import unittest
from pathlib import Path


ORCHESTRATOR_DIR = Path(__file__).resolve().parents[1]
if str(ORCHESTRATOR_DIR) not in sys.path:
    sys.path.insert(0, str(ORCHESTRATOR_DIR))

import api  # noqa: E402
import storage  # noqa: E402


UPDATE_MESSAGE = 'Версия приложения устарела. Обновите приложение.'


class ConfigSyncApiTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_database_path = storage.DATABASE_PATH
        self.original_wait_seconds = api.CONFIG_SYNC_WAIT_SECONDS
        self.original_poll_interval_seconds = api.CONFIG_SYNC_POLL_INTERVAL_SECONDS
        self.original_min_supported_version = api.MIN_SUPPORTED_APP_VERSION_CODE
        self.original_update_message = api.APP_UPDATE_MESSAGE
        storage.DATABASE_PATH = str(Path(self.temp_dir.name) / 'skryon-api-test.db')
        api.CONFIG_SYNC_WAIT_SECONDS = 0
        storage.init_storage()

    def tearDown(self) -> None:
        storage.DATABASE_PATH = self.original_database_path
        api.CONFIG_SYNC_WAIT_SECONDS = self.original_wait_seconds
        api.CONFIG_SYNC_POLL_INTERVAL_SECONDS = self.original_poll_interval_seconds
        api.MIN_SUPPORTED_APP_VERSION_CODE = self.original_min_supported_version
        api.APP_UPDATE_MESSAGE = self.original_update_message
        self.temp_dir.cleanup()

    async def test_outdated_client_receives_update_message(self) -> None:
        api.MIN_SUPPORTED_APP_VERSION_CODE = 716
        api.APP_UPDATE_MESSAGE = UPDATE_MESSAGE
        code = storage.create_activation_code()
        storage.validate_activation_code(code, 'device-1')

        result = await api.sync_config(
            api.ConfigSyncRequest(
                code=code,
                deviceId='device-1',
                revision=-1,
                appVersionCode=715,
            ),
        )

        self.assertEqual(
            result,
            {
                'ok': False,
                'reason': 'upgrade_required',
                'message': UPDATE_MESSAGE,
                'minVersionCode': 716,
            },
        )

    async def test_deleted_server_is_returned_to_the_bound_device(self) -> None:
        code = storage.create_activation_code()
        storage.validate_activation_code(code, 'device-1')
        server_id = storage.save_server('DE-1', 'DE', 'vless://first')

        first = await api.sync_config(
            api.ConfigSyncRequest(code=code, deviceId='device-1', revision=-1),
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
        )
        self.assertTrue(deleted['ok'])
        self.assertTrue(deleted['changed'])
        self.assertIsNone(deleted['server'])

    async def test_waiting_sync_returns_as_soon_as_the_bot_deletes(self) -> None:
        code = storage.create_activation_code()
        storage.validate_activation_code(code, 'device-1')
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
            ),
        )
        await asyncio.sleep(0.05)
        storage.delete_server(server_id)

        deleted = await asyncio.wait_for(waiting_sync, timeout=0.5)
        self.assertTrue(deleted['changed'])
        self.assertIsNone(deleted['server'])

    async def test_unbound_device_cannot_read_the_server_snapshot(self) -> None:
        code = storage.create_activation_code()
        storage.validate_activation_code(code, 'device-1')
        storage.save_server('DE-1', 'DE', 'vless://first')

        denied = await api.sync_config(
            api.ConfigSyncRequest(code=code, deviceId='device-2', revision=-1),
        )
        self.assertEqual(denied, {'ok': False, 'reason': 'not_bound'})


if __name__ == '__main__':
    unittest.main()

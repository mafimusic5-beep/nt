import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


ORCHESTRATOR_DIR = Path(__file__).resolve().parents[1]
if str(ORCHESTRATOR_DIR) not in sys.path:
    sys.path.insert(0, str(ORCHESTRATOR_DIR))

import storage  # noqa: E402
import tg_admin  # noqa: E402
from server_pool_sync import ServerPoolSyncError  # noqa: E402


class TelegramConfigSyncTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_database_path = storage.DATABASE_PATH
        storage.DATABASE_PATH = str(Path(self.temp_dir.name) / 'skryon-bot-test.db')
        storage.init_storage()

    def tearDown(self) -> None:
        storage.DATABASE_PATH = self.original_database_path
        self.temp_dir.cleanup()

    @staticmethod
    def message(text: str):
        return SimpleNamespace(text=text, answer=AsyncMock())

    async def test_delete_is_cancelled_when_old_pool_fails(self) -> None:
        server_id = storage.save_server('DE-1', 'DE', 'vless://first@1.2.3.4:443')
        message = self.message(f'/delconfig {server_id}')

        with (
            patch.object(tg_admin, 'is_admin_message', return_value=True),
            patch.object(tg_admin, 'pool_sync_enabled', return_value=True),
            patch.object(
                tg_admin,
                'unpublish_server',
                AsyncMock(side_effect=ServerPoolSyncError('backend_unreachable')),
            ),
        ):
            await tg_admin.delconfig_cmd(message)

        self.assertIsNotNone(storage.get_server(server_id))

    async def test_delete_removes_local_after_old_pool_confirms(self) -> None:
        server_id = storage.save_server('DE-1', 'DE', 'vless://first@1.2.3.4:443')
        message = self.message(f'/delconfig {server_id}')

        with (
            patch.object(tg_admin, 'is_admin_message', return_value=True),
            patch.object(tg_admin, 'pool_sync_enabled', return_value=True),
            patch.object(tg_admin, 'unpublish_server', AsyncMock(return_value=None)),
        ):
            await tg_admin.delconfig_cmd(message)

        self.assertIsNone(storage.get_server(server_id))

    async def test_add_rolls_back_when_old_pool_fails(self) -> None:
        message = self.message(
            '/addconfig '
            'vless://00000000-0000-0000-0000-000000000000@1.2.3.4:443'
            '?security=reality&pbk=key&sid=abcd&type=tcp#Germany'
        )

        with (
            patch.object(tg_admin, 'is_admin_message', return_value=True),
            patch.object(tg_admin, 'pool_sync_enabled', return_value=True),
            patch.object(
                tg_admin,
                'publish_server',
                AsyncMock(side_effect=ServerPoolSyncError('backend_unreachable')),
            ),
        ):
            await tg_admin.addconfig_cmd(message)

        self.assertEqual(storage.list_server_records(), [])

    async def test_startup_sync_publishes_existing_configs(self) -> None:
        server_id = storage.save_server('DE-1', 'DE', 'vless://first@1.2.3.4:443')

        with patch.object(tg_admin, 'publish_server', AsyncMock(return_value=77)):
            synced, errors = await tg_admin.sync_pool_configs()

        self.assertEqual(synced, 1)
        self.assertEqual(errors, [])
        self.assertEqual(storage.get_server(server_id)['pool_node_id'], 77)


if __name__ == '__main__':
    unittest.main()

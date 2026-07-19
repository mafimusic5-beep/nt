import sys
import tempfile
import unittest
from pathlib import Path


ORCHESTRATOR_DIR = Path(__file__).resolve().parents[1]
if str(ORCHESTRATOR_DIR) not in sys.path:
    sys.path.insert(0, str(ORCHESTRATOR_DIR))

import storage  # noqa: E402


class ConfigSyncStorageTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_database_path = storage.DATABASE_PATH
        storage.DATABASE_PATH = str(Path(self.temp_dir.name) / 'skryon-test.db')
        storage.init_storage()

    def tearDown(self) -> None:
        storage.DATABASE_PATH = self.original_database_path
        self.temp_dir.cleanup()

    def test_server_snapshot_revision_tracks_bot_changes(self) -> None:
        initial = storage.get_server_snapshot()
        self.assertEqual(initial, {'revision': 0, 'server': None})

        first_id = storage.save_server('DE-1', 'DE', 'vless://first')
        first = storage.get_server_snapshot()
        self.assertEqual(first['revision'], 1)
        self.assertEqual(first['server']['id'], first_id)

        second_id = storage.save_server('NL-1', 'NL', 'vless://second')
        second = storage.get_server_snapshot()
        self.assertEqual(second['revision'], 2)
        self.assertEqual(second['server']['id'], second_id)

        self.assertTrue(storage.delete_server(second_id))
        replacement = storage.get_server_snapshot()
        self.assertEqual(replacement['revision'], 3)
        self.assertEqual(replacement['server']['id'], first_id)

        self.assertTrue(storage.delete_server(first_id))
        deleted = storage.get_server_snapshot()
        self.assertEqual(deleted, {'revision': 4, 'server': None})

    def test_failed_delete_does_not_change_revision(self) -> None:
        storage.save_server('DE-1', 'DE', 'vless://first')
        before = storage.get_server_snapshot()['revision']

        self.assertFalse(storage.delete_server(999_999))
        self.assertEqual(storage.get_server_snapshot()['revision'], before)

    def test_pool_node_mapping_is_persisted_without_changing_revision(self) -> None:
        server_id = storage.save_server('DE-1', 'DE', 'vless://first')
        revision = storage.get_server_snapshot()['revision']

        self.assertTrue(storage.set_server_pool_node_id(server_id, 42))
        record = storage.get_server(server_id)

        self.assertIsNotNone(record)
        self.assertEqual(record['pool_node_id'], 42)
        self.assertEqual(storage.get_server_snapshot()['revision'], revision)

    def test_config_sync_requires_the_activated_device(self) -> None:
        code = storage.create_activation_code()
        self.assertTrue(storage.validate_activation_code(code, 'device-1')['ok'])

        allowed = storage.check_activation_access(code, 'device-1')
        self.assertTrue(allowed['ok'])

        denied = storage.check_activation_access(code, 'device-2')
        self.assertEqual(denied, {'ok': False, 'reason': 'not_bound'})

        self.assertTrue(storage.revoke_activation_code(code))
        revoked = storage.check_activation_access(code, 'device-1')
        self.assertEqual(revoked, {'ok': False, 'reason': 'banned'})


if __name__ == '__main__':
    unittest.main()

import sys
import tempfile
import unittest
from pathlib import Path


ORCHESTRATOR_DIR = Path(__file__).resolve().parents[1]
if str(ORCHESTRATOR_DIR) not in sys.path:
    sys.path.insert(0, str(ORCHESTRATOR_DIR))

import config  # noqa: E402


class SharedEmeryEnvironmentTest(unittest.TestCase):
    def test_reads_existing_admin_key_without_a_second_secret(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / '.env'
            env_path.write_text(
                'ADMIN_API_KEY=existing-admin-key\n'
                'BACKEND_BASE_URL=http://127.0.0.1:9330\n',
                encoding='utf-8',
            )

            self.assertEqual(
                config._shared_emery_value('ADMIN_API_KEY', (env_path,)),
                'existing-admin-key',
            )
            self.assertEqual(
                config._shared_emery_value('BACKEND_BASE_URL', (env_path,)),
                'http://127.0.0.1:9330',
            )


if __name__ == '__main__':
    unittest.main()

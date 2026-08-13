import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch


ORCHESTRATOR_DIR = Path(__file__).resolve().parents[1]
if str(ORCHESTRATOR_DIR) not in sys.path:
    sys.path.insert(0, str(ORCHESTRATOR_DIR))

import server_pool_sync  # noqa: E402


class ServerPoolSyncTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.server = {
            'id': 7,
            'name': 'Germany',
            'region': 'AUTO',
            'config': (
                'vless://00000000-0000-0000-0000-000000000000@203.0.113.10:443'
                '?security=reality&pbk=key&sid=abcd&type=tcp#Germany'
            ),
            'pool_node_id': None,
        }

    async def test_publish_creates_a_legacy_pool_node(self) -> None:
        request = AsyncMock(side_effect=[[], {'id': 31}])
        with patch.object(server_pool_sync, '_request', request):
            node_id = await server_pool_sync.publish_server(self.server)

        self.assertEqual(node_id, 31)
        create_call = request.await_args_list[1]
        self.assertEqual(create_call.args[:2], ('POST', '/api/v1/admin/nodes'))
        self.assertEqual(create_call.kwargs['payload']['provider'], 'skryon-legacy')
        self.assertEqual(create_call.kwargs['payload']['region_code'], 'de')
        self.assertEqual(create_call.kwargs['payload']['endpoint'], '203.0.113.10')
        self.assertEqual(create_call.kwargs['payload']['capacity_clients'], 20)
        self.assertEqual(create_call.kwargs['payload']['bandwidth_limit_mbps'], 600)
        self.assertEqual(create_call.kwargs['payload']['per_device_speed_limit_mbps'], 30)

    async def test_publish_reenables_an_existing_disabled_node(self) -> None:
        self.server['pool_node_id'] = 31
        nodes = [{'id': 31, 'status': 'maintenance', 'health_status': 'down'}]
        request = AsyncMock(side_effect=[nodes, {'node_id': 31, 'status': 'ok'}])
        with patch.object(server_pool_sync, '_request', request):
            node_id = await server_pool_sync.publish_server(self.server)

        self.assertEqual(node_id, 31)
        request.assert_awaited_with('POST', '/api/v1/admin/nodes/31/enable')

    async def test_unpublish_disables_the_mapped_node(self) -> None:
        self.server['pool_node_id'] = 31
        nodes = [{'id': 31, 'status': 'active', 'health_status': 'healthy'}]
        request = AsyncMock(side_effect=[nodes, {'node_id': 31, 'status': 'ok'}])
        with patch.object(server_pool_sync, '_request', request):
            await server_pool_sync.unpublish_server(self.server)

        request.assert_awaited_with('POST', '/api/v1/admin/nodes/31/disable')


if __name__ == '__main__':
    unittest.main()

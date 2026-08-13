from __future__ import annotations

import re
from urllib.parse import unquote, urlparse

import httpx

from config import SERVER_POOL_SYNC_ADMIN_KEY, SERVER_POOL_SYNC_URL


POOL_PROVIDER = 'skryon-legacy'
REQUEST_TIMEOUT_SECONDS = 15.0
SERVER_CAPACITY_DEVICES = 20
SERVER_BANDWIDTH_LIMIT_MBPS = 600
PER_DEVICE_SPEED_LIMIT_MBPS = 30

_REGION_ALIASES = {
    'de': ('de', 'germany', 'deutschland', 'герман', 'frankfurt', 'франкфурт'),
    'nl': ('nl', 'netherlands', 'nederland', 'нидер', 'amsterdam', 'амстердам'),
    'fr': ('fr', 'france', 'франц', 'paris', 'париж'),
    'pl': ('pl', 'poland', 'польш', 'warsaw', 'варшав'),
    'uk': ('uk', 'gb', 'united kingdom', 'london', 'лондон'),
    'us': ('us', 'usa', 'united states', 'america', 'new york'),
}


class ServerPoolSyncError(RuntimeError):
    pass


def is_enabled() -> bool:
    return bool(SERVER_POOL_SYNC_URL and SERVER_POOL_SYNC_ADMIN_KEY)


def endpoint_from_config(config_text: str) -> str:
    try:
        return (urlparse(config_text.strip()).hostname or '').strip()
    except ValueError:
        return ''


def region_code_from_server(server: dict) -> str:
    config = str(server.get('config') or '').strip()
    remark = unquote(config.rpartition('#')[2]).strip() if '#' in config else ''
    candidates = (
        str(server.get('region') or '').strip(),
        str(server.get('name') or '').strip(),
        remark,
    )
    searchable = ' '.join(candidates).lower().replace('_', ' ').replace('-', ' ')
    tokens = set(re.findall(r'[a-z0-9]+', searchable))
    for region_code, aliases in _REGION_ALIASES.items():
        if any(alias in searchable if len(alias) > 2 else alias in tokens for alias in aliases):
            return region_code

    explicit = re.sub(r'[^a-z0-9-]+', '-', candidates[0].lower()).strip('-')
    if explicit and explicit not in {'auto', 'unknown'}:
        return explicit[:16]
    return 'unknown'


def _headers() -> dict[str, str]:
    return {'X-Admin-Api-Key': SERVER_POOL_SYNC_ADMIN_KEY}


def _error_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return f'http_{response.status_code}'
    if isinstance(payload, dict):
        return str(payload.get('detail') or payload.get('error') or f'http_{response.status_code}')
    return f'http_{response.status_code}'


async def _request(method: str, path: str, *, payload: dict | None = None):
    if not is_enabled():
        raise ServerPoolSyncError('pool_sync_not_configured')
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            response = await client.request(
                method,
                SERVER_POOL_SYNC_URL + path,
                headers=_headers(),
                json=payload,
            )
    except httpx.HTTPError as exc:
        raise ServerPoolSyncError('pool_backend_unreachable') from exc
    if response.status_code >= 400:
        raise ServerPoolSyncError(_error_detail(response))
    if not response.content:
        return None
    try:
        return response.json()
    except ValueError as exc:
        raise ServerPoolSyncError('invalid_pool_backend_response') from exc


async def list_pool_nodes() -> list[dict]:
    payload = await _request('GET', '/api/v1/admin/nodes')
    return payload if isinstance(payload, list) else []


def _find_pool_node(server: dict, nodes: list[dict]) -> dict | None:
    mapped_id = server.get('pool_node_id')
    if mapped_id:
        match = next((node for node in nodes if int(node.get('id') or 0) == int(mapped_id)), None)
        if match:
            return match

    endpoint = endpoint_from_config(str(server.get('config') or ''))
    name = str(server.get('name') or '').strip().casefold()
    for node in nodes:
        if str(node.get('endpoint') or '').strip() != endpoint:
            continue
        provider = str(node.get('provider') or '').strip()
        remote_name = str(node.get('name') or '').strip().casefold()
        if provider == POOL_PROVIDER or (name and remote_name == name):
            return node
    return None


async def publish_server(server: dict) -> int:
    endpoint = endpoint_from_config(str(server.get('config') or ''))
    if not endpoint:
        raise ServerPoolSyncError('invalid_vless_endpoint')

    nodes = await list_pool_nodes()
    existing = _find_pool_node(server, nodes)
    if existing:
        node_id = int(existing['id'])
        if existing.get('status') != 'active' or existing.get('health_status') not in {'healthy', 'degraded'}:
            await _request('POST', f'/api/v1/admin/nodes/{node_id}/enable')
        return node_id

    server_id = int(server['id'])
    created = await _request(
        'POST',
        '/api/v1/admin/nodes',
        payload={
            'name': str(server.get('name') or f'Server {server_id}'),
            'region_code': region_code_from_server(server),
            'provider': POOL_PROVIDER,
            'endpoint': endpoint,
            'config_payload': str(server.get('config') or ''),
            'status': 'active',
            'health_status': 'healthy',
            'load_score': 100,
            'priority': 0,
            'capacity_clients': SERVER_CAPACITY_DEVICES,
            'bandwidth_limit_mbps': SERVER_BANDWIDTH_LIMIT_MBPS,
            'current_clients': 0,
            'per_device_speed_limit_mbps': PER_DEVICE_SPEED_LIMIT_MBPS,
        },
    )
    if not isinstance(created, dict) or not created.get('id'):
        raise ServerPoolSyncError('missing_pool_node_id')
    return int(created['id'])


async def unpublish_server(server: dict) -> None:
    nodes = await list_pool_nodes()
    existing = _find_pool_node(server, nodes)
    if not existing:
        return
    await _request('POST', f"/api/v1/admin/nodes/{int(existing['id'])}/disable")

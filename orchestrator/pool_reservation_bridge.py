from __future__ import annotations

import hashlib
import hmac
import re
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

from config import (
    POOL_BRIDGE_API_KEY,
    POOL_BRIDGE_ENABLED,
    POOL_BRIDGE_PSEUDONYM_KEY,
    POOL_BRIDGE_REGION_CODE,
    POOL_BRIDGE_TIMEOUT_SECONDS,
    POOL_BRIDGE_URL,
)


_REGION_RE = re.compile(r'^[a-z0-9-]{1,16}$')


@dataclass
class PoolBridgeError(RuntimeError):
    reason: str
    status_code: int = 503

    def __str__(self) -> str:
        return self.reason


def is_enabled() -> bool:
    return bool(
        POOL_BRIDGE_ENABLED
        and POOL_BRIDGE_URL
        and POOL_BRIDGE_API_KEY
        and POOL_BRIDGE_PSEUDONYM_KEY
    )


def _pseudonym(namespace: str, *parts: str) -> str:
    message = '\0'.join((namespace, *(str(part).strip() for part in parts)))
    return hmac.new(
        POOL_BRIDGE_PSEUDONYM_KEY.encode('utf-8'),
        message.encode('utf-8'),
        hashlib.sha256,
    ).hexdigest()


def _subject_key(formatted_code: str, device_id: str) -> str:
    return _pseudonym('legacy-device-v1', formatted_code, device_id)


def _entitlement_hash(formatted_code: str, plan: str, expires_at: str) -> str:
    return _pseudonym('legacy-entitlement-v1', formatted_code, plan, expires_at)


def _error_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return f'pool_http_{response.status_code}'
    if isinstance(payload, dict):
        return str(payload.get('detail') or payload.get('error') or f'pool_http_{response.status_code}')
    return f'pool_http_{response.status_code}'


def _request(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    if not is_enabled():
        raise PoolBridgeError('pool_bridge_not_configured', 503)
    try:
        response = httpx.post(
            POOL_BRIDGE_URL + path,
            headers={'X-Pool-Bridge-Key': POOL_BRIDGE_API_KEY},
            json=payload,
            timeout=POOL_BRIDGE_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        raise PoolBridgeError('pool_backend_unreachable', 503) from exc
    if response.status_code >= 400:
        detail = _error_detail(response)
        status = 409 if response.status_code == 409 else 403 if response.status_code == 403 else 503
        raise PoolBridgeError(detail, status)
    try:
        result = response.json()
    except ValueError as exc:
        raise PoolBridgeError('invalid_pool_backend_response', 503) from exc
    if not isinstance(result, dict):
        raise PoolBridgeError('invalid_pool_backend_response', 503)
    return result


def _validated_assignment(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        assignment_id = int(payload['assignment_id'])
        node_id = int(payload['node_id'])
        revision = int(payload['config_revision'])
        speed = int(payload['speed_limit_mbps'])
        config = str(payload['config']).strip()
        parsed = urlsplit(config)
        parsed_uuid = uuid.UUID(parsed.username or '')
        port = parsed.port
    except (KeyError, TypeError, ValueError) as exc:
        raise PoolBridgeError('invalid_pool_assignment', 503) from exc
    if assignment_id <= 0 or node_id <= 0 or revision <= 0:
        raise PoolBridgeError('invalid_pool_assignment', 503)
    if parsed.scheme != 'vless' or not parsed.hostname or port is None or not (1024 <= port <= 65535):
        raise PoolBridgeError('invalid_pool_assignment_config', 503)
    if str(parsed_uuid) != (parsed.username or '').lower():
        raise PoolBridgeError('invalid_pool_assignment_uuid', 503)
    if speed <= 0 or speed > 30:
        raise PoolBridgeError('invalid_pool_speed_limit', 503)

    region = str(payload.get('region_code') or '').strip().lower()
    if not _REGION_RE.fullmatch(region):
        raise PoolBridgeError('invalid_pool_region', 503)
    confirmation_required = bool(payload.get('confirmation_required'))
    token = str(payload.get('confirmation_token') or '').strip()
    if confirmation_required and len(token) < 32:
        raise PoolBridgeError('missing_pool_confirmation_token', 503)
    return {
        'pool_assignment_id': assignment_id,
        'pool_status': str(payload.get('status') or 'pending')[:32],
        'pool_confirmation_token': token,
        'pool_node_id': node_id,
        'pool_node_name': str(payload.get('node_name') or f'Server {node_id}')[:128],
        'pool_region': region,
        'pool_config': config,
        'pool_config_revision': revision,
        'pool_speed_limit_mbps': speed,
        'pool_entitlement_expires_at': str(payload.get('entitlement_expires_at') or ''),
        'confirmation_required': confirmation_required,
    }


def prepare_assignment(
    *,
    formatted_code: str,
    device_id: str,
    plan: str,
    expires_at: str,
) -> dict[str, Any]:
    region = POOL_BRIDGE_REGION_CODE if _REGION_RE.fullmatch(POOL_BRIDGE_REGION_CODE) else 'auto'
    entitlement = _entitlement_hash(formatted_code, plan, expires_at)
    response = _request(
        '/api/v1/internal/pool/assignments/prepare',
        {
            'subject_type': 'legacy_device',
            'subject_key': _subject_key(formatted_code, device_id),
            'entitlement_hash': entitlement,
            'entitlement_expires_at': expires_at,
            'region_code': region,
        },
    )
    result = _validated_assignment(response)
    result['pool_entitlement_hash'] = entitlement
    return result


def confirm_assignment(assignment_id: int, confirmation_token: str) -> dict[str, Any]:
    if assignment_id <= 0 or len(confirmation_token.strip()) < 32:
        raise PoolBridgeError('invalid_pool_confirmation', 503)
    return _request(
        '/api/v1/internal/pool/assignments/confirm',
        {
            'assignment_id': assignment_id,
            'confirmation_token': confirmation_token,
        },
    )


def confirm_persisted_assignment(assignment: dict[str, Any]) -> dict[str, Any]:
    """Confirm after the local transaction; leave retry data on failure."""

    from storage import mark_device_pool_assignment_confirmed

    token = str(assignment.get('pool_confirmation_token') or '')
    assignment_id = int(assignment.get('pool_assignment_id') or 0)
    if not bool(assignment.get('confirmation_required')):
        mark_device_pool_assignment_confirmed(assignment_id)
        assignment['pool_status'] = 'active'
        assignment['pool_confirmation_token'] = ''
        return assignment
    confirmed = confirm_assignment(assignment_id, token)
    if str(confirmed.get('status') or '') != 'active':
        raise PoolBridgeError('pool_confirmation_failed', 503)
    mark_device_pool_assignment_confirmed(assignment_id)
    assignment['pool_status'] = 'active'
    assignment['pool_confirmation_token'] = ''
    assignment['confirmation_required'] = False
    return assignment


def refresh_stored_assignment(raw_code: str, device_id: str) -> dict[str, Any]:
    """Refresh entitlement/config for an already authenticated legacy device."""

    from storage import (
        get_device_pool_assignment,
        get_device_pool_entitlement,
        save_device_pool_assignment,
    )

    entitlement = get_device_pool_entitlement(raw_code, device_id)
    if not entitlement:
        raise PoolBridgeError('device_not_registered', 403)

    stored = get_device_pool_assignment(entitlement['code'], device_id)
    if stored and stored.get('pool_status') == 'pending' and stored.get('pool_confirmation_token'):
        try:
            stored['confirmation_required'] = True
            return confirm_persisted_assignment(stored)
        except PoolBridgeError:
            # The token may have expired after an API restart.  Re-preparing is
            # idempotent for the same pseudonymous subject and rotates the token.
            pass

    prepared = prepare_assignment(
        formatted_code=entitlement['code'],
        device_id=device_id,
        plan=entitlement['plan'],
        expires_at=entitlement['expires_at'],
    )
    save_device_pool_assignment(entitlement['code'], device_id, prepared)
    return confirm_persisted_assignment(prepared)

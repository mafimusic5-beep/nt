import asyncio
import hashlib
import hmac
import json
import secrets
import time
from urllib.error import HTTPError, URLError
from urllib.request import urlopen
from collections import defaultdict, deque
from typing import Deque, Dict

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from checkout_routes import router as checkout_router
from config import (
    APP_UPDATE_MESSAGE,
    DEFAULT_SERVER_CONFIG,
    DEFAULT_SERVER_NAME,
    DEFAULT_SERVER_REGION,
    DEVICE_GATE_API_KEY,
    MIN_SUPPORTED_APP_VERSION_CODE,
    SERVER_POOL_SYNC_URL,
)
from device_auth import (
    DeviceAuthError,
    acquire_vpn_session,
    authenticate_registered_device,
    authorize_gateway_connection,
    ensure_device_auth_storage,
    heartbeat_vpn_session,
    is_vpn_session_active,
    register_device,
    release_vpn_session,
    validate_device_registration,
)
from pool_reservation_bridge import (
    PoolBridgeError,
    apply_stored_assignment_policy,
    is_enabled as pool_bridge_enabled,
    refresh_stored_assignment,
    release_assignment,
)
from storage import clear_device_pool_assignment, get_server_snapshot, init_storage, save_server


app = FastAPI(title='Skryon Orchestrator API')
app.include_router(checkout_router)

RATE_LIMIT_WINDOW_SECONDS = 300
RATE_LIMIT_MAX_ATTEMPTS = 12
CONFIG_SYNC_WAIT_SECONDS = 25.0
CONFIG_SYNC_POLL_INTERVAL_SECONDS = 0.5
_RATE_LIMIT_PSEUDONYM_KEY = secrets.token_bytes(32)
_attempts: Dict[str, Deque[float]] = defaultdict(deque)


class DeviceRegisterRequest(BaseModel):
    key: str = Field(default='', max_length=64)
    access_key: str = Field(default='', max_length=64)
    device_id: str = Field(min_length=4, max_length=128)
    device_name: str = Field(min_length=1, max_length=80)
    client_public_key: str = Field(min_length=32, max_length=4096)
    timestamp: str = Field(min_length=1, max_length=32)
    nonce: str = Field(min_length=16, max_length=128)
    signature: str = Field(min_length=16, max_length=2048)
    signature_algorithm: str = Field(default='SHA256withECDSA', max_length=64)
    client_platform: str = Field(default='android', max_length=32)
    app_version: str = Field(default='', max_length=32)
    app_version_code: int = Field(default=0, ge=0)


class ActivationRequest(BaseModel):
    code: str = Field(min_length=1, max_length=32)
    deviceId: str = Field(min_length=4, max_length=128)
    deviceName: str = Field(default='', max_length=80)
    client_public_key: str = Field(default='', max_length=4096)
    timestamp: str = Field(default='', max_length=32)
    nonce: str = Field(default='', max_length=128)
    signature: str = Field(default='', max_length=2048)
    signature_algorithm: str = Field(default='SHA256withECDSA', max_length=64)
    appVersionCode: int = Field(default=0, ge=0)


class ConfigSyncRequest(BaseModel):
    code: str = Field(min_length=1, max_length=32)
    deviceId: str = Field(min_length=4, max_length=128)
    revision: int = Field(default=-1, ge=-1)
    appVersionCode: int = Field(default=0, ge=0)


class RegionalPolicyRequest(BaseModel):
    code: str = Field(min_length=1, max_length=32)
    deviceId: str = Field(min_length=4, max_length=128)
    trafficPolicy: str = Field(pattern=r'^(international|russia)$')
    appVersionCode: int = Field(default=0, ge=0)


class VpnSessionAcquireRequest(BaseModel):
    access_key: str = Field(min_length=1, max_length=64)
    server_id: int = Field(gt=0)
    traffic_policy: str = Field(pattern=r'^(international|russia)$')


class VpnSessionControlRequest(BaseModel):
    session_id: str = Field(min_length=16, max_length=128)


class DeviceGateAuthorizeRequest(BaseModel):
    assignment_id: int = Field(gt=0)
    node_id: int = Field(gt=0)
    gate_server_name: str = Field(min_length=1, max_length=255)
    gate_spki_sha256: str = Field(pattern=r'^[a-fA-F0-9]{64}$')
    device_id: str = Field(min_length=4, max_length=128)
    server_issued_at: str = Field(min_length=1, max_length=32)
    timestamp: str = Field(min_length=1, max_length=32)
    server_nonce: str = Field(min_length=16, max_length=128)
    client_nonce: str = Field(min_length=16, max_length=128)
    signature: str = Field(min_length=16, max_length=2048)
    signature_algorithm: str = Field(default='SHA256withECDSA', max_length=64)


def upgrade_required(app_version_code: int) -> bool:
    return (
        MIN_SUPPORTED_APP_VERSION_CODE > 0
        and app_version_code > 0
        and app_version_code < MIN_SUPPORTED_APP_VERSION_CODE
    )


def upgrade_required_response() -> dict:
    return {
        'ok': False,
        'reason': 'upgrade_required',
        'message': APP_UPDATE_MESSAGE,
        'minVersionCode': MIN_SUPPORTED_APP_VERSION_CODE,
    }


def client_key(request: Request, payload: ActivationRequest) -> str:
    forwarded = request.headers.get('x-forwarded-for', '')
    ip = forwarded.split(',')[0].strip() if forwarded else (request.client.host if request.client else 'unknown')
    device = payload.deviceId.strip()[:64]
    material = (ip + '\0' + device).encode('utf-8', errors='ignore')
    return hmac.new(
        _RATE_LIMIT_PSEUDONYM_KEY,
        material,
        hashlib.sha256,
    ).hexdigest()


def rate_limited(key: str) -> bool:
    now = time.time()
    bucket = _attempts[key]
    while bucket and now - bucket[0] > RATE_LIMIT_WINDOW_SECONDS:
        bucket.popleft()
    if len(bucket) >= RATE_LIMIT_MAX_ATTEMPTS:
        return True
    bucket.append(now)
    return False


def _header(request: Request, name: str) -> str:
    value = request.headers.get(name, '').strip()
    if not value:
        raise DeviceAuthError('device_signature_missing', 401)
    return value


def _require_header_match(body_value: str, header_value: str) -> None:
    if body_value.strip() and body_value.strip() != header_value:
        raise DeviceAuthError('device_proof_mismatch', 401)


def _bearer_code(request: Request) -> str:
    authorization = request.headers.get('authorization', '').strip()
    if not authorization.lower().startswith('bearer '):
        raise DeviceAuthError('invalid_or_expired_key', 401)
    code = authorization[7:].strip()
    if not code:
        raise DeviceAuthError('invalid_or_expired_key', 401)
    return code


def _auth_error(error: DeviceAuthError):
    return JSONResponse(
        status_code=error.status_code,
        content={
            'ok': False,
            'valid': False,
            'reason': error.reason,
            'error': error.reason,
        },
    )


def _pool_assignment_server(assignment: dict) -> dict:
    return {
        'id': int(assignment.get('pool_node_id') or 0),
        'name': str(assignment.get('pool_node_name') or 'VPN'),
        'region': str(assignment.get('pool_region') or ''),
        'config': str(assignment.get('pool_config') or ''),
    }


@app.middleware('http')
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['Cache-Control'] = 'no-store'
    return response


@app.on_event('startup')
def on_startup() -> None:
    init_storage()
    ensure_device_auth_storage()
    if DEFAULT_SERVER_CONFIG and not get_server_snapshot()['server']:
        save_server(DEFAULT_SERVER_NAME, DEFAULT_SERVER_REGION, DEFAULT_SERVER_CONFIG)


@app.get('/health')
def health() -> dict:
    return {'ok': True}


@app.get('/api/vpn/servers')
def vpn_servers():
    upstream = f"{SERVER_POOL_SYNC_URL.rstrip('/')}/api/v1/vpn/servers"
    try:
        with urlopen(upstream, timeout=5.0) as response:
            if response.status != 200:
                raise RuntimeError('server_list_unavailable')
            payload = json.loads(response.read().decode('utf-8'))
        if not isinstance(payload, list):
            raise ValueError('invalid_server_list')
        return payload
    except (HTTPError, URLError, TimeoutError, ValueError, RuntimeError):
        return JSONResponse(
            status_code=503,
            content={'ok': False, 'reason': 'server_list_unavailable'},
        )

@app.post('/api/device-gate/authorize')
@app.post('/internal/device-gate/authorize')
def device_gate_authorize(payload: DeviceGateAuthorizeRequest, request: Request):
    supplied_key = request.headers.get('x-device-gate-key', '').strip()
    if (
        len(DEVICE_GATE_API_KEY) < 32
        or not supplied_key
        or not secrets.compare_digest(supplied_key, DEVICE_GATE_API_KEY)
    ):
        return _auth_error(DeviceAuthError('device_gate_forbidden', 403))
    try:
        return authorize_gateway_connection(
            assignment_id=payload.assignment_id,
            node_id=payload.node_id,
            gate_server_name=payload.gate_server_name,
            gate_spki_sha256=payload.gate_spki_sha256,
            device_id=payload.device_id,
            server_issued_at=payload.server_issued_at,
            timestamp=payload.timestamp,
            server_nonce=payload.server_nonce,
            client_nonce=payload.client_nonce,
            signature_base64=payload.signature,
            signature_algorithm=payload.signature_algorithm,
        )
    except DeviceAuthError as error:
        return _auth_error(error)


@app.post('/api/device/register')
@app.post('/auth/key')
def device_register(payload: DeviceRegisterRequest, request: Request):
    raw_code = payload.key.strip() or payload.access_key.strip()
    if not raw_code:
        return _auth_error(DeviceAuthError('bad_request', 400))

    try:
        header_device_id = _header(request, 'x-emery-device-id')
        header_timestamp = _header(request, 'x-emery-timestamp')
        header_nonce = _header(request, 'x-emery-nonce')
        header_signature = _header(request, 'x-emery-signature')
        header_algorithm = _header(request, 'x-emery-signature-algorithm')
        _require_header_match(payload.device_id, header_device_id)
        _require_header_match(payload.timestamp, header_timestamp)
        _require_header_match(payload.nonce, header_nonce)
        _require_header_match(payload.signature, header_signature)
        _require_header_match(payload.signature_algorithm, header_algorithm)

        return register_device(
            raw_code=raw_code,
            path=request.url.path,
            device_id=header_device_id,
            device_name=payload.device_name,
            public_key_base64=payload.client_public_key,
            timestamp=header_timestamp,
            nonce=header_nonce,
            signature_base64=header_signature,
            signature_algorithm=header_algorithm,
            platform=payload.client_platform,
            app_version=payload.app_version,
        )
    except DeviceAuthError as error:
        return _auth_error(error)


@app.get('/api/device/profile')
@app.get('/profile')
def device_profile(request: Request):
    try:
        return authenticate_registered_device(
            raw_code=_bearer_code(request),
            method='GET',
            path=request.url.path,
            device_id=_header(request, 'x-emery-device-id'),
            timestamp=_header(request, 'x-emery-timestamp'),
            nonce=_header(request, 'x-emery-nonce'),
            signature_base64=_header(request, 'x-emery-signature'),
            signature_algorithm=_header(request, 'x-emery-signature-algorithm'),
        )
    except DeviceAuthError as error:
        return _auth_error(error)


@app.post('/api/vpn/session/acquire')
def vpn_session_acquire(payload: VpnSessionAcquireRequest, request: Request):
    try:
        raw_code = _bearer_code(request)
        if payload.access_key.strip() != raw_code:
            raise DeviceAuthError('device_proof_mismatch', 401)
        device_id = _header(request, 'x-emery-device-id')
        authenticate_registered_device(
            raw_code=raw_code,
            method='POST',
            path=request.url.path,
            device_id=device_id,
            timestamp=_header(request, 'x-emery-timestamp'),
            nonce=_header(request, 'x-emery-nonce'),
            signature_base64=_header(request, 'x-emery-signature'),
            signature_algorithm=_header(request, 'x-emery-signature-algorithm'),
        )
        lease = acquire_vpn_session(raw_code, device_id)
    except DeviceAuthError as error:
        return _auth_error(error)

    if pool_bridge_enabled():
        for stale in lease.get('stale_assignments', []):
            assignment_id = int(stale.get('assignment_id') or 0)
            stale_device_id = str(stale.get('device_id') or '')
            if assignment_id <= 0 or not stale_device_id:
                continue
            try:
                release_assignment(assignment_id)
                clear_device_pool_assignment(raw_code, stale_device_id)
            except PoolBridgeError:
                pass
        try:
            assignment = refresh_stored_assignment(
                raw_code,
                device_id,
                node_id=payload.server_id,
            )
            apply_stored_assignment_policy(
                raw_code,
                device_id,
                payload.traffic_policy,
            )
        except PoolBridgeError as error:
            try:
                release_vpn_session(raw_code, device_id, str(lease.get('session_id') or ''))
            except DeviceAuthError:
                pass
            return JSONResponse(
                status_code=error.status_code,
                content={'ok': False, 'reason': error.reason, 'error': error.reason},
            )
        server = _pool_assignment_server(assignment)
    else:
        snapshot = get_server_snapshot()
        server = snapshot['server']
        if not server:
            try:
                release_vpn_session(raw_code, device_id, str(lease.get('session_id') or ''))
            except DeviceAuthError:
                pass
            return JSONResponse(status_code=503, content={'ok': False, 'reason': 'no_server'})

    return {
        'ok': True,
        'server_id': int(server['id']),
        'city': str(server['name']),
        'import_text': str(server['config']),
        'session_id': lease['session_id'],
        'session_expires_at_epoch': lease['expires_at_epoch'],
        'active_connections': lease['active_connections'],
        'connections_limit': lease['connections_limit'],
    }


@app.post('/api/vpn/session/heartbeat')
def vpn_session_heartbeat(payload: VpnSessionControlRequest, request: Request):
    try:
        raw_code = _bearer_code(request)
        device_id = _header(request, 'x-emery-device-id')
        authenticate_registered_device(
            raw_code=raw_code,
            method='POST',
            path=request.url.path,
            device_id=device_id,
            timestamp=_header(request, 'x-emery-timestamp'),
            nonce=_header(request, 'x-emery-nonce'),
            signature_base64=_header(request, 'x-emery-signature'),
            signature_algorithm=_header(request, 'x-emery-signature-algorithm'),
        )
        return heartbeat_vpn_session(raw_code, device_id, payload.session_id)
    except DeviceAuthError as error:
        return _auth_error(error)


@app.post('/api/vpn/session/release')
def vpn_session_release(payload: VpnSessionControlRequest, request: Request):
    try:
        raw_code = _bearer_code(request)
        device_id = _header(request, 'x-emery-device-id')
        authenticate_registered_device(
            raw_code=raw_code,
            method='POST',
            path=request.url.path,
            device_id=device_id,
            timestamp=_header(request, 'x-emery-timestamp'),
            nonce=_header(request, 'x-emery-nonce'),
            signature_base64=_header(request, 'x-emery-signature'),
            signature_algorithm=_header(request, 'x-emery-signature-algorithm'),
        )
        released = release_vpn_session(raw_code, device_id, payload.session_id)
    except DeviceAuthError as error:
        return _auth_error(error)

    assignment_id = int(released.get('assignment_id') or 0)
    cleanup_pending = False
    if pool_bridge_enabled() and assignment_id > 0:
        try:
            release_assignment(assignment_id)
            clear_device_pool_assignment(raw_code, device_id)
        except PoolBridgeError:
            cleanup_pending = True
    return {'ok': True, 'cleanup_pending': cleanup_pending}


@app.post('/api/activate/validate')
def validate_activation(payload: ActivationRequest, request: Request):
    if upgrade_required(payload.appVersionCode):
        return upgrade_required_response()

    if rate_limited(client_key(request, payload)):
        return JSONResponse(status_code=429, content={'ok': False, 'reason': 'too_many_attempts'})

    try:
        header_device_id = _header(request, 'x-emery-device-id')
        header_timestamp = _header(request, 'x-emery-timestamp')
        header_nonce = _header(request, 'x-emery-nonce')
        header_signature = _header(request, 'x-emery-signature')
        header_algorithm = _header(request, 'x-emery-signature-algorithm')
        _require_header_match(payload.deviceId, header_device_id)
        _require_header_match(payload.timestamp, header_timestamp)
        _require_header_match(payload.nonce, header_nonce)
        _require_header_match(payload.signature, header_signature)
        _require_header_match(payload.signature_algorithm, header_algorithm)

        validation = validate_device_registration(
            raw_code=payload.code,
            path=request.url.path,
            device_id=header_device_id,
            device_name=payload.deviceName,
            public_key_base64=payload.client_public_key,
            timestamp=header_timestamp,
            nonce=header_nonce,
            signature_base64=header_signature,
            signature_algorithm=header_algorithm,
        )
    except DeviceAuthError as error:
        return _auth_error(error)

    return {
        'ok': True,
        'code': validation.get('code') or payload.code.strip(),
        'alreadyRegistered': validation.get('already_registered', False),
        'plan': validation.get('plan_code'),
        'planTitle': validation.get('plan_name'),
        'usedDevices': validation.get('devices_used'),
        'maxDevices': validation.get('devices_limit'),
        'expiresAt': validation.get('expires_at'),
    }


@app.post('/api/activate')
def activate(payload: ActivationRequest, request: Request):
    if upgrade_required(payload.appVersionCode):
        return upgrade_required_response()

    if rate_limited(client_key(request, payload)):
        return JSONResponse(status_code=429, content={'ok': False, 'reason': 'too_many_attempts'})

    try:
        header_device_id = _header(request, 'x-emery-device-id')
        header_timestamp = _header(request, 'x-emery-timestamp')
        header_nonce = _header(request, 'x-emery-nonce')
        header_signature = _header(request, 'x-emery-signature')
        header_algorithm = _header(request, 'x-emery-signature-algorithm')
        _require_header_match(payload.deviceId, header_device_id)
        _require_header_match(payload.timestamp, header_timestamp)
        _require_header_match(payload.nonce, header_nonce)
        _require_header_match(payload.signature, header_signature)
        _require_header_match(payload.signature_algorithm, header_algorithm)

        access = register_device(
            raw_code=payload.code,
            path=request.url.path,
            device_id=header_device_id,
            device_name=payload.deviceName,
            public_key_base64=payload.client_public_key,
            timestamp=header_timestamp,
            nonce=header_nonce,
            signature_base64=header_signature,
            signature_algorithm=header_algorithm,
            platform='android',
            app_version=str(payload.appVersionCode),
        )
    except DeviceAuthError as error:
        return _auth_error(error)

    return {
        'ok': True,
        'code': payload.code.strip(),
        'revision': -1,
        'serverId': -1,
        'serverName': '',
        'region': '',
        'config': '',
        'plan': access.get('plan_code'),
        'planTitle': access.get('plan_name'),
        'usedDevices': access.get('devices_used'),
        'maxDevices': access.get('devices_limit'),
        'expiresAt': access.get('expires_at'),
        'devices': access.get('devices'),
    }


@app.post('/api/policy')
async def apply_regional_policy(payload: RegionalPolicyRequest, request: Request):
    if upgrade_required(payload.appVersionCode):
        return upgrade_required_response()

    try:
        header_device_id = _header(request, 'x-emery-device-id')
        _require_header_match(payload.deviceId, header_device_id)
        authenticate_registered_device(
            raw_code=payload.code,
            method='POST',
            path=request.url.path,
            device_id=header_device_id,
            timestamp=_header(request, 'x-emery-timestamp'),
            nonce=_header(request, 'x-emery-nonce'),
            signature_base64=_header(request, 'x-emery-signature'),
            signature_algorithm=_header(request, 'x-emery-signature-algorithm'),
        )
    except DeviceAuthError as error:
        return _auth_error(error)

    if not pool_bridge_enabled():
        return JSONResponse(
            status_code=409,
            content={'ok': False, 'reason': 'regional_policy_unavailable'},
        )

    try:
        await asyncio.to_thread(
            apply_stored_assignment_policy,
            payload.code,
            header_device_id,
            payload.trafficPolicy,
        )
    except PoolBridgeError as error:
        return JSONResponse(
            status_code=error.status_code,
            content={'ok': False, 'reason': error.reason},
        )

    return {
        'ok': True,
        'trafficPolicy': payload.trafficPolicy,
    }


@app.post('/api/config/sync')
async def sync_config(payload: ConfigSyncRequest, request: Request):
    if upgrade_required(payload.appVersionCode):
        return upgrade_required_response()

    try:
        header_device_id = _header(request, 'x-emery-device-id')
        _require_header_match(payload.deviceId, header_device_id)
        authenticate_registered_device(
            raw_code=payload.code,
            method='POST',
            path=request.url.path,
            device_id=header_device_id,
            timestamp=_header(request, 'x-emery-timestamp'),
            nonce=_header(request, 'x-emery-nonce'),
            signature_base64=_header(request, 'x-emery-signature'),
            signature_algorithm=_header(request, 'x-emery-signature-algorithm'),
        )
    except DeviceAuthError as error:
        return _auth_error(error)

    if pool_bridge_enabled():
        if not is_vpn_session_active(payload.code, header_device_id):
            return {
                'ok': True,
                'changed': False,
                'revision': payload.revision,
                'reason': 'session_inactive',
                'server': None,
            }
        try:
            assignment = await asyncio.to_thread(
                refresh_stored_assignment,
                payload.code,
                header_device_id,
            )
        except PoolBridgeError as error:
            return JSONResponse(
                status_code=error.status_code,
                content={'ok': False, 'reason': error.reason},
            )
        revision = int(assignment.get('pool_config_revision') or 0)
        return {
            'ok': True,
            'changed': revision != payload.revision,
            'revision': revision,
            'server': _pool_assignment_server(assignment),
        }

    deadline = time.monotonic() + CONFIG_SYNC_WAIT_SECONDS
    snapshot = get_server_snapshot()
    while snapshot['revision'] == payload.revision and time.monotonic() < deadline:
        await asyncio.sleep(CONFIG_SYNC_POLL_INTERVAL_SECONDS)
        snapshot = get_server_snapshot()

    return {
        'ok': True,
        'changed': snapshot['revision'] != payload.revision,
        'revision': snapshot['revision'],
        'server': snapshot['server'],
    }

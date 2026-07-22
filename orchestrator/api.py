import asyncio
import hashlib
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Deque, Dict

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from checkout_routes import router as checkout_router
from config import (
    ADMIN_IDS,
    APP_UPDATE_MESSAGE,
    BOT_TOKEN,
    DEFAULT_SERVER_CONFIG,
    DEFAULT_SERVER_NAME,
    DEFAULT_SERVER_REGION,
    MIN_SUPPORTED_APP_VERSION_CODE,
)
from device_auth import (
    DeviceAuthError,
    authenticate_registered_device,
    ensure_device_auth_storage,
    register_device,
)
from storage import get_server_snapshot, init_storage, save_server


app = FastAPI(title='Skryon Orchestrator API')
app.include_router(checkout_router)

RATE_LIMIT_WINDOW_SECONDS = 300
RATE_LIMIT_MAX_ATTEMPTS = 12
CONFIG_SYNC_WAIT_SECONDS = 25.0
CONFIG_SYNC_POLL_INTERVAL_SECONDS = 0.5
CLIENT_ERROR_WINDOW_SECONDS = 600
CLIENT_ERROR_MAX_PER_SIGNATURE = 2
_attempts: Dict[str, Deque[float]] = defaultdict(deque)
_client_error_attempts: Dict[str, Deque[float]] = defaultdict(deque)


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


class ClientErrorReportRequest(BaseModel):
    kind: str = Field(default='handled', min_length=1, max_length=16)
    stage: str = Field(default='unknown_stage', min_length=1, max_length=80)
    code: str = Field(default='unknown_error', min_length=1, max_length=120)
    app_version: str = Field(default='', max_length=32)
    app_version_code: int = Field(default=0, ge=0)
    android_api: int = Field(default=0, ge=0, le=1000)
    stack: list[str] = Field(default_factory=list, max_length=8)


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
    return ip + ':' + device


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


def _safe_report_token(value: str, fallback: str, limit: int) -> str:
    normalized = ''.join(
        char if (char.isalnum() or char in '._:/-') else '_'
        for char in str(value or '').strip().lower()
    )
    while '__' in normalized:
        normalized = normalized.replace('__', '_')
    normalized = normalized.strip('_')[:limit]
    return normalized or fallback


def _safe_stack_line(value: str) -> str:
    return ''.join(
        char for char in str(value or '')
        if char.isalnum() or char in '._:/-()$'
    )[:180]


def _normalized_client_error(payload: ClientErrorReportRequest) -> dict:
    kind = _safe_report_token(payload.kind, 'handled', 16)
    if kind not in {'handled', 'crash'}:
        kind = 'handled'
    return {
        'kind': kind,
        'stage': _safe_report_token(payload.stage, 'unknown_stage', 80),
        'code': _safe_report_token(payload.code, 'unknown_error', 120),
        'app_version': _safe_report_token(payload.app_version, 'unknown', 32),
        'app_version_code': max(0, int(payload.app_version_code)),
        'android_api': max(0, int(payload.android_api)),
        'stack': [
            safe
            for safe in (_safe_stack_line(line) for line in payload.stack[:8])
            if safe
        ],
    }


def _client_error_rate_limited(report: dict) -> bool:
    raw_signature = '|'.join(
        (
            report['kind'],
            report['stage'],
            report['code'],
            str(report['app_version_code']),
        )
    )
    signature = hashlib.sha256(raw_signature.encode('utf-8')).hexdigest()
    now = time.time()
    bucket = _client_error_attempts[signature]
    while bucket and now - bucket[0] > CLIENT_ERROR_WINDOW_SECONDS:
        bucket.popleft()
    if len(bucket) >= CLIENT_ERROR_MAX_PER_SIGNATURE:
        return True
    bucket.append(now)
    return False


def _client_error_message(report: dict) -> str:
    title = '💥 Сбой приложения' if report['kind'] == 'crash' else '⚠️ Ошибка приложения'
    lines = [
        title,
        '',
        'Этап: ' + report['stage'],
        'Код: ' + report['code'],
        'Версия: ' + report['app_version'] + ' (' + str(report['app_version_code']) + ')',
        'Android API: ' + str(report['android_api']),
        'Время UTC: ' + datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'),
    ]
    if report['stack']:
        lines.extend(('', 'Стек:', *report['stack']))
    lines.extend(
        (
            '',
            'Без кода доступа, IP, идентификатора устройства, модели и VPN-конфигурации.',
        )
    )
    return '\n'.join(lines)[:3900]


async def _send_client_error_to_admins(report: dict) -> int:
    if not BOT_TOKEN or not ADMIN_IDS:
        return 0
    endpoint = f'https://api.telegram.org/bot{BOT_TOKEN}/sendMessage'
    message = _client_error_message(report)
    delivered = 0
    async with httpx.AsyncClient(timeout=8.0) as client:
        for admin_id in ADMIN_IDS:
            try:
                response = await client.post(
                    endpoint,
                    json={
                        'chat_id': admin_id,
                        'text': message,
                        'disable_web_page_preview': True,
                    },
                )
                if response.is_success:
                    delivered += 1
            except httpx.HTTPError:
                continue
    return delivered


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


@app.post('/api/client/error')
async def client_error_report(payload: ClientErrorReportRequest, request: Request):
    try:
        authenticate_registered_device(
            raw_code=_bearer_code(request),
            method='POST',
            path=request.url.path,
            device_id=_header(request, 'x-emery-device-id'),
            timestamp=_header(request, 'x-emery-timestamp'),
            nonce=_header(request, 'x-emery-nonce'),
            signature_base64=_header(request, 'x-emery-signature'),
            signature_algorithm=_header(request, 'x-emery-signature-algorithm'),
        )
    except DeviceAuthError as error:
        return _auth_error(error)

    report = _normalized_client_error(payload)
    if _client_error_rate_limited(report):
        return {'ok': True, 'delivered': 0, 'deduplicated': True}

    delivered = await _send_client_error_to_admins(report)
    return {
        'ok': True,
        'delivered': delivered,
        'configured': bool(BOT_TOKEN and ADMIN_IDS),
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

    snapshot = get_server_snapshot()
    server = snapshot['server']
    if not server:
        return {'ok': False, 'reason': 'no_server'}

    return {
        'ok': True,
        'code': payload.code.strip(),
        'revision': snapshot['revision'],
        'serverId': server['id'],
        'serverName': server['name'],
        'region': server['region'],
        'config': server['config'],
        'plan': access.get('plan_code'),
        'planTitle': access.get('plan_name'),
        'usedDevices': access.get('devices_used'),
        'maxDevices': access.get('devices_limit'),
        'expiresAt': access.get('expires_at'),
        'devices': access.get('devices'),
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

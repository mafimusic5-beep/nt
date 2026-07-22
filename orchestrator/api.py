import asyncio
import hashlib
import logging
import time
from collections import defaultdict, deque
from typing import Deque, Dict

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from checkout_routes import router as checkout_router
from config import (
    APP_UPDATE_MESSAGE,
    DEFAULT_SERVER_CONFIG,
    DEFAULT_SERVER_NAME,
    DEFAULT_SERVER_REGION,
    MIN_SUPPORTED_APP_VERSION_CODE,
)
from storage import check_activation_access, get_server_snapshot, init_storage, save_server, validate_activation_code

# Never write remote addresses through Uvicorn's access logger.
logging.getLogger("uvicorn.access").disabled = True
logging.getLogger("uvicorn.access").propagate = False

app = FastAPI(title='Skryon Orchestrator API')
app.include_router(checkout_router)

RATE_LIMIT_WINDOW_SECONDS = 300
RATE_LIMIT_MAX_ATTEMPTS = 12
CONFIG_SYNC_WAIT_SECONDS = 25.0
CONFIG_SYNC_POLL_INTERVAL_SECONDS = 0.5
_attempts: Dict[str, Deque[float]] = defaultdict(deque)


class ActivationRequest(BaseModel):
    code: str = Field(min_length=1, max_length=32)
    deviceId: str = Field(min_length=4, max_length=128)
    appVersionCode: int = Field(default=0, ge=0)


class ConfigSyncRequest(BaseModel):
    code: str = Field(min_length=1, max_length=32)
    deviceId: str = Field(min_length=4, max_length=128)
    revision: int = Field(default=-1, ge=-1)
    appVersionCode: int = Field(default=0, ge=0)


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


def client_key(payload: ActivationRequest) -> str:
    """Return a short-lived rate-limit key without reading or retaining an IP address."""
    material = f"{payload.code.strip().upper()}:{payload.deviceId.strip()}"
    return hashlib.sha256(material.encode('utf-8')).hexdigest()


def rate_limited(key: str) -> bool:
    now = time.time()
    bucket = _attempts[key]
    while bucket and now - bucket[0] > RATE_LIMIT_WINDOW_SECONDS:
        bucket.popleft()
    if len(bucket) >= RATE_LIMIT_MAX_ATTEMPTS:
        return True
    bucket.append(now)
    return False


@app.middleware('http')
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['Cache-Control'] = 'no-store'
    return response


@app.on_event('startup')
def on_startup() -> None:
    init_storage()
    if DEFAULT_SERVER_CONFIG and not get_server_snapshot()['server']:
        save_server(DEFAULT_SERVER_NAME, DEFAULT_SERVER_REGION, DEFAULT_SERVER_CONFIG)


@app.get('/health')
def health() -> dict:
    return {'ok': True}


@app.post('/api/activate')
def activate(payload: ActivationRequest) -> dict:
    code = payload.code.strip()
    device_id = payload.deviceId.strip()

    if upgrade_required(payload.appVersionCode):
        return upgrade_required_response()

    if rate_limited(client_key(payload)):
        return JSONResponse(status_code=429, content={'ok': False, 'reason': 'too_many_attempts'})

    result = validate_activation_code(code, device_id)
    if not result.get('ok'):
        return result

    snapshot = get_server_snapshot()
    server = snapshot['server']
    if not server:
        return {'ok': False, 'reason': 'no_server'}

    return {
        'ok': True,
        'code': result['code'],
        'revision': snapshot['revision'],
        'serverId': server['id'],
        'serverName': server['name'],
        'region': server['region'],
        'config': server['config'],
        'plan': result.get('plan'),
        'usedDevices': result.get('usedDevices'),
        'maxDevices': result.get('maxDevices'),
    }


@app.post('/api/config/sync')
async def sync_config(payload: ConfigSyncRequest) -> dict:
    if upgrade_required(payload.appVersionCode):
        return upgrade_required_response()

    access = check_activation_access(payload.code, payload.deviceId)
    if not access.get('ok'):
        return access

    deadline = time.monotonic() + CONFIG_SYNC_WAIT_SECONDS
    snapshot = get_server_snapshot()
    while snapshot['revision'] == payload.revision and time.monotonic() < deadline:
        await asyncio.sleep(CONFIG_SYNC_POLL_INTERVAL_SECONDS)
        snapshot = get_server_snapshot()

    server = snapshot['server']
    return {
        'ok': True,
        'changed': snapshot['revision'] != payload.revision,
        'revision': snapshot['revision'],
        'server': server,
    }

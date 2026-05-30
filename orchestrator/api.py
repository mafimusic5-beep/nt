import time
from collections import defaultdict, deque
from typing import Deque, Dict

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from config import DEFAULT_SERVER_CONFIG, DEFAULT_SERVER_NAME, DEFAULT_SERVER_REGION
from storage import get_active_server, init_storage, save_server, validate_activation_code

app = FastAPI(title='Skryon Orchestrator API')

RATE_LIMIT_WINDOW_SECONDS = 300
RATE_LIMIT_MAX_ATTEMPTS = 12
_attempts: Dict[str, Deque[float]] = defaultdict(deque)


class ActivationRequest(BaseModel):
    code: str = Field(min_length=1, max_length=32)
    deviceId: str = Field(min_length=4, max_length=128)


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
    if DEFAULT_SERVER_CONFIG:
        save_server(DEFAULT_SERVER_NAME, DEFAULT_SERVER_REGION, DEFAULT_SERVER_CONFIG)


@app.get('/health')
def health() -> dict:
    return {'ok': True}


@app.post('/api/activate')
def activate(payload: ActivationRequest, request: Request) -> dict:
    code = payload.code.strip()
    device_id = payload.deviceId.strip()

    if rate_limited(client_key(request, payload)):
        return JSONResponse(status_code=429, content={'ok': False, 'reason': 'too_many_attempts'})

    result = validate_activation_code(code, device_id)
    if not result.get('ok'):
        return result

    server = get_active_server()
    if not server:
        return {'ok': False, 'reason': 'no_server'}

    return {
        'ok': True,
        'code': result['code'],
        'serverName': server['name'],
        'region': server['region'],
        'config': server['config'],
    }

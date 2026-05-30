from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from config import API_SECRET, DEFAULT_SERVER_CONFIG, DEFAULT_SERVER_NAME, DEFAULT_SERVER_REGION
from storage import get_active_server, init_storage, save_server, validate_activation_code

app = FastAPI(title='Skryon Orchestrator API')


class ActivationRequest(BaseModel):
    code: str
    deviceId: str


def require_secret(value: str) -> None:
    if API_SECRET and value != API_SECRET:
        raise HTTPException(status_code=401, detail='bad_secret')


@app.on_event('startup')
def on_startup() -> None:
    init_storage()
    if DEFAULT_SERVER_CONFIG:
        save_server(DEFAULT_SERVER_NAME, DEFAULT_SERVER_REGION, DEFAULT_SERVER_CONFIG)


@app.get('/health')
def health() -> dict:
    return {'ok': True}


@app.post('/api/activate')
def activate(payload: ActivationRequest, x_api_secret: str = Header(default='')) -> dict:
    require_secret(x_api_secret)
    result = validate_activation_code(payload.code, payload.deviceId)
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

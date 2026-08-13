import os
from pathlib import Path
from typing import List

from dotenv import dotenv_values


def _emery_env_paths() -> tuple[Path, ...]:
    configured = os.getenv('EMERY_ENV_FILE', '').strip()
    repository_env = Path(__file__).resolve().parent.parent / 'emery vpn orchestrator' / '.env'
    paths = [Path(configured)] if configured else []
    paths.extend((repository_env, Path('/opt/emery-orchestrator/.env')))
    return tuple(paths)


def _shared_emery_value(name: str, paths: tuple[Path, ...] | None = None) -> str:
    candidates = _emery_env_paths() if paths is None else paths
    for path in candidates:
        try:
            value = dotenv_values(path).get(name) if path.is_file() else None
        except OSError:
            continue
        if value:
            return str(value).strip()
    return ''


def _split_admin_ids(raw: str) -> List[int]:
    result: List[int] = []
    for item in raw.split(','):
        item = item.strip()
        if item:
            result.append(int(item))
    return result


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {'1', 'true', 'yes', 'on'}


BOT_TOKEN = os.getenv('BOT_TOKEN', '')
ADMIN_IDS = _split_admin_ids(os.getenv('ADMIN_IDS', ''))
API_HOST = os.getenv('API_HOST', '0.0.0.0')
API_PORT = int(os.getenv('API_PORT', '8080'))
API_SECRET = os.getenv('API_SECRET', '')
CHECKOUT_SECRET = (
    os.getenv('CHECKOUT_SECRET', '').strip()
    or _shared_emery_value('CHECKOUT_SECRET')
)
DATABASE_PATH = os.getenv('DATABASE_PATH', 'skryon.db')
DEFAULT_SERVER_NAME = os.getenv('DEFAULT_SERVER_NAME', 'Secure-DE')
DEFAULT_SERVER_REGION = os.getenv('DEFAULT_SERVER_REGION', 'DE')
DEFAULT_SERVER_CONFIG = os.getenv('DEFAULT_SERVER_CONFIG', '')
SERVER_POOL_SYNC_URL = (
    os.getenv('SERVER_POOL_SYNC_URL', '').strip()
    or os.getenv('BACKEND_BASE_URL', '').strip()
    or _shared_emery_value('BACKEND_BASE_URL')
    or 'http://127.0.0.1:9330'
).rstrip('/')
SERVER_POOL_SYNC_ADMIN_KEY = (
    os.getenv('SERVER_POOL_SYNC_ADMIN_KEY', '').strip()
    or os.getenv('ADMIN_API_KEY', '').strip()
    or _shared_emery_value('ADMIN_API_KEY')
)
POOL_BRIDGE_URL = (
    os.getenv('POOL_BRIDGE_URL', '').strip()
    or SERVER_POOL_SYNC_URL
).rstrip('/')
POOL_BRIDGE_API_KEY = (
    os.getenv('POOL_BRIDGE_API_KEY', '').strip()
    or _shared_emery_value('POOL_BRIDGE_API_KEY')
)
POOL_BRIDGE_PSEUDONYM_KEY = (
    os.getenv('POOL_BRIDGE_PSEUDONYM_KEY', '').strip()
    or POOL_BRIDGE_API_KEY
)
POOL_BRIDGE_ENABLED = _env_bool(
    'POOL_BRIDGE_ENABLED',
    bool(POOL_BRIDGE_URL and POOL_BRIDGE_API_KEY and POOL_BRIDGE_PSEUDONYM_KEY),
)
POOL_BRIDGE_REGION_CODE = os.getenv('POOL_BRIDGE_REGION_CODE', 'auto').strip().lower() or 'auto'
POOL_BRIDGE_TIMEOUT_SECONDS = max(float(os.getenv('POOL_BRIDGE_TIMEOUT_SECONDS', '100')), 10.0)

# Builds before 717 do not implement the signed device protocol and must be upgraded.
SIGNED_DEVICE_PROTOCOL_VERSION_CODE = 717
MIN_SUPPORTED_APP_VERSION_CODE = max(
    int(os.getenv('MIN_SUPPORTED_APP_VERSION_CODE', str(SIGNED_DEVICE_PROTOCOL_VERSION_CODE))),
    SIGNED_DEVICE_PROTOCOL_VERSION_CODE,
)
APP_UPDATE_MESSAGE = os.getenv(
    'APP_UPDATE_MESSAGE',
    'Версия приложения устарела. Обновите приложение.',
).strip()


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

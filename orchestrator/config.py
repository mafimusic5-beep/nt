import os
from typing import List


def _split_admin_ids(raw: str) -> List[int]:
    result: List[int] = []
    for item in raw.split(','):
        item = item.strip()
        if item:
            result.append(int(item))
    return result


BOT_TOKEN = os.getenv('BOT_TOKEN', '')
ADMIN_IDS = _split_admin_ids(os.getenv('ADMIN_IDS', ''))
API_HOST = os.getenv('API_HOST', '0.0.0.0')
API_PORT = int(os.getenv('API_PORT', '8080'))
API_SECRET = os.getenv('API_SECRET', '')
CHECKOUT_SECRET = os.getenv('CHECKOUT_SECRET', '')
DATABASE_PATH = os.getenv('DATABASE_PATH', 'skryon.db')
DEFAULT_SERVER_NAME = os.getenv('DEFAULT_SERVER_NAME', 'Secure-DE')
DEFAULT_SERVER_REGION = os.getenv('DEFAULT_SERVER_REGION', 'DE')
DEFAULT_SERVER_CONFIG = os.getenv('DEFAULT_SERVER_CONFIG', '')
SERVER_POOL_SYNC_URL = os.getenv(
    'SERVER_POOL_SYNC_URL',
    os.getenv('BACKEND_BASE_URL', 'http://127.0.0.1:9330'),
).rstrip('/')
SERVER_POOL_SYNC_ADMIN_KEY = os.getenv(
    'SERVER_POOL_SYNC_ADMIN_KEY',
    os.getenv('ADMIN_API_KEY', ''),
)


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

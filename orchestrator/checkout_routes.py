from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse
from pathlib import Path
from pydantic import BaseModel, Field
import time
from collections import defaultdict, deque
from typing import Deque, Dict

from storage import create_checkout_code, get_checkout_order

router = APIRouter()
WEB_DIR = Path(__file__).resolve().parent / 'web'
PLANS = {
    'personal': {'title': 'Личный', 'devices': 1, 'days': 30},
    'personal_plus': {'title': 'Личный+', 'devices': 2, 'days': 30},
    'family': {'title': 'Семейный', 'devices': 5, 'days': 30},
}
WINDOW = 300
MAX_ATTEMPTS = 12
_attempts: Dict[str, Deque[float]] = defaultdict(deque)


class CheckoutRequest(BaseModel):
    plan: str = Field(min_length=1, max_length=32)
    customer: str = Field(default='', max_length=128)


def limited(key: str) -> bool:
    now = time.time()
    bucket = _attempts[key]
    while bucket and now - bucket[0] > WINDOW:
        bucket.popleft()
    if len(bucket) >= MAX_ATTEMPTS:
        return True
    bucket.append(now)
    return False


def remote_ip(request: Request) -> str:
    forwarded = request.headers.get('x-forwarded-for', '')
    return forwarded.split(',')[0].strip() if forwarded else (request.client.host if request.client else 'unknown')


@router.get('/checkout')
def page_checkout():
    return FileResponse(WEB_DIR / 'checkout.html')


@router.get('/checkout/code')
def page_code():
    return FileResponse(WEB_DIR / 'code.html')


@router.get('/api/checkout/plans')
def plans():
    return {'ok': True, 'plans': PLANS}


@router.post('/api/checkout/get-code')
def get_code(payload: CheckoutRequest, request: Request):
    if limited('checkout:' + remote_ip(request)):
        return JSONResponse(status_code=429, content={'ok': False, 'reason': 'too_many_attempts'})
    if payload.plan not in PLANS:
        return JSONResponse(status_code=400, content={'ok': False, 'reason': 'bad_plan'})
    selected = PLANS[payload.plan]
    order = create_checkout_code(payload.plan, selected['devices'], selected['days'], payload.customer.strip())
    return {
        'ok': True,
        'orderId': order['external_id'],
        'code': order['code'],
        'plan': payload.plan,
        'planTitle': selected['title'],
        'maxDevices': order['max_devices'],
        'expiresAt': order['expires_at'],
        'redirectUrl': '/checkout/code?order=' + order['external_id'],
    }


@router.get('/api/checkout/order/{order_id}')
def order(order_id: str):
    row = get_checkout_order(order_id)
    if not row:
        return {'ok': False, 'reason': 'not_found'}
    return {
        'ok': True,
        'orderId': row['external_id'],
        'code': row['code'],
        'plan': row['plan'],
        'maxDevices': row['max_devices'],
        'expiresAt': row['expires_at'],
        'status': row['status'],
    }

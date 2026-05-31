from fastapi import APIRouter, Header, Request
from fastapi.responses import FileResponse, JSONResponse
from pathlib import Path
from pydantic import BaseModel, Field
import time
from collections import defaultdict, deque
from typing import Deque, Dict, Optional

from config import CHECKOUT_SECRET
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


class CheckoutCallbackRequest(BaseModel):
    externalId: Optional[str] = Field(default=None, max_length=128)
    plan: str = Field(min_length=1, max_length=32)
    customer: str = Field(default='', max_length=128)
    status: str = Field(default='paid', max_length=32)


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


def plan_or_error(plan: str):
    if plan not in PLANS:
        return None
    return PLANS[plan]


def issue_code(plan: str, customer: str, external_id: Optional[str] = None) -> dict:
    selected = PLANS[plan]
    order = create_checkout_code(plan, selected['devices'], selected['days'], customer.strip(), external_id)
    return {
        'ok': True,
        'orderId': order['external_id'],
        'code': order['code'],
        'plan': plan,
        'planTitle': selected['title'],
        'maxDevices': order['max_devices'],
        'expiresAt': order['expires_at'],
        'redirectUrl': '/checkout/code?order=' + order['external_id'],
    }


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
    if not plan_or_error(payload.plan):
        return JSONResponse(status_code=400, content={'ok': False, 'reason': 'bad_plan'})
    return issue_code(payload.plan, payload.customer)


@router.post('/api/checkout/callback')
def callback(payload: CheckoutCallbackRequest, x_checkout_secret: str = Header(default='')):
    if not CHECKOUT_SECRET or x_checkout_secret != CHECKOUT_SECRET:
        return JSONResponse(status_code=401, content={'ok': False, 'reason': 'bad_secret'})
    if payload.status != 'paid':
        return JSONResponse(status_code=400, content={'ok': False, 'reason': 'not_paid'})
    if not plan_or_error(payload.plan):
        return JSONResponse(status_code=400, content={'ok': False, 'reason': 'bad_plan'})
    return issue_code(payload.plan, payload.customer, payload.externalId)


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

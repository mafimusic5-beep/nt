import hashlib
import hmac
import logging
import secrets
import time
from collections import defaultdict, deque
from typing import Deque, Dict

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from yookassa_checkout import (
    PaymentConfigurationError,
    PaymentProviderError,
    PaymentVerificationError,
    create_payment_order,
    create_yookassa_payment,
    get_order_by_payment_id,
    get_payment_order,
    mark_payment_failed,
    payment_ready,
    public_order,
    sync_and_fulfill,
    validate_email,
    validate_months,
    validate_plan,
    webhook_source_allowed,
)

logger = logging.getLogger(__name__)
router = APIRouter()

WINDOW_SECONDS = 600
MAX_CREATE_ATTEMPTS = 10
_RATE_KEY = secrets.token_bytes(32)
_attempts: Dict[str, Deque[float]] = defaultdict(deque)


class CreatePaymentRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    plan: str = Field(min_length=1, max_length=32)
    months: int = Field(ge=1, le=12)
    personalDataConsent: bool = False


def _source_ip(request: Request) -> str:
    cloudflare = request.headers.get("cf-connecting-ip", "").strip()
    if cloudflare:
        return cloudflare
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",", 1)[0].strip()
    return request.client.host if request.client else "unknown"


def _rate_key(request: Request) -> str:
    return hmac.new(
        _RATE_KEY,
        _source_ip(request).encode("utf-8", errors="ignore"),
        hashlib.sha256,
    ).hexdigest()


def _limited(request: Request) -> bool:
    key = _rate_key(request)
    now = time.time()
    bucket = _attempts[key]
    while bucket and now - bucket[0] > WINDOW_SECONDS:
        bucket.popleft()
    if len(bucket) >= MAX_CREATE_ATTEMPTS:
        return True
    bucket.append(now)
    return False


@router.get("/api/checkout/payment-ready")
def payment_configuration_status() -> dict:
    return {"ok": True, "ready": payment_ready()}


@router.post("/api/checkout/payment")
def create_payment(payload: CreatePaymentRequest, request: Request):
    if _limited(request):
        return JSONResponse(
            status_code=429,
            content={"ok": False, "reason": "too_many_attempts"},
        )
    if payload.personalDataConsent is not True:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "reason": "personal_data_consent_required"},
        )
    try:
        email = validate_email(payload.email)
        validate_plan(payload.plan)
        months = validate_months(payload.months)
    except (TypeError, ValueError) as exc:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "reason": str(exc)},
        )
    if not payment_ready():
        return JSONResponse(
            status_code=503,
            content={"ok": False, "reason": "payment_not_configured"},
        )

    order = create_payment_order(email, payload.plan, months)
    try:
        provider = create_yookassa_payment(order)
    except PaymentConfigurationError as exc:
        mark_payment_failed(str(order["order_id"]), str(exc))
        return JSONResponse(
            status_code=503,
            content={"ok": False, "reason": "payment_not_configured"},
        )
    except PaymentProviderError as exc:
        mark_payment_failed(str(order["order_id"]), str(exc))
        return JSONResponse(
            status_code=502,
            content={"ok": False, "reason": "payment_provider_unavailable"},
        )

    return {
        "ok": True,
        "orderId": str(order["order_id"]),
        "confirmationUrl": provider["confirmation_url"],
    }


@router.get("/api/checkout/payment/{order_id}")
def payment_status(order_id: str):
    if len(order_id) < 20 or len(order_id) > 96:
        return JSONResponse(
            status_code=404,
            content={"ok": False, "reason": "not_found"},
        )
    order = get_payment_order(order_id)
    if not order:
        return JSONResponse(
            status_code=404,
            content={"ok": False, "reason": "not_found"},
        )
    if order.get("payment_id"):
        try:
            order = sync_and_fulfill(order_id)
        except (PaymentProviderError, PaymentConfigurationError):
            logger.warning("Could not refresh YooKassa order %s", order_id)
        except PaymentVerificationError:
            logger.exception("YooKassa verification failed for order %s", order_id)
            return JSONResponse(
                status_code=409,
                content={"ok": False, "reason": "payment_verification_failed"},
            )
    return public_order(order)


@router.post("/api/checkout/yookassa-webhook")
async def yookassa_webhook(request: Request):
    source_ip = _source_ip(request)
    if not webhook_source_allowed(source_ip):
        logger.warning("Rejected YooKassa webhook source %s", source_ip)
        return JSONResponse(
            status_code=403,
            content={"ok": False, "reason": "forbidden_source"},
        )
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "reason": "invalid_json"},
        )

    event = str(payload.get("event") or "")
    if event not in {"payment.succeeded", "payment.canceled"}:
        return {"ok": True, "ignored": True}
    payment_id = str((payload.get("object") or {}).get("id") or "").strip()
    if not payment_id:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "reason": "payment_id_missing"},
        )
    order = get_order_by_payment_id(payment_id)
    if not order:
        return {"ok": True, "ignored": True}

    try:
        sync_and_fulfill(str(order["order_id"]))
    except (PaymentProviderError, PaymentConfigurationError):
        return JSONResponse(
            status_code=503,
            content={"ok": False, "reason": "provider_verification_unavailable"},
        )
    except PaymentVerificationError:
        logger.exception("Rejected unverifiable YooKassa payment %s", payment_id)
        return JSONResponse(
            status_code=409,
            content={"ok": False, "reason": "payment_verification_failed"},
        )

    return {"ok": True}

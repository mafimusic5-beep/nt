import html
import ipaddress
import logging
import os
import re
import secrets
import smtplib
import sqlite3
import ssl
import threading
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formataddr
from typing import Any, Dict, Optional

import httpx
from cryptography.fernet import Fernet, InvalidToken

from config import DATABASE_PATH, _shared_emery_value
from storage import (
    create_checkout_code,
    get_activation_code,
    get_checkout_order,
    renew_activation_code,
)

logger = logging.getLogger(__name__)

SHOP_ID_DEFAULT = "1467477"
RETURN_BASE_DEFAULT = "https://skryon.ru"
CONSENT_VERSION = "2026-09-20-v1"
ALLOWED_MONTHS = (1, 3, 6, 12)
PLAN_CATALOG = {
    "personal": {"title": "Личный", "devices": 1, "monthly_rub": 200},
    "personal_plus": {"title": "Личный+", "devices": 2, "monthly_rub": 269},
    "family": {"title": "Семейный", "devices": 5, "monthly_rub": 500},
}
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_FULFILLMENT_LOCK = threading.Lock()

YOOKASSA_WEBHOOK_NETWORKS = tuple(
    ipaddress.ip_network(value)
    for value in (
        "185.71.76.0/27",
        "185.71.77.0/27",
        "77.75.153.0/25",
        "77.75.156.11/32",
        "77.75.156.35/32",
        "77.75.154.128/25",
        "2a02:5180::/32",
    )
)


class PaymentConfigurationError(RuntimeError):
    pass


class PaymentProviderError(RuntimeError):
    pass


class PaymentVerificationError(RuntimeError):
    pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _setting(name: str, default: str = "") -> str:
    return os.getenv(name, "").strip() or _shared_emery_value(name) or default


def _shop_id() -> str:
    return _setting("YOOKASSA_SHOP_ID", SHOP_ID_DEFAULT)


def _secret_key() -> str:
    return _setting("YOOKASSA_SECRET_KEY")


def _return_base() -> str:
    return _setting("YOOKASSA_RETURN_BASE_URL", RETURN_BASE_DEFAULT).rstrip("/")


def _pii_cipher() -> Fernet:
    key = _setting("PAYMENT_PII_KEY")
    if not key:
        raise PaymentConfigurationError("payment_pii_key_missing")
    return Fernet(key.encode("ascii"))


def _encrypt_email(value: str) -> str:
    raw = (value or "").strip()
    if not raw or raw.startswith("enc1:"):
        return raw
    return "enc1:" + _pii_cipher().encrypt(raw.encode("utf-8")).decode("ascii")


def _decrypt_email(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    if not raw.startswith("enc1:"):
        return raw
    try:
        return _pii_cipher().decrypt(raw[5:].encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError) as exc:
        raise PaymentConfigurationError("payment_pii_decrypt_failed") from exc


def payment_ready() -> bool:
    return bool(
        _shop_id()
        and _secret_key()
        and _setting("SMTP_PASSWORD")
        and _setting("PAYMENT_PII_KEY")
    )


def validate_email(value: str) -> str:
    email = value.strip().lower()
    if not email or len(email) > 254 or not EMAIL_RE.fullmatch(email):
        raise ValueError("invalid_email")
    return email


def validate_plan(plan: str) -> Dict[str, Any]:
    selected = PLAN_CATALOG.get(plan)
    if not selected:
        raise ValueError("invalid_plan")
    return selected


def validate_months(months: int) -> int:
    value = int(months)
    if value not in ALLOWED_MONTHS:
        raise ValueError("invalid_months")
    return value


# SKRYON_RENEW_FULFILLMENT_V1
def resolve_renewal_target(code: str) -> Dict[str, Any]:
    row = get_activation_code(code)
    if not row or str(row.get("status") or "").lower() == "banned":
        raise ValueError("renew_code_invalid")
    plan = str(row.get("plan") or "").strip()
    selected = validate_plan(plan)
    used_devices = int(row.get("used_devices") or 0)
    if used_devices > int(selected["devices"]):
        raise ValueError("renew_code_invalid")
    return {"code": str(row["code"]), "plan": plan, "selected": selected}


def amount_kopeks(plan: str, months: int) -> int:
    selected = validate_plan(plan)
    period = validate_months(months)
    return int(selected["monthly_rub"]) * period * 100


def _connect() -> sqlite3.Connection:
    con = sqlite3.connect(DATABASE_PATH, timeout=15)
    con.row_factory = sqlite3.Row
    return con


def ensure_payment_storage() -> None:
    with _connect() as con:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS payment_orders (
                order_id TEXT PRIMARY KEY,
                payment_id TEXT UNIQUE,
                status TEXT NOT NULL,
                email TEXT NOT NULL,
                plan TEXT NOT NULL,
                months INTEGER NOT NULL,
                amount_kopeks INTEGER NOT NULL,
                mode TEXT NOT NULL DEFAULT 'new',
                target_code TEXT NOT NULL DEFAULT '',
                code TEXT,
                idempotence_key TEXT NOT NULL UNIQUE,
                consent_version TEXT NOT NULL,
                consent_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                paid_at TEXT,
                email_sent_at TEXT,
                email_error TEXT,
                fulfilled_at TEXT
            )
            """
        )
        columns = {
            row[1]
            for row in con.execute("PRAGMA table_info(payment_orders)").fetchall()
        }
        if "fulfilled_at" not in columns:
            con.execute("ALTER TABLE payment_orders ADD COLUMN fulfilled_at TEXT")
        if "mode" not in columns:
            con.execute("ALTER TABLE payment_orders ADD COLUMN mode TEXT NOT NULL DEFAULT 'new'")
        if "target_code" not in columns:
            con.execute("ALTER TABLE payment_orders ADD COLUMN target_code TEXT NOT NULL DEFAULT ''")
        for row in con.execute(
            "SELECT order_id,email FROM payment_orders WHERE email<>''"
        ).fetchall():
            encrypted = _encrypt_email(str(row[1] or ""))
            if encrypted != str(row[1] or ""):
                con.execute(
                    "UPDATE payment_orders SET email=? WHERE order_id=?",
                    (encrypted, row[0]),
                )
        con.execute(
            "UPDATE payment_orders SET email='', code='' "
            "WHERE email_sent_at IS NOT NULL OR status IN ('failed','canceled')"
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_payment_orders_payment_id "
            "ON payment_orders(payment_id)"
        )
        con.commit()


def create_payment_order(
    email: str,
    plan: str,
    months: int,
    mode: str = "new",
    target_code: str = "",
) -> Dict[str, Any]:
    ensure_payment_storage()
    safe_email = validate_email(email)
    period = validate_months(months)
    safe_mode = str(mode or "new").strip().lower()
    if safe_mode not in {"new", "renew"}:
        raise ValueError("invalid_mode")

    safe_target = ""
    if safe_mode == "renew":
        target = resolve_renewal_target(target_code)
        safe_target = str(target["code"])
        safe_plan = str(target["plan"])
    else:
        validate_plan(plan)
        safe_plan = plan

    order_id = secrets.token_urlsafe(24)
    idempotence_key = secrets.token_hex(24)
    now = _now_iso()
    with _connect() as con:
        con.execute(
            """
            INSERT INTO payment_orders(
                order_id, status, email, plan, months, amount_kopeks,
                mode, target_code, idempotence_key,
                consent_version, consent_at, created_at
            ) VALUES (?, 'pending', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                order_id,
                _encrypt_email(safe_email),
                safe_plan,
                period,
                amount_kopeks(safe_plan, period),
                safe_mode,
                safe_target,
                idempotence_key,
                CONSENT_VERSION,
                now,
                now,
            ),
        )
        con.commit()
    return get_payment_order(order_id) or {}


def get_payment_order(order_id: str) -> Optional[Dict[str, Any]]:
    ensure_payment_storage()
    with _connect() as con:
        row = con.execute(
            "SELECT * FROM payment_orders WHERE order_id = ?",
            (order_id,),
        ).fetchone()
    return dict(row) if row else None


def get_order_by_payment_id(payment_id: str) -> Optional[Dict[str, Any]]:
    ensure_payment_storage()
    with _connect() as con:
        row = con.execute(
            "SELECT * FROM payment_orders WHERE payment_id = ?",
            (payment_id,),
        ).fetchone()
    return dict(row) if row else None


def _update_order(order_id: str, **fields: Any) -> None:
    allowed = {
        "payment_id",
        "status",
        "code",
        "paid_at",
        "email_sent_at",
        "email_error",
        "email",
    }
    updates = [(key, value) for key, value in fields.items() if key in allowed]
    if not updates:
        return
    sql = (
        "UPDATE payment_orders SET "
        + ", ".join(f"{key} = ?" for key, _ in updates)
        + " WHERE order_id = ?"
    )
    values = [value for _, value in updates] + [order_id]
    with _connect() as con:
        con.execute(sql, values)
        con.commit()


def mark_payment_failed(order_id: str, reason: str) -> None:
    _update_order(
        order_id,
        status="failed",
        email_error=reason[:500],
        email="",
        code="",
    )


def _amount_value(kopeks: int) -> str:
    return f"{int(kopeks) / 100:.2f}"


def create_yookassa_payment(order: Dict[str, Any]) -> Dict[str, Any]:
    shop_id = _shop_id()
    secret_key = _secret_key()
    if not shop_id or not secret_key:
        raise PaymentConfigurationError("yookassa_not_configured")

    selected = validate_plan(str(order["plan"]))
    months = validate_months(int(order["months"]))
    description = f"Skryon {selected['title']} — {months} мес."
    amount = {
        "value": _amount_value(int(order["amount_kopeks"])),
        "currency": "RUB",
    }
    payload: Dict[str, Any] = {
        "amount": amount,
        "capture": True,
        "confirmation": {
            "type": "redirect",
            "return_url": f"{_return_base()}/checkout/code?order={order['order_id']}",
        },
        "description": description,
        "metadata": {"order_id": str(order["order_id"])},
    }

    vat_code = _setting("YOOKASSA_VAT_CODE")
    if vat_code:
        try:
            vat = int(vat_code)
        except ValueError as exc:
            raise PaymentConfigurationError("invalid_yookassa_vat_code") from exc
        payload["receipt"] = {
            "customer": {"email": _decrypt_email(str(order["email"]))},
            "items": [
                {
                    "description": (
                        f"Доступ к VPN-сервису Skryon, "
                        f"{selected['title']}, {months} мес."
                    ),
                    "quantity": "1.00",
                    "amount": amount,
                    "vat_code": vat,
                    "payment_mode": "full_payment",
                    "payment_subject": "service",
                }
            ],
        }

    try:
        with httpx.Client(timeout=15.0, auth=(shop_id, secret_key)) as client:
            response = client.post(
                "https://api.yookassa.ru/v3/payments",
                headers={
                    "Idempotence-Key": str(order["idempotence_key"]),
                    "Content-Type": "application/json",
                },
                json=payload,
            )
    except httpx.HTTPError as exc:
        raise PaymentProviderError("yookassa_create_unavailable") from exc

    if response.status_code not in (200, 201):
        logger.error(
            "YooKassa create payment failed: HTTP %s", response.status_code
        )
        raise PaymentProviderError(
            f"yookassa_create_http_{response.status_code}"
        )

    data = response.json()
    payment_id = str(data.get("id") or "").strip()
    confirmation_url = str(
        (data.get("confirmation") or {}).get("confirmation_url") or ""
    ).strip()
    if not payment_id or not confirmation_url:
        raise PaymentProviderError("yookassa_create_invalid_response")

    _update_order(
        str(order["order_id"]),
        payment_id=payment_id,
        status=str(data.get("status") or "pending")[:32],
    )
    return {
        "payment_id": payment_id,
        "confirmation_url": confirmation_url,
        "status": str(data.get("status") or "pending"),
    }


def fetch_yookassa_payment(payment_id: str) -> Dict[str, Any]:
    shop_id = _shop_id()
    secret_key = _secret_key()
    if not shop_id or not secret_key:
        raise PaymentConfigurationError("yookassa_not_configured")
    try:
        with httpx.Client(timeout=12.0, auth=(shop_id, secret_key)) as client:
            response = client.get(
                f"https://api.yookassa.ru/v3/payments/{payment_id}"
            )
    except httpx.HTTPError as exc:
        raise PaymentProviderError("yookassa_status_unavailable") from exc
    if response.status_code != 200:
        raise PaymentProviderError(
            f"yookassa_status_http_{response.status_code}"
        )
    return response.json()


def _verify_provider_payment(
    order: Dict[str, Any], payment: Dict[str, Any]
) -> None:
    if str(payment.get("id") or "") != str(order.get("payment_id") or ""):
        raise PaymentVerificationError("payment_id_mismatch")
    metadata = payment.get("metadata") or {}
    if str(metadata.get("order_id") or "") != str(order["order_id"]):
        raise PaymentVerificationError("payment_order_mismatch")
    amount = payment.get("amount") or {}
    if str(amount.get("currency") or "") != "RUB":
        raise PaymentVerificationError("payment_currency_mismatch")
    try:
        provider_kopeks = int(
            round(float(str(amount.get("value") or "0")) * 100)
        )
    except ValueError as exc:
        raise PaymentVerificationError("payment_amount_invalid") from exc
    if provider_kopeks != int(order["amount_kopeks"]):
        raise PaymentVerificationError("payment_amount_mismatch")
    recipient = payment.get("recipient") or {}
    account_id = str(recipient.get("account_id") or "").strip()
    if account_id and account_id != _shop_id():
        raise PaymentVerificationError("payment_recipient_mismatch")


def _send_activation_email(order: Dict[str, Any]) -> bool:
    if order.get("email_sent_at"):
        return True
    password = _setting("SMTP_PASSWORD")
    user = _setting("SMTP_USER", "support@skryon.ru")
    host = _setting("SMTP_HOST", "smtp.purelymail.com")
    from_addr = _setting("SMTP_FROM", "support@skryon.ru")
    try:
        port = int(_setting("SMTP_PORT", "465"))
    except ValueError:
        port = 465
    if not password:
        _update_order(
            str(order["order_id"]), email_error="smtp_not_configured"
        )
        return False

    selected = validate_plan(str(order["plan"]))
    code = str(order.get("code") or "")
    if not code:
        return False
    recipient_email = _decrypt_email(str(order.get("email") or ""))
    if not recipient_email:
        _update_order(str(order["order_id"]), email_error="email_missing")
        return False
    subject = "Ваш код активации Skryon"
    plain = (
        f"Здравствуйте!\n\n"
        f"Оплата подтверждена. Ваш код активации Skryon: {code}\n"
        f"Тариф: {selected['title']}\n"
        f"Срок: {order['months']} мес.\n\n"
        f"Введите код в приложении Skryon.\n"
        f"Поддержка: support@skryon.ru\n"
    )
    safe_code = html.escape(code)
    safe_title = html.escape(str(selected["title"]))
    rich = (
        "<p>Здравствуйте!</p>"
        "<p>Оплата подтверждена. Ваш код активации Skryon:</p>"
        f"<p style='font-size:22px;font-weight:700;letter-spacing:.04em'>"
        f"{safe_code}</p>"
        f"<p>Тариф: <strong>{safe_title}</strong><br>"
        f"Срок: <strong>{int(order['months'])} мес.</strong></p>"
        "<p>Введите код в приложении Skryon.</p>"
        "<p>Поддержка: <a href='mailto:support@skryon.ru'>"
        "support@skryon.ru</a></p>"
    )
    message = EmailMessage()
    message["From"] = formataddr(("Skryon Support", from_addr))
    message["To"] = recipient_email
    message["Subject"] = subject
    message.set_content(plain)
    message.add_alternative(rich, subtype="html")

    try:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(
            host, port, context=context, timeout=12
        ) as smtp:
            smtp.login(user, password)
            smtp.send_message(message)
    except Exception as exc:
        logger.exception(
            "Activation email delivery failed for order %s",
            order["order_id"],
        )
        _update_order(
            str(order["order_id"]),
            email_error=type(exc).__name__[:120],
        )
        return False

    _update_order(
        str(order["order_id"]),
        email_sent_at=_now_iso(),
        email_error="",
        email="",
        code="",
    )
    with _connect() as con:
        con.execute(
            "DELETE FROM checkout_orders WHERE external_id = ?",
            (str(order["order_id"]),),
        )
        con.commit()
    return True


def sync_and_fulfill(order_id: str) -> Dict[str, Any]:
    order = get_payment_order(order_id)
    if not order:
        raise KeyError("order_not_found")
    if order.get("status") == "succeeded":
        if order.get("email_sent_at"):
            return order
        existing = get_checkout_order(order_id)
        if existing and existing.get("code"):
            mail_order = dict(order)
            mail_order["code"] = str(existing["code"])
            _send_activation_email(mail_order)
            return get_payment_order(order_id) or order
    payment_id = str(order.get("payment_id") or "").strip()
    if not payment_id:
        return order

    payment = fetch_yookassa_payment(payment_id)
    _verify_provider_payment(order, payment)
    provider_status = str(payment.get("status") or "pending")[:32]
    if provider_status == "canceled":
        _update_order(
            order_id,
            status="canceled",
            email="",
            code="",
        )
        return get_payment_order(order_id) or order
    if provider_status != "succeeded":
        _update_order(order_id, status=provider_status)
        return get_payment_order(order_id) or order

    with _FULFILLMENT_LOCK:
        current = get_payment_order(order_id)
        if not current:
            raise KeyError("order_not_found")
        if not current.get("code"):
            selected = validate_plan(str(current["plan"]))
            mode = str(current.get("mode") or "new").strip().lower()
            if mode == "renew":
                target = resolve_renewal_target(str(current.get("target_code") or ""))
                if str(target["plan"]) != str(current["plan"]):
                    raise PaymentVerificationError("renew_plan_changed")
                issued = renew_activation_code(
                    str(target["code"]),
                    str(current["plan"]),
                    int(selected["devices"]),
                    30 * int(current["months"]),
                    "",
                    order_id,
                )
                if not issued:
                    raise PaymentVerificationError("renew_target_missing")
            else:
                issued = create_checkout_code(
                    str(current["plan"]),
                    int(selected["devices"]),
                    30 * int(current["months"]),
                    "",
                    order_id,
                )
            _update_order(
                order_id,
                status="succeeded",
                paid_at=_now_iso(),
                email_error="",
            )
        else:
            issued = get_checkout_order(order_id)
            _update_order(
                order_id,
                status="succeeded",
                paid_at=current.get("paid_at") or _now_iso(),
            )


    fulfilled = get_payment_order(order_id)
    if not fulfilled:
        raise KeyError("order_not_found")
    if issued and issued.get("code"):
        mail_order = dict(fulfilled)
        mail_order["code"] = str(issued["code"])
        _send_activation_email(mail_order)
    return get_payment_order(order_id) or fulfilled


def public_order(order: Dict[str, Any]) -> Dict[str, Any]:
    selected = validate_plan(str(order["plan"]))
    return {
        "ok": True,
        "orderId": str(order["order_id"]),
        "status": str(order["status"]),
        "plan": str(order["plan"]),
        "planTitle": str(selected["title"]),
        "months": int(order["months"]),
        "amount": _amount_value(int(order["amount_kopeks"])),
        "code": "",
        "emailSent": bool(order.get("email_sent_at")),
    }


def webhook_source_allowed(value: str) -> bool:
    raw = value.strip()
    if not raw:
        return False
    try:
        address = ipaddress.ip_address(raw)
    except ValueError:
        return False
    return any(
        address in network for network in YOOKASSA_WEBHOOK_NETWORKS
    )

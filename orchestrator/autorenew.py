import hashlib
import html
import logging
import os
import secrets
import smtplib
import sqlite3
import ssl
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formataddr
from typing import Any, Dict, Optional

import httpx
from cryptography.fernet import Fernet, InvalidToken

from config import DATABASE_PATH, _shared_emery_value
from storage import get_activation_code, get_checkout_order, renew_activation_code

logger = logging.getLogger(__name__)

AUTORENEW_CONSENT_VERSION = "2026-09-23-v1"
AUTORENEW_LEAD_HOURS = 24
AUTORENEW_RETRY_HOURS = 6
AUTORENEW_MAX_FAILURES = 4


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().replace(microsecond=0).isoformat()


def _setting(name: str, default: str = "") -> str:
    return os.getenv(name, "").strip() or _shared_emery_value(name) or default


def autorenew_available() -> bool:
    value = _setting("YOOKASSA_AUTORENEW_ENABLED", "0").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _shop_id() -> str:
    return _setting("YOOKASSA_SHOP_ID", "1467477")


def _secret_key() -> str:
    return _setting("YOOKASSA_SECRET_KEY")


def _cipher() -> Fernet:
    key = _setting("PAYMENT_PII_KEY")
    if not key:
        raise RuntimeError("payment_pii_key_missing")
    return Fernet(key.encode("ascii"))


def _encrypt(value: str) -> str:
    raw = str(value or "").strip()
    if not raw or raw.startswith("enc1:"):
        return raw
    return "enc1:" + _cipher().encrypt(raw.encode("utf-8")).decode("ascii")


def _decrypt(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if not raw.startswith("enc1:"):
        return raw
    try:
        return _cipher().decrypt(raw[5:].encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError) as exc:
        raise RuntimeError("payment_pii_decrypt_failed") from exc


def _connect() -> sqlite3.Connection:
    con = sqlite3.connect(DATABASE_PATH, timeout=20)
    con.row_factory = sqlite3.Row
    return con


def ensure_autorenew_storage() -> None:
    with _connect() as con:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS autorenew_intents (
                order_id TEXT PRIMARY KEY,
                consent_version TEXT NOT NULL,
                consent_at TEXT NOT NULL,
                activated_at TEXT,
                error TEXT NOT NULL DEFAULT ''
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS autorenew_subscriptions (
                code TEXT PRIMARY KEY,
                plan TEXT NOT NULL,
                months INTEGER NOT NULL,
                max_devices INTEGER NOT NULL,
                amount_kopeks INTEGER NOT NULL,
                payment_method_enc TEXT NOT NULL,
                payment_method_type TEXT NOT NULL DEFAULT '',
                email TEXT NOT NULL DEFAULT '',
                manage_token_hash TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL DEFAULT 'active',
                next_charge_at TEXT NOT NULL,
                failure_count INTEGER NOT NULL DEFAULT 0,
                last_error TEXT NOT NULL DEFAULT '',
                last_charge_id TEXT NOT NULL DEFAULT '',
                last_success_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS autorenew_charges (
                charge_id TEXT PRIMARY KEY,
                code TEXT NOT NULL,
                payment_id TEXT UNIQUE,
                status TEXT NOT NULL,
                amount_kopeks INTEGER NOT NULL,
                months INTEGER NOT NULL,
                idempotence_key TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                paid_at TEXT,
                error TEXT NOT NULL DEFAULT ''
            )
            """
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_autorenew_due "
            "ON autorenew_subscriptions(status, next_charge_at)"
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_autorenew_charge_status "
            "ON autorenew_charges(status, updated_at)"
        )
        con.commit()


def register_autorenew_intent(order_id: str) -> None:
    if not autorenew_available():
        raise RuntimeError("autorenew_not_enabled")
    ensure_autorenew_storage()
    now = _now_iso()
    with _connect() as con:
        con.execute(
            """
            INSERT INTO autorenew_intents(order_id, consent_version, consent_at, error)
            VALUES (?, ?, ?, '')
            ON CONFLICT(order_id) DO UPDATE SET
                consent_version=excluded.consent_version,
                consent_at=excluded.consent_at,
                error=''
            """,
            (str(order_id), AUTORENEW_CONSENT_VERSION, now),
        )
        con.commit()


def autorenew_requested(order_id: str) -> bool:
    ensure_autorenew_storage()
    with _connect() as con:
        row = con.execute(
            "SELECT order_id FROM autorenew_intents WHERE order_id=?",
            (str(order_id),),
        ).fetchone()
    return bool(row)


def _mark_intent(order_id: str, *, activated: bool = False, error: str = "") -> None:
    ensure_autorenew_storage()
    with _connect() as con:
        con.execute(
            "UPDATE autorenew_intents SET activated_at=?, error=? WHERE order_id=?",
            (_now_iso() if activated else None, str(error)[:300], str(order_id)),
        )
        con.commit()


def _parse_iso(value: str) -> Optional[datetime]:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _next_charge_iso(expires_at: str) -> str:
    expiry = _parse_iso(expires_at) or (_now() + timedelta(days=30))
    charge_at = expiry - timedelta(hours=AUTORENEW_LEAD_HOURS)
    minimum = _now() + timedelta(minutes=10)
    if charge_at < minimum:
        charge_at = minimum
    return charge_at.replace(microsecond=0).isoformat()


def _token_hash(token: str) -> str:
    return hashlib.sha256(str(token).encode("utf-8")).hexdigest()


def _smtp_send(recipient_enc: str, subject: str, plain: str, rich: str) -> bool:
    recipient = _decrypt(recipient_enc)
    if not recipient:
        return False
    password = _setting("SMTP_PASSWORD")
    if not password:
        return False
    user = _setting("SMTP_USER", "support@skryon.ru")
    host = _setting("SMTP_HOST", "smtp.purelymail.com")
    from_addr = _setting("SMTP_FROM", "support@skryon.ru")
    try:
        port = int(_setting("SMTP_PORT", "465"))
    except ValueError:
        port = 465
    message = EmailMessage()
    message["From"] = formataddr(("Skryon Support", from_addr))
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(plain)
    message.add_alternative(rich, subtype="html")
    try:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(host, port, context=context, timeout=12) as smtp:
            smtp.login(user, password)
            smtp.send_message(message)
        return True
    except Exception:
        logger.exception("Autorenew email delivery failed")
        return False


def _send_enabled_email(
    email_enc: str,
    token: str,
    plan: str,
    months: int,
    amount_kopeks: int,
    next_charge_at: str,
) -> None:
    amount = f"{int(amount_kopeks) / 100:.2f} ₽"
    when = _parse_iso(next_charge_at)
    when_text = when.strftime("%d.%m.%Y %H:%M UTC") if when else next_charge_at
    manage_url = f"https://skryon.ru/autorenewal#token={token}"
    plain = (
        "Автопродление Skryon подключено.\n\n"
        f"Тариф: {plan}\n"
        f"Период: {months} мес.\n"
        f"Сумма повторного платежа: {amount}\n"
        f"Следующая попытка списания: {when_text}\n\n"
        f"Отключить автопродление: {manage_url}\n"
        "Поддержка: support@skryon.ru\n"
    )
    rich = (
        "<p><strong>Автопродление Skryon подключено.</strong></p>"
        f"<p>Тариф: <strong>{html.escape(plan)}</strong><br>"
        f"Период: <strong>{int(months)} мес.</strong><br>"
        f"Сумма повторного платежа: <strong>{html.escape(amount)}</strong><br>"
        f"Следующая попытка списания: <strong>{html.escape(when_text)}</strong></p>"
        f"<p><a href='{html.escape(manage_url)}'>Отключить автопродление</a></p>"
        "<p>Поддержка: <a href='mailto:support@skryon.ru'>support@skryon.ru</a></p>"
    )
    _smtp_send(email_enc, "Автопродление Skryon подключено", plain, rich)


def activate_autorenew_from_payment(
    order: Dict[str, Any],
    payment: Dict[str, Any],
    issued: Dict[str, Any],
) -> Dict[str, Any]:
    order_id = str(order.get("order_id") or "")
    if not order_id or not autorenew_requested(order_id):
        return {"enabled": False, "reason": "not_requested"}
    method = payment.get("payment_method") or {}
    method_id = str(method.get("id") or "").strip()
    if not method_id or method.get("saved") is not True:
        _mark_intent(order_id, error="payment_method_not_saved")
        return {"enabled": False, "reason": "payment_method_not_saved"}
    code = str(issued.get("code") or "").strip()
    if not code:
        _mark_intent(order_id, error="activation_code_missing")
        return {"enabled": False, "reason": "activation_code_missing"}
    current = get_activation_code(code) or {}
    expires_at = str(issued.get("expires_at") or current.get("expires_at") or "")
    max_devices = int(issued.get("max_devices") or current.get("max_devices") or 1)
    token = secrets.token_urlsafe(32)
    now = _now_iso()
    next_charge_at = _next_charge_iso(expires_at)
    email_enc = str(order.get("email") or "")
    plan = str(order.get("plan") or "")
    months = int(order.get("months") or 1)
    amount = int(order.get("amount_kopeks") or 0)
    with _connect() as con:
        con.execute(
            """
            INSERT INTO autorenew_subscriptions(
                code, plan, months, max_devices, amount_kopeks,
                payment_method_enc, payment_method_type, email,
                manage_token_hash, status, next_charge_at,
                failure_count, last_error, last_charge_id,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, 0, '', '', ?, ?)
            ON CONFLICT(code) DO UPDATE SET
                plan=excluded.plan,
                months=excluded.months,
                max_devices=excluded.max_devices,
                amount_kopeks=excluded.amount_kopeks,
                payment_method_enc=excluded.payment_method_enc,
                payment_method_type=excluded.payment_method_type,
                email=excluded.email,
                manage_token_hash=excluded.manage_token_hash,
                status='active',
                next_charge_at=excluded.next_charge_at,
                failure_count=0,
                last_error='',
                updated_at=excluded.updated_at
            """,
            (
                code,
                plan,
                months,
                max_devices,
                amount,
                _encrypt(method_id),
                str(method.get("type") or "")[:64],
                email_enc,
                _token_hash(token),
                next_charge_at,
                now,
                now,
            ),
        )
        con.commit()
    _mark_intent(order_id, activated=True, error="")
    _send_enabled_email(email_enc, token, plan, months, amount, next_charge_at)
    return {"enabled": True, "next_charge_at": next_charge_at}


def reschedule_existing_autorenew(code: str, expires_at: str) -> None:
    ensure_autorenew_storage()
    if not code or not expires_at:
        return
    with _connect() as con:
        con.execute(
            """
            UPDATE autorenew_subscriptions
            SET next_charge_at=?, failure_count=0, last_error='', updated_at=?
            WHERE code=? AND status='active'
            """,
            (_next_charge_iso(expires_at), _now_iso(), str(code)),
        )
        con.commit()


def disable_autorenew(token: str) -> bool:
    ensure_autorenew_storage()
    raw = str(token or "").strip()
    if len(raw) < 32 or len(raw) > 256:
        return False
    digest = _token_hash(raw)
    with _connect() as con:
        cursor = con.execute(
            """
            UPDATE autorenew_subscriptions
            SET status='disabled', payment_method_enc='', updated_at=?
            WHERE manage_token_hash=? AND status IN ('active','payment_failed')
            """,
            (_now_iso(), digest),
        )
        con.commit()
    return cursor.rowcount > 0


def _subscription(code: str) -> Optional[Dict[str, Any]]:
    with _connect() as con:
        row = con.execute(
            "SELECT * FROM autorenew_subscriptions WHERE code=?",
            (str(code),),
        ).fetchone()
    return dict(row) if row else None


def _charge(charge_id: str) -> Optional[Dict[str, Any]]:
    with _connect() as con:
        row = con.execute(
            "SELECT * FROM autorenew_charges WHERE charge_id=?",
            (str(charge_id),),
        ).fetchone()
    return dict(row) if row else None


def _update_charge(charge_id: str, **fields: Any) -> None:
    allowed = {"payment_id", "status", "updated_at", "paid_at", "error"}
    items = [(k, v) for k, v in fields.items() if k in allowed]
    if not items:
        return
    sql = "UPDATE autorenew_charges SET " + ", ".join(f"{k}=?" for k, _ in items) + " WHERE charge_id=?"
    with _connect() as con:
        con.execute(sql, [v for _, v in items] + [str(charge_id)])
        con.commit()


def _schedule_failure(code: str, reason: str) -> None:
    sub = _subscription(code)
    if not sub:
        return
    failures = int(sub.get("failure_count") or 0) + 1
    now = _now()
    if failures >= AUTORENEW_MAX_FAILURES:
        status = "payment_failed"
        next_at = str(sub.get("next_charge_at") or _now_iso())
    else:
        status = "active"
        next_at = (now + timedelta(hours=AUTORENEW_RETRY_HOURS)).replace(microsecond=0).isoformat()
    with _connect() as con:
        con.execute(
            """
            UPDATE autorenew_subscriptions
            SET status=?, next_charge_at=?, failure_count=?, last_error=?, updated_at=?
            WHERE code=?
            """,
            (status, next_at, failures, str(reason)[:300], _now_iso(), str(code)),
        )
        con.commit()


def _validate_subscription(sub: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    row = get_activation_code(str(sub.get("code") or ""))
    if not row:
        return None
    if str(row.get("status") or "").lower() == "banned":
        return None
    if str(row.get("plan") or "") != str(sub.get("plan") or ""):
        return None
    if int(row.get("used_devices") or 0) > int(sub.get("max_devices") or 1):
        return None
    return row


def _disable_for_security(code: str, reason: str) -> None:
    with _connect() as con:
        con.execute(
            """
            UPDATE autorenew_subscriptions
            SET status='disabled', payment_method_enc='', last_error=?, updated_at=?
            WHERE code=?
            """,
            (str(reason)[:300], _now_iso(), str(code)),
        )
        con.commit()


def _receipt(email_enc: str, amount_kopeks: int, months: int) -> Optional[Dict[str, Any]]:
    vat_code = _setting("YOOKASSA_VAT_CODE")
    if not vat_code:
        return None
    try:
        vat = int(vat_code)
    except ValueError:
        return None
    return {
        "customer": {"email": _decrypt(email_enc)},
        "items": [
            {
                "description": f"Автопродление доступа к VPN-сервису Skryon, {months} мес.",
                "quantity": "1.00",
                "amount": {"value": f"{amount_kopeks / 100:.2f}", "currency": "RUB"},
                "vat_code": vat,
                "payment_mode": "full_payment",
                "payment_subject": "service",
            }
        ],
    }


def _begin_charge(sub: Dict[str, Any]) -> Dict[str, Any]:
    charge_id = "ar_" + secrets.token_urlsafe(18)
    now = _now_iso()
    with _connect() as con:
        con.execute(
            """
            INSERT INTO autorenew_charges(
                charge_id, code, status, amount_kopeks, months,
                idempotence_key, created_at, updated_at
            ) VALUES (?, ?, 'creating', ?, ?, ?, ?, ?)
            """,
            (
                charge_id,
                str(sub["code"]),
                int(sub["amount_kopeks"]),
                int(sub["months"]),
                secrets.token_hex(24),
                now,
                now,
            ),
        )
        con.execute(
            "UPDATE autorenew_subscriptions SET last_charge_id=?, updated_at=? WHERE code=?",
            (charge_id, now, str(sub["code"])),
        )
        con.commit()
    return _charge(charge_id) or {}


def _provider_create(charge: Dict[str, Any], sub: Dict[str, Any]) -> Dict[str, Any]:
    shop_id = _shop_id()
    secret = _secret_key()
    if not shop_id or not secret:
        raise RuntimeError("yookassa_not_configured")
    amount = int(charge["amount_kopeks"])
    payload: Dict[str, Any] = {
        "amount": {"value": f"{amount / 100:.2f}", "currency": "RUB"},
        "capture": True,
        "payment_method_id": _decrypt(str(sub["payment_method_enc"])),
        "description": f"Автопродление Skryon — {int(charge['months'])} мес.",
        "metadata": {"autorenew_charge_id": str(charge["charge_id"])},
    }
    receipt = _receipt(str(sub.get("email") or ""), amount, int(charge["months"]))
    if receipt:
        payload["receipt"] = receipt
    with httpx.Client(timeout=15.0, auth=(shop_id, secret)) as client:
        response = client.post(
            "https://api.yookassa.ru/v3/payments",
            headers={
                "Idempotence-Key": str(charge["idempotence_key"]),
                "Content-Type": "application/json",
            },
            json=payload,
        )
    if response.status_code not in (200, 201):
        raise RuntimeError(f"yookassa_create_http_{response.status_code}")
    data = response.json()
    payment_id = str(data.get("id") or "").strip()
    if not payment_id:
        raise RuntimeError("yookassa_create_invalid_response")
    _update_charge(
        str(charge["charge_id"]),
        payment_id=payment_id,
        status=str(data.get("status") or "pending")[:32],
        updated_at=_now_iso(),
    )
    return data


def _provider_fetch(payment_id: str) -> Dict[str, Any]:
    shop_id = _shop_id()
    secret = _secret_key()
    if not shop_id or not secret:
        raise RuntimeError("yookassa_not_configured")
    with httpx.Client(timeout=12.0, auth=(shop_id, secret)) as client:
        response = client.get(f"https://api.yookassa.ru/v3/payments/{payment_id}")
    if response.status_code != 200:
        raise RuntimeError(f"yookassa_status_http_{response.status_code}")
    return response.json()


def _verify_charge(charge: Dict[str, Any], payment: Dict[str, Any]) -> None:
    if str(payment.get("id") or "") != str(charge.get("payment_id") or ""):
        raise RuntimeError("payment_id_mismatch")
    metadata = payment.get("metadata") or {}
    if str(metadata.get("autorenew_charge_id") or "") != str(charge["charge_id"]):
        raise RuntimeError("payment_metadata_mismatch")
    amount = payment.get("amount") or {}
    if str(amount.get("currency") or "") != "RUB":
        raise RuntimeError("payment_currency_mismatch")
    provider_kopeks = int(round(float(str(amount.get("value") or "0")) * 100))
    if provider_kopeks != int(charge["amount_kopeks"]):
        raise RuntimeError("payment_amount_mismatch")
    recipient = payment.get("recipient") or {}
    account_id = str(recipient.get("account_id") or "").strip()
    if account_id and account_id != _shop_id():
        raise RuntimeError("payment_recipient_mismatch")


def _fulfill_charge(charge: Dict[str, Any], sub: Dict[str, Any]) -> None:
    current = _validate_subscription(sub)
    if not current:
        _disable_for_security(str(sub["code"]), "subscription_target_changed")
        raise RuntimeError("subscription_target_changed_after_payment")
    existing = get_checkout_order(str(charge["charge_id"]))
    if existing and existing.get("code"):
        issued = existing
    else:
        issued = renew_activation_code(
            str(sub["code"]),
            str(sub["plan"]),
            int(sub["max_devices"]),
            30 * int(sub["months"]),
            "",
            str(charge["charge_id"]),
        )
    if not issued:
        raise RuntimeError("renew_target_missing")
    now = _now_iso()
    next_at = _next_charge_iso(str(issued.get("expires_at") or ""))
    _update_charge(
        str(charge["charge_id"]),
        status="succeeded",
        paid_at=now,
        updated_at=now,
        error="",
    )
    with _connect() as con:
        con.execute(
            """
            UPDATE autorenew_subscriptions
            SET next_charge_at=?, failure_count=0, last_error='',
                last_success_at=?, updated_at=?
            WHERE code=?
            """,
            (next_at, now, now, str(sub["code"])),
        )
        con.commit()
    amount = f"{int(charge['amount_kopeks']) / 100:.2f} ₽"
    _smtp_send(
        str(sub.get("email") or ""),
        "Skryon автоматически продлён",
        f"Автопродление выполнено. Списано: {amount}.\nПодписка продлена на {int(sub['months'])} мес.\n",
        f"<p>Автопродление выполнено.</p><p>Списано: <strong>{html.escape(amount)}</strong>.<br>Подписка продлена на <strong>{int(sub['months'])} мес.</strong></p>",
    )


def _process_charge(charge: Dict[str, Any]) -> str:
    sub = _subscription(str(charge.get("code") or ""))
    if not sub or str(sub.get("status") or "") != "active":
        return "ignored"
    try:
        if str(charge.get("status") or "") == "creating" and not charge.get("payment_id"):
            payment = _provider_create(charge, sub)
            charge = _charge(str(charge["charge_id"])) or charge
        else:
            payment_id = str(charge.get("payment_id") or "")
            if not payment_id:
                return "pending"
            payment = _provider_fetch(payment_id)
        provider_status = str(payment.get("status") or "pending")[:32]
        if charge.get("payment_id"):
            _verify_charge(charge, payment)
        if provider_status == "succeeded":
            _fulfill_charge(charge, sub)
            return "succeeded"
        if provider_status == "canceled":
            _update_charge(
                str(charge["charge_id"]),
                status="canceled",
                updated_at=_now_iso(),
                error="provider_canceled",
            )
            _schedule_failure(str(sub["code"]), "provider_canceled")
            return "canceled"
        _update_charge(
            str(charge["charge_id"]),
            status=provider_status,
            updated_at=_now_iso(),
        )
        return "pending"
    except httpx.HTTPError:
        logger.exception("Autorenew provider network error for %s", charge.get("charge_id"))
        return "retry_later"
    except Exception as exc:
        logger.exception("Autorenew charge failed for %s", charge.get("charge_id"))
        _update_charge(
            str(charge["charge_id"]),
            status="failed",
            updated_at=_now_iso(),
            error=str(exc)[:300],
        )
        _schedule_failure(str(sub["code"]), str(exc))
        return "failed"


def process_autorenewals(limit: int = 50) -> Dict[str, int]:
    ensure_autorenew_storage()
    result = {"pending": 0, "created": 0, "succeeded": 0, "failed": 0, "disabled": 0}
    if not autorenew_available():
        return result
    with _connect() as con:
        outstanding = [
            dict(row)
            for row in con.execute(
                """
                SELECT * FROM autorenew_charges
                WHERE status IN ('creating','pending','waiting_for_capture')
                ORDER BY created_at ASC LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
        ]
    for charge in outstanding:
        outcome = _process_charge(charge)
        if outcome == "succeeded":
            result["succeeded"] += 1
        elif outcome in {"failed", "canceled"}:
            result["failed"] += 1
        else:
            result["pending"] += 1

    now = _now_iso()
    with _connect() as con:
        due = [
            dict(row)
            for row in con.execute(
                """
                SELECT * FROM autorenew_subscriptions
                WHERE status='active' AND next_charge_at<=?
                ORDER BY next_charge_at ASC LIMIT ?
                """,
                (now, int(limit)),
            ).fetchall()
        ]
    for sub in due:
        with _connect() as con:
            active_charge = con.execute(
                """
                SELECT charge_id FROM autorenew_charges
                WHERE code=? AND status IN ('creating','pending','waiting_for_capture')
                LIMIT 1
                """,
                (str(sub["code"]),),
            ).fetchone()
        if active_charge:
            continue
        if not _validate_subscription(sub):
            _disable_for_security(str(sub["code"]), "subscription_target_invalid")
            result["disabled"] += 1
            continue
        charge = _begin_charge(sub)
        result["created"] += 1
        outcome = _process_charge(charge)
        if outcome == "succeeded":
            result["succeeded"] += 1
        elif outcome in {"failed", "canceled"}:
            result["failed"] += 1
        else:
            result["pending"] += 1
    return result

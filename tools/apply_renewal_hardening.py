from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected 1 match, got {count}")
    return text.replace(old, new, 1)


def replace_between(text: str, start: str, end: str, replacement: str, label: str) -> str:
    start_at = text.find(start)
    if start_at < 0:
        raise SystemExit(f"{label}: start marker missing")
    end_at = text.find(end, start_at + len(start))
    if end_at < 0:
        raise SystemExit(f"{label}: end marker missing")
    return text[:start_at] + replacement + text[end_at:]


def patch_checkout_routes() -> None:
    path = ROOT / "orchestrator" / "checkout_routes.py"
    text = path.read_text(encoding="utf-8")
    if "SKRYON_RENEW_SERVER_PLAN_V1" in text:
        return

    text = replace_once(
        text,
        "class RenewCodeRequest(CheckoutRequest):\n    code: str = Field(min_length=3, max_length=64)\n",
        "class RenewCodeRequest(BaseModel):\n    code: str = Field(min_length=3, max_length=64)\n    customer: str = Field(default='', max_length=128)\n    months: int = Field(default=1, ge=MIN_MONTHS, le=MAX_MONTHS)\n",
        "renew request model",
    )

    issue_new = '''# SKRYON_RENEW_SERVER_PLAN_V1\ndef issue_renewal(code: str, plan: str, customer: str, months: int = 1, external_id: Optional[str] = None) -> dict:\n    # The client-supplied plan is intentionally ignored for renewals.\n    current = get_activation_code(code)\n    if not current:\n        return {'ok': False, 'reason': 'not_found'}\n    if current.get('status') == 'banned':\n        return {'ok': False, 'reason': 'banned'}\n\n    effective_plan = str(current.get('plan') or '').strip()\n    selected = PLANS.get(effective_plan)\n    if not selected:\n        return {'ok': False, 'reason': 'invalid_current_plan'}\n\n    used_devices = int(current.get('used_devices') or 0)\n    if used_devices > selected['devices']:\n        return {\n            'ok': False,\n            'reason': 'device_limit_conflict',\n            'usedDevices': used_devices,\n            'maxDevices': selected['devices'],\n        }\n\n    selected_months = safe_months(months)\n    days = selected['days'] * selected_months\n    order = renew_activation_code(\n        code,\n        effective_plan,\n        selected['devices'],\n        days,\n        customer.strip(),\n        external_id,\n    )\n    if not order:\n        return {'ok': False, 'reason': 'not_found'}\n    return {\n        'ok': True,\n        'orderId': order['external_id'],\n        'code': order['code'],\n        'plan': effective_plan,\n        'planTitle': selected['title'],\n        'months': selected_months,\n        'days': days,\n        'maxDevices': order['max_devices'],\n        'expiresAt': order['expires_at'],\n        'redirectUrl': '/checkout/code?order=' + order['external_id'],\n    }\n\n\n'''
    text = replace_between(
        text,
        "def issue_renewal(",
        "def renewal_error_response",
        issue_new,
        "issue renewal",
    )

    find_new = '''@router.post('/api/checkout/find-code')\ndef find_code(payload: CodeLookupRequest, request: Request):\n    if limited('find-code:' + remote_bucket_key(request)):\n        return JSONResponse(status_code=429, content={'ok': False, 'reason': 'too_many_attempts'})\n    # Never disclose whether a supplied activation code exists.\n    return JSONResponse(status_code=404, content={'ok': False, 'reason': 'not_available'})\n\n\n'''
    text = replace_between(
        text,
        "@router.post('/api/checkout/find-code')",
        "@router.post('/api/checkout/renew-code')",
        find_new,
        "public code lookup",
    )

    renew_new = '''@router.post('/api/checkout/renew-code')\ndef renew_code(\n    payload: RenewCodeRequest,\n    request: Request,\n    x_checkout_secret: str = Header(default=''),\n):\n    if not checkout_secret_valid(x_checkout_secret):\n        return checkout_auth_error()\n    if limited('renew-code:' + remote_bucket_key(request)):\n        return JSONResponse(status_code=429, content={'ok': False, 'reason': 'too_many_attempts'})\n    result = issue_renewal(payload.code, '', payload.customer, payload.months)\n    if not result.get('ok'):\n        return renewal_error_response(result)\n    return result\n\n\n'''
    text = replace_between(
        text,
        "@router.post('/api/checkout/renew-code')",
        "@router.post('/api/checkout/callback')",
        renew_new,
        "secret renewal route",
    )

    callback_new = '''@router.post('/api/checkout/callback')\ndef callback(payload: CheckoutCallbackRequest, x_checkout_secret: str = Header(default='')):\n    if not checkout_secret_valid(x_checkout_secret):\n        return checkout_auth_error()\n    if payload.status != 'paid':\n        return JSONResponse(status_code=400, content={'ok': False, 'reason': 'not_paid'})\n    if payload.mode == 'renew':\n        if not payload.code:\n            return JSONResponse(status_code=400, content={'ok': False, 'reason': 'missing_code'})\n        result = issue_renewal(payload.code, '', payload.customer, payload.months, payload.externalId)\n        if not result.get('ok'):\n            return renewal_error_response(result)\n        return result\n    if not plan_or_error(payload.plan):\n        return JSONResponse(status_code=400, content={'ok': False, 'reason': 'bad_plan'})\n    return issue_code(payload.plan, payload.customer, payload.externalId, payload.months)\n\n\n'''
    text = replace_between(
        text,
        "@router.post('/api/checkout/callback')",
        "@router.get('/api/checkout/order/{order_id}')",
        callback_new,
        "checkout callback",
    )
    path.write_text(text, encoding="utf-8")


def patch_payment_routes() -> None:
    path = ROOT / "orchestrator" / "payment_routes.py"
    text = path.read_text(encoding="utf-8")
    if "SKRYON_RENEW_PAYMENT_LOCK_V1" in text:
        return

    text = replace_once(
        text,
        '''class CreatePaymentRequest(BaseModel):\n    email: str = Field(min_length=3, max_length=254)\n    plan: str = Field(min_length=1, max_length=32)\n    months: int = Field(ge=1, le=12)\n    personalDataConsent: bool = False\n''',
        '''class CreatePaymentRequest(BaseModel):\n    email: str = Field(min_length=3, max_length=254)\n    plan: str = Field(default='', max_length=32)\n    months: int = Field(ge=1, le=12)\n    personalDataConsent: bool = False\n    mode: str = Field(default='new', max_length=16)\n    code: str = Field(default='', max_length=64)\n''',
        "payment request model",
    )

    route_new = '''# SKRYON_RENEW_PAYMENT_LOCK_V1\n@router.post("/api/checkout/payment")\ndef create_payment(payload: CreatePaymentRequest, request: Request):\n    if _limited(request):\n        return JSONResponse(\n            status_code=429,\n            content={"ok": False, "reason": "too_many_attempts"},\n        )\n    if payload.personalDataConsent is not True:\n        return JSONResponse(\n            status_code=400,\n            content={"ok": False, "reason": "personal_data_consent_required"},\n        )\n    try:\n        email = validate_email(payload.email)\n        months = validate_months(payload.months)\n        mode = str(payload.mode or "new").strip().lower()\n        if mode not in {"new", "renew"}:\n            raise ValueError("invalid_mode")\n        if mode == "renew":\n            target_code = str(payload.code or "").strip()\n            if not target_code:\n                raise ValueError("renew_code_invalid")\n        else:\n            validate_plan(payload.plan)\n            target_code = ""\n    except (TypeError, ValueError) as exc:\n        return JSONResponse(\n            status_code=400,\n            content={"ok": False, "reason": str(exc)},\n        )\n    if not payment_ready():\n        return JSONResponse(\n            status_code=503,\n            content={"ok": False, "reason": "payment_not_configured"},\n        )\n\n    try:\n        order = create_payment_order(\n            email,\n            payload.plan,\n            months,\n            mode=mode,\n            target_code=target_code,\n        )\n    except (TypeError, ValueError) as exc:\n        reason = "renew_code_invalid" if mode == "renew" else str(exc)\n        return JSONResponse(\n            status_code=400,\n            content={"ok": False, "reason": reason},\n        )\n\n    try:\n        provider = create_yookassa_payment(order)\n    except PaymentConfigurationError as exc:\n        mark_payment_failed(str(order["order_id"]), str(exc))\n        return JSONResponse(\n            status_code=503,\n            content={"ok": False, "reason": "payment_not_configured"},\n        )\n    except PaymentProviderError as exc:\n        mark_payment_failed(str(order["order_id"]), str(exc))\n        return JSONResponse(\n            status_code=502,\n            content={"ok": False, "reason": "payment_provider_unavailable"},\n        )\n\n    return {\n        "ok": True,\n        "orderId": str(order["order_id"]),\n        "confirmationUrl": provider["confirmation_url"],\n    }\n\n\n'''
    text = replace_between(
        text,
        '@router.post("/api/checkout/payment")',
        '@router.get("/api/checkout/payment/{order_id}")',
        route_new,
        "payment route",
    )
    path.write_text(text, encoding="utf-8")


def patch_yookassa_checkout() -> None:
    path = ROOT / "orchestrator" / "yookassa_checkout.py"
    text = path.read_text(encoding="utf-8")
    if "SKRYON_RENEW_FULFILLMENT_V1" in text:
        return

    text = replace_once(
        text,
        "from storage import create_checkout_code, get_checkout_order\n",
        "from storage import (\n    create_checkout_code,\n    get_activation_code,\n    get_checkout_order,\n    renew_activation_code,\n)\n",
        "storage imports",
    )

    resolver = '''# SKRYON_RENEW_FULFILLMENT_V1\ndef resolve_renewal_target(code: str) -> Dict[str, Any]:\n    row = get_activation_code(code)\n    if not row or str(row.get("status") or "").lower() == "banned":\n        raise ValueError("renew_code_invalid")\n    plan = str(row.get("plan") or "").strip()\n    selected = validate_plan(plan)\n    used_devices = int(row.get("used_devices") or 0)\n    if used_devices > int(selected["devices"]):\n        raise ValueError("renew_code_invalid")\n    return {"code": str(row["code"]), "plan": plan, "selected": selected}\n\n\n'''
    text = replace_once(
        text,
        "def amount_kopeks(plan: str, months: int) -> int:\n",
        resolver + "def amount_kopeks(plan: str, months: int) -> int:\n",
        "renew target resolver",
    )

    text = replace_once(
        text,
        "                amount_kopeks INTEGER NOT NULL,\n                code TEXT,\n",
        "                amount_kopeks INTEGER NOT NULL,\n                mode TEXT NOT NULL DEFAULT 'new',\n                target_code TEXT NOT NULL DEFAULT '',\n                code TEXT,\n",
        "payment schema definition",
    )
    text = replace_once(
        text,
        '''        if "fulfilled_at" not in columns:\n            con.execute("ALTER TABLE payment_orders ADD COLUMN fulfilled_at TEXT")\n''',
        '''        if "fulfilled_at" not in columns:\n            con.execute("ALTER TABLE payment_orders ADD COLUMN fulfilled_at TEXT")\n        if "mode" not in columns:\n            con.execute("ALTER TABLE payment_orders ADD COLUMN mode TEXT NOT NULL DEFAULT 'new'")\n        if "target_code" not in columns:\n            con.execute("ALTER TABLE payment_orders ADD COLUMN target_code TEXT NOT NULL DEFAULT ''")\n''',
        "payment schema migration",
    )

    create_new = '''def create_payment_order(\n    email: str,\n    plan: str,\n    months: int,\n    mode: str = "new",\n    target_code: str = "",\n) -> Dict[str, Any]:\n    ensure_payment_storage()\n    safe_email = validate_email(email)\n    period = validate_months(months)\n    safe_mode = str(mode or "new").strip().lower()\n    if safe_mode not in {"new", "renew"}:\n        raise ValueError("invalid_mode")\n\n    safe_target = ""\n    if safe_mode == "renew":\n        target = resolve_renewal_target(target_code)\n        safe_target = str(target["code"])\n        safe_plan = str(target["plan"])\n    else:\n        validate_plan(plan)\n        safe_plan = plan\n\n    order_id = secrets.token_urlsafe(24)\n    idempotence_key = secrets.token_hex(24)\n    now = _now_iso()\n    with _connect() as con:\n        con.execute(\n            """\n            INSERT INTO payment_orders(\n                order_id, status, email, plan, months, amount_kopeks,\n                mode, target_code, idempotence_key,\n                consent_version, consent_at, created_at\n            ) VALUES (?, 'pending', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)\n            """,\n            (\n                order_id,\n                _encrypt_email(safe_email),\n                safe_plan,\n                period,\n                amount_kopeks(safe_plan, period),\n                safe_mode,\n                safe_target,\n                idempotence_key,\n                CONSENT_VERSION,\n                now,\n                now,\n            ),\n        )\n        con.commit()\n    return get_payment_order(order_id) or {}\n\n\n'''
    text = replace_between(
        text,
        "def create_payment_order(",
        "def get_payment_order",
        create_new,
        "payment order creation",
    )

    fulfill_new = '''        if not current.get("code"):\n            selected = validate_plan(str(current["plan"]))\n            mode = str(current.get("mode") or "new").strip().lower()\n            if mode == "renew":\n                target = resolve_renewal_target(str(current.get("target_code") or ""))\n                if str(target["plan"]) != str(current["plan"]):\n                    raise PaymentVerificationError("renew_plan_changed")\n                issued = renew_activation_code(\n                    str(target["code"]),\n                    str(current["plan"]),\n                    int(selected["devices"]),\n                    30 * int(current["months"]),\n                    "",\n                    order_id,\n                )\n                if not issued:\n                    raise PaymentVerificationError("renew_target_missing")\n            else:\n                issued = create_checkout_code(\n                    str(current["plan"]),\n                    int(selected["devices"]),\n                    30 * int(current["months"]),\n                    "",\n                    order_id,\n                )\n            _update_order(\n                order_id,\n                status="succeeded",\n                paid_at=_now_iso(),\n                email_error="",\n            )\n        else:\n            issued = get_checkout_order(order_id)\n            _update_order(\n                order_id,\n                status="succeeded",\n                paid_at=current.get("paid_at") or _now_iso(),\n            )\n'''
    text = replace_between(
        text,
        '        if not current.get("code"):',
        "\n\n    fulfilled = get_payment_order(order_id)",
        fulfill_new,
        "payment fulfillment",
    )
    path.write_text(text, encoding="utf-8")


def main() -> None:
    patch_checkout_routes()
    patch_payment_routes()
    patch_yookassa_checkout()
    print("RENEWAL_HARDENING_SOURCE_OK")


if __name__ == "__main__":
    main()

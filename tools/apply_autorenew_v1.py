#!/usr/bin/env python3
from pathlib import Path
import re
import sys

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else "/opt/nt/orchestrator")
PAYMENT = ROOT / "payment_routes.py"
YOOKASSA = ROOT / "yookassa_checkout.py"


def patch_payment_routes() -> None:
    s = PAYMENT.read_text(encoding="utf-8")
    if "SKRYON_AUTORENEW_ROUTES_V1" in s:
        return

    logger_anchor = "\n\nlogger = logging.getLogger(__name__)"
    if logger_anchor not in s:
        raise SystemExit("payment_routes logger anchor missing")
    s = s.replace(
        logger_anchor,
        "\n\nfrom autorenew import (\n"
        "    autorenew_available,\n"
        "    disable_autorenew,\n"
        "    register_autorenew_intent,\n"
        ")\n"
        "\n# SKRYON_AUTORENEW_ROUTES_V1"
        + logger_anchor,
        1,
    )

    class_match = re.search(
        r"class CreatePaymentRequest\(BaseModel\):\n(?P<body>.*?)(?=\n\ndef _source_ip)",
        s,
        re.S,
    )
    if not class_match:
        raise SystemExit("CreatePaymentRequest block missing")
    body = class_match.group("body")
    if "autorenew:" not in body:
        body = body.rstrip() + (
            "\n    autorenew: bool = False\n"
            "    autorenewConsent: bool = False\n"
            "\n\nclass DisableAutorenewRequest(BaseModel):\n"
            "    token: str = Field(min_length=32, max_length=256)\n"
        )
        s = s[: class_match.start("body")] + body + s[class_match.end("body") :]

    old_ready = 'return {"ok": True, "ready": payment_ready()}'
    new_ready = 'return {"ok": True, "ready": payment_ready(), "autorenew": autorenew_available()}'
    if old_ready not in s:
        raise SystemExit("payment-ready return anchor missing")
    s = s.replace(old_ready, new_ready, 1)

    consent_anchor = '''    if payload.personalDataConsent is not True:\n        return JSONResponse(\n            status_code=400,\n            content={"ok": False, "reason": "personal_data_consent_required"},\n        )\n'''
    if consent_anchor not in s:
        raise SystemExit("personal consent anchor missing")
    consent_new = consent_anchor + '''    if payload.autorenew:\n        if not autorenew_available():\n            return JSONResponse(\n                status_code=503,\n                content={"ok": False, "reason": "autorenew_not_enabled"},\n            )\n        if payload.autorenewConsent is not True:\n            return JSONResponse(\n                status_code=400,\n                content={"ok": False, "reason": "autorenew_consent_required"},\n            )\n'''
    s = s.replace(consent_anchor, consent_new, 1)

    provider_anchor = '''    try:\n        provider = create_yookassa_payment(order)\n'''
    if provider_anchor not in s:
        raise SystemExit("provider creation anchor missing")
    registration = '''    if payload.autorenew:\n        try:\n            register_autorenew_intent(str(order["order_id"]))\n        except Exception:\n            logger.exception("Could not register autorenew intent for %s", order.get("order_id"))\n            mark_payment_failed(str(order["order_id"]), "autorenew_intent_failed")\n            return JSONResponse(\n                status_code=503,\n                content={"ok": False, "reason": "autorenew_unavailable"},\n            )\n\n'''
    s = s.replace(provider_anchor, registration + provider_anchor, 1)

    if '/api/checkout/autorenew/disable' not in s:
        s += '''\n\n@router.post("/api/checkout/autorenew/disable")\ndef disable_autorenew_route(payload: DisableAutorenewRequest):\n    try:\n        disable_autorenew(payload.token)\n    except Exception:\n        logger.exception("Autorenew disable request failed")\n    # Always generic: never expose whether a token/subscription exists.\n    return {"ok": True}\n'''

    PAYMENT.write_text(s, encoding="utf-8")


def patch_yookassa() -> None:
    s = YOOKASSA.read_text(encoding="utf-8")
    if "SKRYON_AUTORENEW_YOOKASSA_V1" in s:
        return

    logger_anchor = "\n\nlogger = logging.getLogger(__name__)"
    if logger_anchor not in s:
        raise SystemExit("yookassa logger anchor missing")
    s = s.replace(
        logger_anchor,
        "\n\nfrom autorenew import (\n"
        "    activate_autorenew_from_payment,\n"
        "    autorenew_requested,\n"
        "    reschedule_existing_autorenew,\n"
        ")\n"
        "\n# SKRYON_AUTORENEW_YOOKASSA_V1"
        + logger_anchor,
        1,
    )

    vat_anchor = '\n\n    vat_code = _setting("YOOKASSA_VAT_CODE")'
    if vat_anchor not in s:
        raise SystemExit("YooKassa payload/VAT anchor missing")
    s = s.replace(
        vat_anchor,
        '''\n\n    if autorenew_requested(str(order["order_id"])):\n        payload["save_payment_method"] = True\n''' + vat_anchor,
        1,
    )

    mail_anchor = '''    if issued and issued.get("code"):\n        mail_order = dict(fulfilled)\n'''
    if mail_anchor not in s:
        raise SystemExit("fulfillment mail anchor missing")
    hook = '''    if issued and issued.get("code"):\n        try:\n            if autorenew_requested(order_id):\n                result = activate_autorenew_from_payment(fulfilled, payment, issued)\n                if not result.get("enabled"):\n                    logger.warning(\n                        "Autorenew was requested but not activated for order %s: %s",\n                        order_id,\n                        result.get("reason"),\n                    )\n            else:\n                reschedule_existing_autorenew(\n                    str(issued.get("code") or ""),\n                    str(issued.get("expires_at") or ""),\n                )\n        except Exception:\n            # A paid one-time purchase must still be fulfilled if autorenew setup fails.\n            logger.exception("Autorenew activation failed for paid order %s", order_id)\n        mail_order = dict(fulfilled)\n'''
    s = s.replace(mail_anchor, hook, 1)

    YOOKASSA.write_text(s, encoding="utf-8")


def verify() -> None:
    payment = PAYMENT.read_text(encoding="utf-8")
    yookassa = YOOKASSA.read_text(encoding="utf-8")
    checks = [
        ("SKRYON_AUTORENEW_ROUTES_V1" in payment, "payment marker"),
        ("autorenewConsent" in payment, "autorenew consent field"),
        ('/api/checkout/autorenew/disable' in payment, "disable endpoint"),
        ("SKRYON_AUTORENEW_YOOKASSA_V1" in yookassa, "yookassa marker"),
        ('payload["save_payment_method"] = True' in yookassa, "save payment method"),
        ("activate_autorenew_from_payment" in yookassa, "activation hook"),
    ]
    missing = [name for ok, name in checks if not ok]
    if missing:
        raise SystemExit("autorenew verification failed: " + ", ".join(missing))


patch_payment_routes()
patch_yookassa()
verify()
print("AUTORENEW_PATCH_V1_OK")

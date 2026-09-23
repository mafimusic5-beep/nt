from pathlib import Path

import apply_renewal_hardening as base

ROOT = Path(__file__).resolve().parents[1]


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected 1 match, got {count}")
    return text.replace(old, new, 1)


def patch_checkout() -> None:
    base.patch_checkout_routes()


def patch_payment() -> None:
    path = ROOT / "orchestrator" / "payment_routes.py"
    text = path.read_text(encoding="utf-8")
    if "SKRYON_RENEW_PAYMENT_LOCK_V2" in text:
        return

    if "plan: str = Field(default='', max_length=32)" not in text:
        text = replace_once(
            text,
            '    plan: str = Field(min_length=1, max_length=32)\n',
            "    plan: str = Field(default='', max_length=32)\n",
            "optional renewal plan field",
        )

    old = '''    except (TypeError, ValueError) as exc:\n        reason = str(exc)\n        if reason in {\n            "renew_code_not_found",\n            "renew_code_banned",\n            "renew_device_limit_conflict",\n        }:\n            reason = "renew_code_invalid"\n        return JSONResponse(\n            status_code=400,\n            content={"ok": False, "reason": reason},\n        )\n'''
    new = '''    except (TypeError, ValueError) as exc:\n        reason = "renew_code_invalid" if mode == "renew" else str(exc)\n        return JSONResponse(\n            status_code=400,\n            content={"ok": False, "reason": reason},\n        )\n'''
    if old in text:
        text = text.replace(old, new, 1)
    elif new not in text:
        raise SystemExit("renewal error normalization block not found")

    marker = "# SKRYON_RENEW_PAYMENT_LOCK_V2\n"
    anchor = "# SKRYON_RENEW_ROUTE_LOCK_V1\n"
    if anchor in text:
        text = text.replace(anchor, anchor + marker, 1)
    else:
        text = replace_once(
            text,
            '@router.post("/api/checkout/payment")\n',
            marker + '@router.post("/api/checkout/payment")\n',
            "payment route marker",
        )
    path.write_text(text, encoding="utf-8")


def patch_yookassa() -> None:
    path = ROOT / "orchestrator" / "yookassa_checkout.py"
    text = path.read_text(encoding="utf-8")
    if "SKRYON_RENEW_PLAN_LOCK_V2" in text:
        return

    start = text.find("# SKRYON_RENEW_PLAN_LOCK_V1\n")
    end = text.find("def amount_kopeks", start)
    if start < 0 or end < 0:
        raise SystemExit("renewal resolver block not found")

    replacement = '''# SKRYON_RENEW_PLAN_LOCK_V1\n# SKRYON_RENEW_PLAN_LOCK_V2\ndef resolve_renewal_target(code: str) -> Dict[str, Any]:\n    row = get_activation_code(code)\n    if not row or str(row.get("status") or "").lower() == "banned":\n        raise ValueError("renew_code_invalid")\n\n    plan = str(row.get("plan") or "").strip()\n    selected = validate_plan(plan)\n    used_devices = int(row.get("used_devices") or 0)\n    if used_devices > int(selected["devices"]):\n        raise ValueError("renew_code_invalid")\n\n    return {\n        "code": str(row["code"]),\n        "plan": plan,\n        "selected": selected,\n    }\n\n\ndef validate_renewal_target(\n    code: str,\n    plan: Optional[str] = None,\n    enforce_device_limit: bool = False,\n) -> str:\n    target = resolve_renewal_target(code)\n    expected_plan = str(plan or "").strip()\n    if expected_plan and str(target["plan"]) != expected_plan:\n        raise ValueError("renew_plan_changed")\n    return str(target["code"])\n\n\n'''
    text = text[:start] + replacement + text[end:]
    path.write_text(text, encoding="utf-8")


def verify() -> None:
    checkout = (ROOT / "orchestrator" / "checkout_routes.py").read_text(encoding="utf-8")
    payment = (ROOT / "orchestrator" / "payment_routes.py").read_text(encoding="utf-8")
    yookassa = (ROOT / "orchestrator" / "yookassa_checkout.py").read_text(encoding="utf-8")

    assert "SKRYON_RENEW_SERVER_PLAN_V1" in checkout
    a = checkout.index("@router.post('/api/checkout/find-code')")
    b = checkout.index("@router.post('/api/checkout/renew-code')", a)
    lookup = checkout[a:b]
    assert "get_activation_code" not in lookup
    assert "'not_available'" in lookup

    assert "SKRYON_RENEW_PAYMENT_LOCK_V2" in payment
    assert "plan: str = Field(default='', max_length=32)" in payment
    assert 'reason = "renew_code_invalid" if mode == "renew" else str(exc)' in payment

    assert "SKRYON_RENEW_PLAN_LOCK_V2" in yookassa
    assert 'safe_plan = str(target["plan"])' in yookassa
    assert 'raise ValueError("renew_plan_changed")' in yookassa
    assert "renew_activation_code(" in yookassa


if __name__ == "__main__":
    patch_checkout()
    patch_payment()
    patch_yookassa()
    verify()
    print("LIVE_RENEWAL_V2_COMPAT_OK")

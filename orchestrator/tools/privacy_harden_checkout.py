from pathlib import Path
import shutil
import sqlite3
from datetime import datetime, timezone

ROOT = Path('/opt/nt/orchestrator')
PAY = ROOT / 'yookassa_checkout.py'
STORAGE = ROOT / 'storage.py'
DB = ROOT / 'data' / 'emery.db'
STAMP = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')

for path in (PAY, STORAGE):
    shutil.copy2(path, path.with_name(path.name + f'.before-privacy-{STAMP}.bak'))

def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count == 0:
        if new in text:
            print(f'{label}: already applied')
            return text
        raise SystemExit(f'{label}: expected block not found; stopped without guessing')
    if count != 1:
        raise SystemExit(f'{label}: expected 1 match, got {count}; stopped')
    print(f'{label}: patched')
    return text.replace(old, new, 1)

s = PAY.read_text(encoding='utf-8')

s = replace_once(
    s,
    '        "email_error",\n    }',
    '        "email_error",\n        "email",\n        "fulfilled_at",\n    }',
    'allow privacy fields',
)

s = replace_once(
    s,
    '''    _update_order(\n        str(order["order_id"]),\n        email_sent_at=_now_iso(),\n        email_error="",\n    )''',
    '''    _update_order(\n        str(order["order_id"]),\n        email_sent_at=_now_iso(),\n        email_error="",\n        email="",\n        code=None,\n    )''',
    'scrub email and payment code after mail',
)

s = replace_once(
    s,
    '''    if order.get("status") == "succeeded" and order.get("code"):\n        _send_activation_email(order)\n        return get_payment_order(order_id) or order''',
    '''    if order.get("status") == "succeeded" and order.get("fulfilled_at"):\n        if order.get("code") and not order.get("email_sent_at"):\n            _send_activation_email(order)\n        return get_payment_order(order_id) or order''',
    'prevent duplicate code issuance',
)

s = replace_once(
    s,
    '''                str(current["email"]),\n                order_id,''',
    '''                "",\n                order_id,''',
    'stop copying email into activation tables',
)

s = replace_once(
    s,
    '''                paid_at=_now_iso(),\n                email_error="",''',
    '''                paid_at=_now_iso(),\n                fulfilled_at=_now_iso(),\n                email_error="",''',
    'mark fulfillment before delivery',
)

s = replace_once(
    s,
    '''    succeeded = order.get("status") == "succeeded" and bool(order.get("code"))\n''',
    '',
    'remove public code condition',
)

s = replace_once(
    s,
    '''        "code": str(order.get("code") or "") if succeeded else "",''',
    '''        "code": "",''',
    'never expose activation code publicly',
)

PAY.write_text(s, encoding='utf-8')

st = STORAGE.read_text(encoding='utf-8')
st = replace_once(
    st,
    "    add_event('code_created', f'Code {code} created: {plan}, devices {safe_max_devices}', code, plan)",
    "    add_event('code_created', f'Activation code created: {plan}, devices {safe_max_devices}', '', plan)",
    'remove manual code from event log',
)
st = replace_once(
    st,
    "    add_event('checkout_code_created', f'Checkout issued {code}: {plan}, devices {safe_max_devices}', code, plan)",
    "    add_event('checkout_code_created', f'Checkout activation issued: {plan}, devices {safe_max_devices}', '', plan)",
    'remove checkout code from event log',
)
st = replace_once(
    st,
    "    add_event('checkout_code_renewed', f'Checkout renewed {formatted}: {plan}, devices {safe_max_devices}', formatted, plan)",
    "    add_event('checkout_code_renewed', f'Checkout activation renewed: {plan}, devices {safe_max_devices}', '', plan)",
    'remove renewed code from event log',
)
STORAGE.write_text(st, encoding='utf-8')

print('source patches written')

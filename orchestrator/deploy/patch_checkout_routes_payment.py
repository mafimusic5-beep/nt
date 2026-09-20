from pathlib import Path
from datetime import datetime, timezone
import re
import shutil

path = Path('/opt/nt/orchestrator/checkout_routes.py')
text = path.read_text(encoding='utf-8')
stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
backup = path.with_name(f'checkout_routes.py.pre-yookassa-{stamp}.bak')
shutil.copy2(path, backup)

payment_import = 'from payment_routes import router as payment_router\n'
if payment_import not in text:
    anchor = 'from device_recovery_routes_v2 import router as device_recovery_router\n'
    if anchor not in text:
        raise SystemExit('device recovery import anchor not found')
    text = text.replace(anchor, anchor + payment_import, 1)

payment_include = 'router.include_router(payment_router)\n'
if payment_include not in text:
    anchor = 'router.include_router(device_recovery_router)\n'
    if anchor not in text:
        raise SystemExit('device recovery router anchor not found')
    text = text.replace(anchor, anchor + payment_include, 1)

replacements = [
    (
        r"@router\.post\('/api/checkout/get-code'\)\ndef get_code\(.*?(?=@router\.post\('/api/checkout/find-code'\))",
        "@router.post('/api/checkout/get-code')\n"
        "def get_code(payload: CheckoutRequest, request: Request, x_checkout_secret: str = Header(default='')):\n"
        "    return JSONResponse(\n"
        "        status_code=410,\n"
        "        content={'ok': False, 'reason': 'legacy_direct_issuance_disabled'},\n"
        "    )\n\n\n",
    ),
    (
        r"@router\.post\('/api/checkout/renew-code'\)\ndef renew_code\(.*?(?=@router\.post\('/api/checkout/callback'\))",
        "@router.post('/api/checkout/renew-code')\n"
        "def renew_code(payload: RenewCodeRequest, request: Request, x_checkout_secret: str = Header(default='')):\n"
        "    return JSONResponse(\n"
        "        status_code=410,\n"
        "        content={'ok': False, 'reason': 'legacy_direct_issuance_disabled'},\n"
        "    )\n\n\n",
    ),
    (
        r"@router\.post\('/api/checkout/callback'\)\ndef callback\(.*?(?=@router\.get\('/api/checkout/order/\{order_id\}'\))",
        "@router.post('/api/checkout/callback')\n"
        "def callback(payload: CheckoutCallbackRequest, x_checkout_secret: str = Header(default='')):\n"
        "    return JSONResponse(\n"
        "        status_code=410,\n"
        "        content={'ok': False, 'reason': 'legacy_callback_disabled'},\n"
        "    )\n\n\n",
    ),
]

for pattern, replacement in replacements:
    text, count = re.subn(pattern, replacement, text, count=1, flags=re.S)
    if count != 1:
        raise SystemExit(f'patch target not found: {pattern}')

path.write_text(text, encoding='utf-8')
print(f'patched={path}')
print(f'backup={backup}')
print('payment_import=', payment_import.strip() in text)
print('payment_include=', payment_include.strip() in text)
print('legacy_direct_disabled=', text.count('legacy_direct_issuance_disabled'))
print('legacy_callback_disabled=', text.count('legacy_callback_disabled'))

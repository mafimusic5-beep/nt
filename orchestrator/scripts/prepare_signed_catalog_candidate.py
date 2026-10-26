#!/usr/bin/env python3
"""Prepare a surgical, reviewable candidate from the *deployed* backend.

Never overwrites live files; preserves locally modified production api.py.
"""
from __future__ import annotations

import argparse
from pathlib import Path


def change_once(text: str, before: str, after: str, label: str) -> str:
    count = text.count(before)
    if count != 1:
        raise RuntimeError(f"{label}: expected one anchor, found {count}")
    return text.replace(before, after, 1)


def patch_api(original: str) -> str:
    if "/api/vpn/servers/authorized" in original:
        raise RuntimeError("API already has the authorized catalog; inspect manually")
    source = change_once(
        original, "from device_auth import (",
        "from client_version_policy import optional_lease_version, public_listing_disabled, signed_version\n"
        "from device_auth import (", "client-version import",
    )
    source = change_once(
        source,
        "def vpn_servers():\n    upstream = ",
        """def vpn_servers():
    # Legacy list stays enabled until Android + Windows ship their v2 clients.
    if public_listing_disabled():
        return JSONResponse(
            status_code=403,
            content={'ok': False, 'reason': 'authenticated_server_list_required'},
        )
    return _public_pool_server_list()


def _public_pool_server_list():
    upstream = """,
        "legacy public listing wrapper",
    )
    catalog = '''@app.get('/api/vpn/servers/authorized')
def vpn_servers_authorized(request: Request):
    try:
        client_version = signed_version(request)
        authenticate_registered_device(
            raw_code=_bearer_code(request),
            method='GET',
            path=request.url.path,
            device_id=_header(request, 'x-emery-device-id'),
            timestamp=_header(request, 'x-emery-timestamp'),
            nonce=_header(request, 'x-emery-nonce'),
            signature_base64=_header(request, 'x-emery-signature'),
            signature_algorithm=_header(request, 'x-emery-signature-algorithm'),
            app_version_code=client_version,
        )
    except DeviceAuthError as error:
        return _auth_error(error)
    return _public_pool_server_list()


'''
    source = change_once(
        source, "@app.post('/api/device-gate/authorize')",
        catalog + "@app.post('/api/device-gate/authorize')",
        "signed listing route",
    )
    anchors = (
        "@app.post('/api/vpn/session/acquire')",
        "@app.post('/api/vpn/session/heartbeat')",
        "@app.post('/api/vpn/session/release')",
    )
    for name in anchors:
        start = source.find(name)
        if start < 0:
            raise RuntimeError("Missing lease route: " + name)
        end = source.find("\n\n@app.", start + len(name))
        if end < 0:
            raise RuntimeError("Cannot isolate lease route: " + name)
        section = source[start:end]
        section = change_once(
            section,
            "signature_algorithm=_header(request, 'x-emery-signature-algorithm'),\n        )",
            "signature_algorithm=_header(request, 'x-emery-signature-algorithm'),\n"
            "            app_version_code=optional_lease_version(request),\n        )",
            "versioned lease request " + name,
        )
        source = source[:start] + section + source[end:]
    return source


def patch_auth(original: str) -> str:
    if "app_version_code: Optional[int]" in original:
        raise RuntimeError("Device authentication already patched; inspect manually")
    source = original
    # Preserve upstream and local changes outside the canonical-signature function.
    start = source.find("def _request_canonical(")
    end = source.find("\n\ndef _gateway_canonical(", start)
    if start < 0 or end < 0:
        raise RuntimeError("Cannot isolate v1 canonical signer")
    old = source[start:end]
    for required in ("def _request_canonical(", "auth_sha256=", "timestamp=", "nonce="):
        if required not in old:
            raise RuntimeError("Unexpected canonical signer structure: " + required)
    updated = '''def _request_canonical(
    *,
    method: str,
    path: str,
    raw_code: str,
    device_id: str,
    timestamp: str,
    nonce: str,
    app_version_code: Optional[int] = None,
) -> str:
    fields = [
        f'method={method.strip().upper()}',
        f'path={path}',
        f'device_id={device_id}',
        f'timestamp={timestamp}',
        f'nonce={nonce}',
        f'auth_sha256={_auth_hash(raw_code)}',
    ]
    if app_version_code is not None:
        if not isinstance(app_version_code, int) or app_version_code < 1:
            raise DeviceAuthError('client_proof_upgrade_required', 426)
        fields.append(f'app_version_code={app_version_code}')
    return '\\n'.join(fields)
'''
    source = source[:start] + updated + source[end:]

    start = source.find("def authenticate_registered_device(")
    end = source.find("\n\ndef acquire_vpn_session(", start)
    if start < 0 or end < 0:
        raise RuntimeError("Cannot isolate the device authentication function")
    section = source[start:end]
    section = change_once(
        section,
        "    device_name: Optional[str] = None,\n) -> Dict[str, Any]:",
        "    device_name: Optional[str] = None,\n"
        "    app_version_code: Optional[int] = None,\n) -> Dict[str, Any]:",
        "bound auth signature",
    )
    section = change_once(
        section,
        "                nonce=nonce,\n            )\n        else:",
        "                nonce=nonce,\n"
        "                app_version_code=app_version_code,\n            )\n        else:",
        "versioned canonical call",
    )
    source = source[:start] + section + source[end:]
    # Preserve platform fidelity, otherwise new Windows devices show as Android.
    start = source.find("def register_device(")
    end = source.find("\n\ndef authenticate_registered_device(", start)
    if start < 0 or end < 0:
        raise RuntimeError("Cannot isolate device registration")
    section = source[start:end]
    section = change_once(
        section, "    safe_device_name = _public_device_name(signed_device_name)",
        "    safe_device_name = _public_device_name(signed_device_name)\n"
        "    safe_platform = platform.strip().lower()\n"
        "    if safe_platform not in ('android', 'windows'):\n"
        "        raise DeviceAuthError('unsupported_client_platform', 400)",
        "platform validation",
    )
    if section.count("                    'android',") != 2:
        raise RuntimeError("Unexpected hard-coded registration platforms")
    section = section.replace("                    'android',", "                    safe_platform,")
    return source[:start] + section + source[end:]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    args = parser.parse_args()
    source = args.source.resolve()
    output = args.output.resolve()
    if source == output:
        raise SystemExit("Refusing to modify the live source in place")
    for name in ("api.py", "device_auth.py"):
        if not (source / name).is_file():
            raise SystemExit(f"Missing input: {source / name}")
    policy_text = args.policy.read_text(encoding="utf-8")
    if "def signed_version(" not in policy_text:
        raise SystemExit("Version policy source is incomplete")
    api = patch_api((source / "api.py").read_text(encoding="utf-8"))
    auth = patch_auth((source / "device_auth.py").read_text(encoding="utf-8"))
    # Compile in memory before writing candidate files.
    compile(api, str(output / "api.py"), "exec")
    compile(auth, str(output / "device_auth.py"), "exec")
    compile(policy_text, str(output / "client_version_policy.py"), "exec")
    output.mkdir(parents=True, exist_ok=True)
    (output / "api.py").write_text(api, encoding="utf-8")
    (output / "device_auth.py").write_text(auth, encoding="utf-8")
    (output / "client_version_policy.py").write_text(policy_text, encoding="utf-8")
    print(f"CANDIDATE_OK: {output}")
    print(f"Preserved production API lines: {api.count(chr(10))} lines")
    print("No production files were changed.")


if __name__ == "__main__":
    main()

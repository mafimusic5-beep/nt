from __future__ import annotations

import re
import shlex


def parse_key_values(text: str) -> dict[str, str]:
    """Parse command arguments like: key=value name="Netherlands 1" config=vless://..."""
    parts = shlex.split(text.strip())
    out: dict[str, str] = {}
    for part in parts:
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        key = key.strip().lower().replace("-", "_")
        value = value.strip()
        if key:
            out[key] = value
    return out


def extract_first_proxy_link(text: str) -> str:
    match = re.search(r"((?:vless|vmess|trojan)://\S+)", text.strip(), flags=re.IGNORECASE)
    return match.group(1).strip() if match else ""


def endpoint_from_proxy_link(link: str) -> str:
    # vless://uuid@host:443?...
    try:
        after_at = link.split("@", 1)[1]
        host_port = after_at.split("?", 1)[0].split("#", 1)[0]
        return host_port.rsplit(":", 1)[0]
    except Exception:
        return ""

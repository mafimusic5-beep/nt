from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx

from src.common.config import settings


class BillManagerError(RuntimeError):
    pass


class BillManagerAuthError(BillManagerError):
    pass


class BillManagerInsufficientFundsError(BillManagerError):
    pass


@dataclass(slots=True)
class FirstVdsOrderResult:
    status: str
    vps_id: str | None = None
    endpoint: str | None = None
    raw: dict[str, Any] | None = None


class FirstVdsBillManagerClient:
    def __init__(self) -> None:
        self.base_url = settings.firstvds_billmgr_url.rstrip("?")
        self.username = settings.firstvds_login
        self.password = settings.firstvds_password
        self.verify_ssl = settings.firstvds_verify_ssl
        self.timeout = settings.firstvds_timeout_seconds

    @staticmethod
    def _sanitize_url(url: str) -> str:
        import re
        return re.sub(r"(password|passwd|token|auth)=[^&\s]*", r"\1=***", str(url), flags=re.IGNORECASE)

    def _get(self, params: dict[str, Any]) -> dict[str, Any]:
        try:
            with httpx.Client(timeout=self.timeout, verify=self.verify_ssl) as client:
                response = client.get(self.base_url, params={"out": "json", **params})
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise BillManagerError(f"http_{exc.response.status_code}") from None
        except httpx.HTTPError as exc:
            safe_msg = self._sanitize_url(str(exc))
            raise BillManagerError(f"request_failed: {safe_msg}") from None
        data = response.json()
        return self._handle_billmgr_response(data)

    def _handle_billmgr_response(self, data: dict[str, Any]) -> dict[str, Any]:
        doc = data.get("doc")
        if not isinstance(doc, dict):
            raise BillManagerError(f"unexpected_billmgr_response:{json.dumps(data, ensure_ascii=False)[:600]}")
        if "error" in doc:
            msg = self._extract_error_message(doc["error"])
            lowered = msg.lower()
            if "badpassword" in lowered or "auth" in lowered:
                raise BillManagerAuthError(msg)
            if "insufficient" in lowered or "not enough" in lowered or "недостат" in lowered or "баланс" in lowered:
                raise BillManagerInsufficientFundsError(msg)
            raise BillManagerError(msg)
        return data

    @staticmethod
    def _extract_error_message(error_block: Any) -> str:
        if isinstance(error_block, dict):
            msg = error_block.get("msg")
            if isinstance(msg, dict):
                return str(msg.get("$", "billmgr_error"))
            return str(msg or error_block)
        return str(error_block)

    def authenticate(self) -> str:
        payload = self._get({"func": "auth", "username": self.username, "password": self.password})
        auth_block = payload.get("doc", {}).get("auth", {})
        session_id = auth_block.get("$id")
        if not session_id:
            raise BillManagerAuthError("session_id_not_returned")
        return str(session_id)

    def request(self, func: str, **params: Any) -> dict[str, Any]:
        auth = self.authenticate()
        return self._get({"auth": auth, "func": func, **params})

    def test_connection(self) -> dict[str, Any]:
        payload = self.request("vds")
        items = payload.get("doc", {}).get("elem", [])
        return {"status": "ok", "services_visible": len(items)}

    def list_vds(self) -> list[dict[str, str]]:
        payload = self.request("vds")
        items = payload.get("doc", {}).get("elem", [])
        return [self._normalize_vds_item(item) for item in items]

    def get_vds(self, vps_id: str) -> dict[str, str] | None:
        for item in self.list_vds():
            if item.get("id") == str(vps_id):
                return item
        return None

    def find_vds_by_domain(self, domain: str) -> dict[str, str] | None:
        for item in self.list_vds():
            if item.get("domain") == domain:
                return item
        return None

    def order_vds(self, *, domain: str, datacenter: str, pricelist: str, ostempl: str, period: str = "1", recipe: str = "null", itemtype: str = "3", skipbasket: bool = True, addons: dict[str, str] | None = None) -> FirstVdsOrderResult:
        params: dict[str, Any] = {
            "elid": "",
            "domain": domain,
            "datacenter": datacenter,
            "itemtype": itemtype,
            "period": period,
            "pricelist": pricelist,
            "skipbasket": "on" if skipbasket else "off",
            "ostempl": ostempl,
            "recipe": recipe,
            "licence_agreement": "on",
            "sok": "ok",
        }
        for key, value in (addons or {}).items():
            params[key] = value
        raw = self.request("vds.order.param", **params)
        found = self.find_vds_by_domain(domain)
        return FirstVdsOrderResult(
            status="ok",
            vps_id=found.get("id") if found else None,
            endpoint=found.get("ip") if found else None,
            raw=raw,
        )

    def delete_vds(self, vps_id: str) -> dict[str, Any]:
        return self.request("vds.delete", elid=vps_id)

    def set_service_password(self, vps_id: str, new_password: str) -> dict[str, Any]:
        return self.request("service.set_password", elid=vps_id, passwd=new_password, confirm=new_password, sok="ok")

    def get_service_password_state(self, vps_id: str) -> dict[str, Any]:
        return self.request("service.set_password.state", elid=vps_id)

    @staticmethod
    def _normalize_vds_item(item: dict[str, Any]) -> dict[str, str]:
        out: dict[str, str] = {}
        for key, value in item.items():
            if isinstance(value, dict) and "$" in value:
                out[key] = str(value["$"])
            elif isinstance(value, str):
                out[key] = value
            else:
                out[key] = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
        return out

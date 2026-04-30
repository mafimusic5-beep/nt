from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from src.common.config import settings


class BackendClientError(Exception):
    def __init__(self, detail: str, status_code: int = 500):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


@dataclass(slots=True)
class BackendClient:
    base_url: str = settings.backend_base_url.rstrip("/")
    internal_api_key: str = settings.internal_api_key
    admin_api_key: str = settings.admin_api_key

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_data: dict | None = None,
        params: dict | None = None,
        headers: dict | None = None,
    ) -> Any:
        url = f"{self.base_url}{path}"
        req_headers = headers or {}
        async with httpx.AsyncClient(timeout=20.0) as client:
            try:
                response = await client.request(method, url, json=json_data, params=params, headers=req_headers)
            except httpx.HTTPError as exc:
                raise BackendClientError("backend_unreachable", 503) from exc
        payload = {}
        if response.content:
            try:
                payload = response.json()
            except ValueError:
                payload = {"detail": "invalid_backend_payload"}
        if response.status_code >= 400:
            detail = payload.get("detail") if isinstance(payload, dict) else "backend_error"
            raise BackendClientError(str(detail), response.status_code)
        return payload

    async def get_subscription_status(self, telegram_id: int) -> dict:
        return await self._request("GET", "/api/v1/subscription/status", params={"telegram_id": telegram_id})

    async def get_user_devices(self, telegram_id: int) -> list[dict]:
        return await self._request("GET", "/api/v1/user/devices", params={"telegram_id": telegram_id})

    async def get_vpn_config(self, telegram_id: int) -> dict:
        return await self._request("GET", "/api/v1/vpn/config", params={"telegram_id": telegram_id})

    async def get_user_codes(self, telegram_id: int) -> list[dict]:
        return await self._request("GET", "/api/v1/user/codes", params={"telegram_id": telegram_id})

    async def create_order(self, telegram_id: int, plan_code: str) -> dict:
        return await self._request(
            "POST",
            "/api/v1/internal/orders",
            json_data={"telegram_id": telegram_id, "plan_code": plan_code},
            headers={"X-Internal-Api-Key": self.internal_api_key},
        )

    async def confirm_payment(self, order_id: int, provider_payment_id: str, idempotency_key: str) -> dict:
        return await self._request(
            "POST",
            "/api/v1/internal/payments/confirm",
            json_data={
                "order_id": order_id,
                "provider_payment_id": provider_payment_id,
                "idempotency_key": idempotency_key,
                "paid": True,
            },
            headers={"X-Internal-Api-Key": self.internal_api_key},
        )

    async def admin_stats(self) -> dict:
        return await self._request("GET", "/api/v1/admin/stats", headers={"X-Admin-Api-Key": self.admin_api_key})

    async def admin_nodes(self) -> list[dict]:
        return await self._request("GET", "/api/v1/admin/nodes", headers={"X-Admin-Api-Key": self.admin_api_key})

    async def admin_create_node(self, payload: dict) -> dict:
        return await self._request(
            "POST",
            "/api/v1/admin/nodes",
            json_data=payload,
            headers={"X-Admin-Api-Key": self.admin_api_key},
        )

    async def admin_capacity(self) -> dict:
        return await self._request("GET", "/api/v1/admin/capacity", headers={"X-Admin-Api-Key": self.admin_api_key})

    async def admin_capacity_alert(self) -> dict:
        return await self._request("GET", "/api/v1/admin/capacity/alert", headers={"X-Admin-Api-Key": self.admin_api_key})

    async def admin_grant_subscription(self, telegram_id: int, months: int) -> dict:
        return await self._request(
            "POST",
            "/api/v1/admin/subscription/grant",
            json_data={"telegram_id": telegram_id, "months": months, "region_code": settings.default_region_code},
            headers={"X-Admin-Api-Key": self.admin_api_key},
        )

    async def admin_generate_code(self, telegram_id: int) -> dict:
        return await self._request(
            "POST",
            "/api/v1/admin/codes/generate",
            params={"telegram_id": telegram_id},
            headers={"X-Admin-Api-Key": self.admin_api_key},
        )

    async def admin_problem_activations(self) -> list[dict]:
        return await self._request(
            "GET",
            "/api/v1/admin/activations/problems",
            headers={"X-Admin-Api-Key": self.admin_api_key},
        )

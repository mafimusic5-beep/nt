from __future__ import annotations

import logging
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from src.common.config import settings

logger = logging.getLogger(__name__)

_LEGACY_CODE_GROUPS = (1, 3, 2, 2, 2, 1)
_DEFAULT_LEGACY_DATABASE_PATH = "/opt/nt/orchestrator/skryon.db"


def _format_legacy_activation_code(value: str) -> str:
    normalized = "".join(ch for ch in str(value).upper() if ch.isalnum())
    if len(normalized) != sum(_LEGACY_CODE_GROUPS):
        raise ValueError("invalid_activation_code_length")
    parts: list[str] = []
    index = 0
    for size in _LEGACY_CODE_GROUPS:
        parts.append(normalized[index:index + size])
        index += size
    return "-".join(parts)


def _mirror_activation_code_to_legacy(code: str, subscription_status: dict) -> None:
    """Keep the website activation endpoint compatible with modern code issuance.

    The Android client still activates through the legacy `/api/activate` endpoint,
    whose SQLite store keeps the formatted plaintext code. Modern backend storage
    intentionally keeps only a SHA-256 hash, so every code shown by the bot must be
    mirrored before it is returned to the admin.
    """

    formatted = _format_legacy_activation_code(code)
    expires_at = str(subscription_status.get("ends_at") or "").strip()
    plan = str(subscription_status.get("plan_code") or "manual").strip()[:64] or "manual"
    try:
        max_devices = int(subscription_status.get("devices_limit") or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid_subscription_device_limit") from exc
    if not expires_at:
        raise ValueError("subscription_expiry_missing")
    if max_devices < 1 or max_devices > 20:
        raise ValueError("invalid_subscription_device_limit")

    db_path = Path(
        os.getenv("SKRYON_LEGACY_DATABASE_PATH", _DEFAULT_LEGACY_DATABASE_PATH).strip()
        or _DEFAULT_LEGACY_DATABASE_PATH
    )
    if not db_path.is_file():
        raise FileNotFoundError("legacy_activation_database_missing")

    created_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    with sqlite3.connect(db_path, timeout=10.0) as con:
        table_exists = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='activation_codes'"
        ).fetchone()
        if not table_exists:
            raise RuntimeError("legacy_activation_table_missing")
        con.execute(
            """
            INSERT INTO activation_codes(
                code, status, note, created_at, expires_at, max_devices, plan
            ) VALUES (?, 'active', ?, ?, ?, ?, ?)
            ON CONFLICT(code) DO UPDATE SET
                status = 'active',
                note = excluded.note,
                expires_at = excluded.expires_at,
                max_devices = excluded.max_devices,
                plan = excluded.plan
            """,
            (
                formatted,
                "modern-subscription",
                created_at,
                expires_at,
                max_devices,
                plan,
            ),
        )
        con.commit()


class BackendClientError(Exception):
    def __init__(
        self,
        detail: str,
        status_code: int = 500,
        *,
        method: str = "",
        path: str = "",
        base_url: str = "",
    ):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code
        self.method = method
        self.path = path
        self.base_url = base_url


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
        timeout_seconds: float = 20.0,
        base_url: str | None = None,
    ) -> Any:
        target_base_url = (base_url or self.base_url).rstrip("/")
        url = f"{target_base_url}{path}"
        req_headers = headers or {}
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            try:
                response = await client.request(method, url, json=json_data, params=params, headers=req_headers)
            except httpx.HTTPError as exc:
                # Never log request payloads or headers here: /setup_server carries
                # a root password and admin calls carry API keys.
                logger.error(
                    "backend request failed method=%s path=%s base_url=%s error=%s",
                    method,
                    path,
                    target_base_url,
                    type(exc).__name__,
                )
                raise BackendClientError(
                    "backend_unreachable",
                    503,
                    method=method,
                    path=path,
                    base_url=target_base_url,
                ) from exc
        payload = {}
        if response.content:
            try:
                payload = response.json()
            except ValueError:
                payload = {"detail": "invalid_backend_payload"}
        if response.status_code >= 400:
            detail = payload.get("detail") if isinstance(payload, dict) else "backend_error"
            logger.warning(
                "backend rejected request method=%s path=%s base_url=%s status=%s detail=%s",
                method,
                path,
                target_base_url,
                response.status_code,
                str(detail)[:160],
            )
            raise BackendClientError(
                str(detail),
                response.status_code,
                method=method,
                path=path,
                base_url=target_base_url,
            )
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

    async def admin_bootstrap_node(self, payload: dict) -> dict:
        path = "/api/v1/admin/nodes/bootstrap"
        request_kwargs = {
            "json_data": payload,
            "headers": {"X-Admin-Api-Key": self.admin_api_key},
            "timeout_seconds": 300.0,
        }
        try:
            return await self._request("POST", path, **request_kwargs)
        except BackendClientError as exc:
            local_backend = "http://127.0.0.1:9330"
            # Production bot and modern backend run on the same control VPS. If
            # an old BACKEND_BASE_URL still points at the legacy compatibility
            # API, a 404 here should not block VPS provisioning.
            if exc.status_code != 404 or self.base_url.rstrip("/") == local_backend:
                raise
            logger.warning(
                "bootstrap endpoint missing on configured backend; retrying local modern backend configured=%s fallback=%s",
                self.base_url,
                local_backend,
            )
            return await self._request(
                "POST",
                path,
                **request_kwargs,
                base_url=local_backend,
            )

    async def admin_disable_node(self, node_id: int) -> dict:
        return await self._request(
            "POST",
            f"/api/v1/admin/nodes/{node_id}/disable",
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
        path = "/api/v1/admin/codes/generate"
        result = await self._request(
            "POST",
            path,
            params={"telegram_id": telegram_id},
            headers={"X-Admin-Api-Key": self.admin_api_key},
        )
        try:
            subscription_status = await self.get_subscription_status(telegram_id)
            if not bool(subscription_status.get("active")):
                raise RuntimeError("subscription_inactive_after_code_generation")
            _mirror_activation_code_to_legacy(
                str(result.get("activation_code") or ""),
                subscription_status,
            )
        except BackendClientError:
            raise
        except Exception as exc:
            # Never log the plaintext activation code.
            logger.error(
                "activation code compatibility sync failed telegram_id=%s error=%s",
                telegram_id,
                type(exc).__name__,
            )
            raise BackendClientError(
                "activation_code_compat_sync_failed",
                503,
                method="POST",
                path=path,
                base_url=self.base_url,
            ) from exc
        return result

    async def admin_problem_activations(self) -> list[dict]:
        return await self._request(
            "GET",
            "/api/v1/admin/activations/problems",
            headers={"X-Admin-Api-Key": self.admin_api_key},
        )

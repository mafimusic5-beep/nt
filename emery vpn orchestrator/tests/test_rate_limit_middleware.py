from __future__ import annotations

import asyncio

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response

from src.backend.middleware.rate_limit import RateLimitMiddleware


def _request(path: str, client_host: str) -> Request:
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "server": ("testserver", 80),
        "client": (client_host, 12345),
        "scheme": "http",
        "method": "GET",
        "root_path": "",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "headers": [],
    }
    return Request(scope)


async def _ok(_: Request) -> Response:
    return Response(status_code=200)


def _dispatch(middleware: RateLimitMiddleware, path: str, client_host: str) -> Response:
    return asyncio.run(middleware.dispatch(_request(path, client_host), _ok))


def _middleware_with_limit(limit: int = 1) -> RateLimitMiddleware:
    middleware = RateLimitMiddleware(Starlette())
    middleware.limit_per_minute = limit
    return middleware


def test_local_internal_and_admin_requests_are_not_rate_limited() -> None:
    middleware = _middleware_with_limit()

    for path in ("/api/v1/internal/pool/assignments/prepare", "/api/v1/admin/nodes"):
        assert _dispatch(middleware, path, "127.0.0.1").status_code == 200
        assert _dispatch(middleware, path, "127.0.0.1").status_code == 200


def test_external_internal_request_remains_rate_limited() -> None:
    middleware = _middleware_with_limit()
    path = "/api/v1/internal/pool/assignments/prepare"

    assert _dispatch(middleware, path, "203.0.113.10").status_code == 200
    assert _dispatch(middleware, path, "203.0.113.10").status_code == 429


def test_public_loopback_request_remains_rate_limited() -> None:
    middleware = _middleware_with_limit()
    path = "/api/config/sync"

    assert _dispatch(middleware, path, "127.0.0.1").status_code == 200
    assert _dispatch(middleware, path, "127.0.0.1").status_code == 429

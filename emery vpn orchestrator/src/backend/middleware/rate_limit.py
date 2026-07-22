import hashlib
import logging
import time
from collections import defaultdict, deque

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from src.common.config import settings

logger = logging.getLogger(__name__)


def _privacy_key(request: Request) -> str:
    """Create an opaque rate-limit bucket without reading the remote address."""
    identity = (
        request.headers.get("x-emery-device-id")
        or request.headers.get("authorization")
        or request.headers.get("x-internal-api-key")
        or request.headers.get("x-admin-api-key")
        or "anonymous"
    )
    material = f"{request.method}:{request.url.path}:{identity}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app) -> None:
        super().__init__(app)
        self.limit_per_minute = max(1, int(settings.rate_limit_per_minute))
        self.window_seconds = 60
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    async def dispatch(self, request: Request, call_next) -> Response:
        key = _privacy_key(request)
        now = time.time()
        bucket = self._hits[key]
        while bucket and (now - bucket[0]) > self.window_seconds:
            bucket.popleft()
        if len(bucket) >= self.limit_per_minute:
            logger.warning("rate limit hit for route=%s", request.url.path)
            return JSONResponse(status_code=429, content={"detail": "rate_limit_exceeded"})
        bucket.append(now)
        return await call_next(request)

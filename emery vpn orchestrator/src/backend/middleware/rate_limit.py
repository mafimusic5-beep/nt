import logging
import time
from collections import defaultdict, deque

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from src.common.config import settings

logger = logging.getLogger(__name__)


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app) -> None:
        super().__init__(app)
        self.limit_per_minute = max(1, int(settings.rate_limit_per_minute))
        self.window_seconds = 60
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path
        key = request.client.host if request.client else "unknown"

        if path in {"/api/v1/health", "/api/v1/ready"}:
            return await call_next(request)

        is_loopback = key in {"127.0.0.1", "::1"}
        is_privileged_path = path.startswith("/api/v1/internal/") or path.startswith("/api/v1/admin/")
        if is_loopback and is_privileged_path:
            return await call_next(request)

        now = time.time()
        bucket = self._hits[key]
        while bucket and (now - bucket[0]) > self.window_seconds:
            bucket.popleft()
        if len(bucket) >= self.limit_per_minute:
            logger.warning("rate limit hit for %s (%d/%d)", key, len(bucket), self.limit_per_minute)
            return JSONResponse(status_code=429, content={"detail": "rate_limit_exceeded"})
        bucket.append(now)
        return await call_next(request)

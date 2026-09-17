import hashlib
import hmac
import logging
import secrets
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
        self._pseudonym_key = secrets.token_bytes(32)

    def _bucket_key(self, client_host: str) -> str:
        return hmac.new(
            self._pseudonym_key,
            client_host.encode("utf-8", errors="ignore"),
            hashlib.sha256,
        ).hexdigest()

    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path
        client_host = request.client.host if request.client else "unknown"

        if path in {"/api/v1/health", "/api/v1/ready"}:
            return await call_next(request)

        is_loopback = client_host in {"127.0.0.1", "::1"}
        is_privileged_path = path.startswith("/api/v1/internal/") or path.startswith("/api/v1/admin/")
        if is_loopback and is_privileged_path:
            return await call_next(request)

        now = time.time()
        bucket = self._hits[self._bucket_key(client_host)]
        while bucket and (now - bucket[0]) > self.window_seconds:
            bucket.popleft()
        if len(bucket) >= self.limit_per_minute:
            logger.warning(
                "rate limit hit path=%s (%d/%d)",
                path,
                len(bucket),
                self.limit_per_minute,
            )
            return JSONResponse(status_code=429, content={"detail": "rate_limit_exceeded"})
        bucket.append(now)
        return await call_next(request)

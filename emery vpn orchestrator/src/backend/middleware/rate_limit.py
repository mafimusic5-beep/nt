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
        key = request.client.host if request.client else "unknown"
        now = time.time()
        bucket = self._hits[key]
        while bucket and (now - bucket[0]) > self.window_seconds:
            bucket.popleft()
        if len(bucket) >= self.limit_per_minute:
            logger.warning("rate limit hit for %s (%d/%d)", key, len(bucket), self.limit_per_minute)
            return JSONResponse(status_code=429, content={"detail": "rate_limit_exceeded"})
        bucket.append(now)
        return await call_next(request)

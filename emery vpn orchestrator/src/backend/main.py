from fastapi import FastAPI

from src.backend.api.compat_routes import compat_router
from src.backend.api.routes import router as api_router
from src.backend.core.bootstrap import seed_plans
from src.backend.core.healthcheck_scheduler import start_healthcheck_scheduler, stop_healthcheck_scheduler
from src.backend.core.logging import setup_logging
from src.backend.middleware.rate_limit import RateLimitMiddleware
from src.common.config import settings
from src.common.db import SessionLocal

app = FastAPI(title="Emery VPN Orchestrator Backend", version="0.1.0")
app.add_middleware(RateLimitMiddleware)
app.include_router(compat_router)
app.include_router(api_router)


@app.on_event("startup")
async def startup() -> None:
    setup_logging(settings.log_level)
    db = SessionLocal()
    try:
        seed_plans(db)
    finally:
        db.close()
    start_healthcheck_scheduler()


@app.on_event("shutdown")
async def shutdown() -> None:
    await stop_healthcheck_scheduler()


@app.get("/")
async def root() -> dict[str, str]:
    return {
        "service": "backend",
        "env": settings.app_env,
        "status": "running",
    }

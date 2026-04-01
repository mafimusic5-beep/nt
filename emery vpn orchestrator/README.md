# Emery VPN Orchestrator

Backend + Telegram bot for subscription sales and VPN access management.

## What is included

- FastAPI backend (`src/backend`)
- aiogram bot (`src/bot`)
- SQLAlchemy + Alembic migrations
- VPN node orchestration layer (FirstVDS BILLmanager + script fallback)
- Healthcheck scheduler and API health endpoints
- Example Xray/VLESS/Reality config placeholders
- Deployment examples (`systemd`, `nginx`, FirstVDS guide)

## Project structure

- `src/backend` - API routes, services, repositories
- `src/bot` - telegram handlers and backend client
- `src/common` - settings, DB base, ORM models
- `alembic` - migrations
- `tests` - pytest scenarios for critical business logic
- `deploy/systemd` - example unit files
- `deploy/nginx` - example reverse proxy config
- `docs/DEPLOY_FIRSTVDS.md` - deployment guide
- `docs/PRODUCTION_NOTES.md` - production hardening notes

## Environment

Copy `.env.example` to `.env` and set required values:

- `BOT_TOKEN`
- `INTERNAL_API_KEY`
- `ADMIN_API_KEY`
- `ADMIN_IDS`
- `BACKEND_BASE_URL`

## Run commands

### Local run

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
alembic upgrade head
uvicorn src.backend.main:app --host 0.0.0.0 --port 9330
python -m src.bot.main
```

### Docker run

```bash
docker compose up --build
```

## Migration commands

```bash
alembic upgrade head
alembic downgrade -1
alembic history
```

## Test commands

```bash
pytest -q
pytest -q tests/test_code_generation.py
pytest -q tests/test_subscription_extension.py
pytest -q tests/test_device_limit.py
pytest -q tests/test_redeem_flow.py
```

## Healthcheck

- Liveness: `GET /api/v1/health`
- Readiness (DB check): `GET /api/v1/ready`

## API overview

### User
- `POST /api/v1/redeem`
- `GET /api/v1/subscription/status`
- `POST /api/v1/device/register`
- `POST /api/v1/device/heartbeat`
- `POST /api/v1/device/unbind`
- `GET /api/v1/vpn/config`
- `GET /api/v1/user/devices`
- `GET /api/v1/user/codes`

### Internal
- `POST /api/v1/internal/orders`
- `POST /api/v1/internal/payments/confirm`

### Admin
- `POST /api/v1/admin/subscription/grant`
- `GET /api/v1/admin/stats`
- `GET /api/v1/admin/nodes`
- `POST /api/v1/admin/nodes`
- `GET /api/v1/admin/nodes/best-moscow`
- `POST /api/v1/admin/nodes/{node_id}/provision`
- `POST /api/v1/admin/nodes/{node_id}/deprovision`
- `POST /api/v1/admin/nodes/healthcheck/run`
- `POST /api/v1/admin/codes/generate`
- `GET /api/v1/admin/activations/problems`

## Business logic coverage

- Product: Warmup (`3m/6m/12m`, `1500/2700/4800 RUB`)
- Order creation + payment confirmation
- Subscription creation/extension on paid order
- One-time activation code display; hash-only storage
- Device registration limit (max 5) via backend checks
- Idempotent payment confirmation (`idempotency_key`)
- Audit logging for critical actions
- Node selection in `moscow` by `health_status -> load_score -> priority`

## FirstVDS integration notes

- Real integration path now targets **BILLmanager** via `https://my.firstvds.ru/billmgr`.
- Configure `FIRSTVDS_BILLMGR_URL`, `FIRSTVDS_LOGIN`, `FIRSTVDS_PASSWORD`, `FIRSTVDS_ALLOWED_IP`.
- Automated provisioning uses account auth + whitelisted IP and can pay from FirstVDS balance with `skipbasket=on`.
- Shell scripts remain as fallback only when BILLmanager credentials are not configured.
- See `docs/DEPLOY_FIRSTVDS.md`.

## Production notes

See `docs/PRODUCTION_NOTES.md` for:
- security hardening
- backups
- monitoring/alerts
- scaling gaps and next steps

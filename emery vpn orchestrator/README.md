# Emery VPN Orchestrator

Backend + Telegram bot for subscription sales and VPN access management.

## What is included

- FastAPI backend (`src/backend`)
- aiogram bot (`src/bot`)
- SQLAlchemy + Alembic migrations
- VPN node orchestration layer (FirstVDS BILLmanager + script fallback)
- Healthcheck scheduler and API health endpoints
- Kubernetes recovery agent for every active public VLESS node
- Two-phase legacy-to-pool reservation bridge with one UUID per device
- Non-destructive contract renewal planner
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
- `deploy/kubernetes/recovery-agent.yaml` - continuous public-node recovery worker
- `docs/DEPLOY_FIRSTVDS.md` - deployment guide
- `docs/PRODUCTION_NOTES.md` - production hardening notes

## Environment

Copy `.env.example` to `.env` and set required values:

- `BOT_TOKEN`
- `INTERNAL_API_KEY`
- `ADMIN_API_KEY`
- `ADMIN_IDS`
- `BACKEND_BASE_URL`
- `MIN_SUPPORTED_APP_VERSION_CODE=716`
- `APP_UPDATE_MESSAGE=Версия приложения устарела. Обновите приложение.`
- `POOL_BRIDGE_API_KEY=<same secret in both backends>`

Новые клиенты передают `X-Skryon-App-Version-Code`. Если переданная версия ниже минимальной, backend возвращает сообщение об обновлении вместо профиля/списка серверов. Отсутствие заголовка сохраняет старые совместимые контракты для legacy polling/event/pool-клиентов. APK, который после активации вообще не выходит в сеть, удалённо показать сообщение не сможет.

Администратор может управлять общим пулом через Telegram:

- `/addconfig <VLESS Reality ссылка>` (также `/add_config`) — добавить сервер;
- `/delconfig ID` — отключить сервер и убрать его из синхронизированных приложений;
- `/servers` — посмотреть ID серверов.

Для связи старого Skryon-бота с этим backend отдельный секрет создавать не нужно: бот
использует существующие `ADMIN_API_KEY` и `BACKEND_BASE_URL` из окружения или Emery `.env`.
Нестандартный путь к файлу можно передать через `EMERY_ENV_FILE`.

## Run commands

### Local run

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
alembic upgrade head
uvicorn src.backend.main:app --host 0.0.0.0 --port 9330
python -m src.bot.main
python -m src.backend.recovery_agent
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
- Public VPN nodes: `python -m src.backend.recovery_agent` probes every active
  VLESS listener in parallel. After three failed checks it restarts Xray on the
  same VPS, then reboots that same VPS if necessary.

The Kubernetes deployment example is in
`deploy/kubernetes/recovery-agent.yaml`. It must use the backend's same external
database and an SSH `known_hosts` secret; see
`docs/IONOS_PUBLIC_POOL_AUTOSCALING.md` before deployment.

## API overview

### User
- `POST /api/v1/redeem`
- `GET /api/v1/subscription/status`
- `POST /api/v1/device/register`
- `POST /api/v1/device/heartbeat`
- `POST /api/v1/device/unbind` — сохранён для совместимости и всегда возвращает `403 device_unbind_disabled`
- `GET /api/v1/vpn/config`
- `GET /api/v1/user/devices`
- `GET /api/v1/user/codes`

### Internal
- `POST /api/v1/internal/orders`
- `POST /api/v1/internal/payments/confirm`
- `POST /api/v1/internal/pool/assignments/prepare`
- `POST /api/v1/internal/pool/assignments/confirm`
- `POST /api/v1/internal/pool/assignments/maintenance`

### Admin
- `POST /api/v1/admin/subscription/grant`
- `GET /api/v1/admin/stats`
- `GET /api/v1/admin/nodes`
- `POST /api/v1/admin/nodes`
- `GET /api/v1/admin/nodes/best-moscow`
- `POST /api/v1/admin/nodes/{node_id}/provision`
- `POST /api/v1/admin/nodes/{node_id}/deprovision` — всегда блокирует удаление VPS
- `POST /api/v1/admin/nodes/{node_id}/disable`
- `POST /api/v1/admin/nodes/{node_id}/enable`
- `POST /api/v1/admin/nodes/healthcheck/run`
- `POST /api/v1/admin/codes/generate`
- `GET /api/v1/admin/activations/problems`
- `GET /api/v1/admin/renewals/plan`
- `POST /api/v1/admin/renewals/plan/apply`

## Business logic coverage

- VPN plans: Personal (`1 device / 200 RUB`), Personal+ (`2 / 260 RUB`),
  Family (`5 / 500 RUB`) per month; legacy Warmup plans remain readable
- Order creation + payment confirmation
- Subscription creation/extension on paid order
- One-time activation code display; hash-only storage
- Exact tariff limits (1/2/5) and immutable registered-device slots
- Unique VLESS UUID and dedicated port per device; hard 30 Mbit/s nftables cap
- Capacity gate at 20 devices and pre-emptive scale request after device 16
- Idempotent payment confirmation (`idempotency_key`)
- Audit logging for critical actions
- Node selection in `moscow` by `health_status -> load_score -> priority`
- Backward-compatible region change feed: `GET /api/v1/vpn/regions/revision` and long-poll `GET /api/v1/vpn/regions/events?since=...`

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

The agreed public-pool capacity, IONOS constraints, margin calculation and
rollout blockers are documented in `docs/IONOS_PUBLIC_POOL_AUTOSCALING.md`.

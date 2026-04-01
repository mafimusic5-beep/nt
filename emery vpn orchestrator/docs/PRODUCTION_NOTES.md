# Production Notes

## Security

- Use long random values for `INTERNAL_API_KEY` and `ADMIN_API_KEY`.
- Restrict internal/admin endpoints by network ACL and reverse proxy rules.
- Enable HTTPS (Let's Encrypt) at Nginx layer.
- Rotate bot token and API keys periodically.
- Do not log activation codes in plain text.

## Data & Backups

- Backup SQLite file (`data/app.db`) daily (or migrate to PostgreSQL before scale).
- Keep Alembic history in sync with deployed code.
- Add periodic restore test for backups.

## Operations

- Monitor:
  - `/api/v1/health` (liveness)
  - `/api/v1/ready` (readiness + DB check)
  - systemd unit health
- Set journal retention and log rotation policy.
- Add alerting for repeated `redeem_invalid_code` and `node_healthcheck` failures.

## Scaling and reliability gaps

- Current in-memory rate limiter is per-process only (not distributed).
- Scheduler is in-process; for HA use dedicated worker/queue.
- SQLite is acceptable for MVP, but PostgreSQL is recommended for production.
- Bot payment confirmation is currently simulated/manual (provider abstraction present).

## FirstVDS specifics

- Provisioning uses script adapter placeholders.
- You must implement idempotent provisioning scripts with retries and rollback.
- Ensure scripts update node metrics (`health_status`, `load_score`, `current_clients`).

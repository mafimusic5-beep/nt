# Deploy on FirstVDS (Ubuntu 22.04+)

This guide deploys backend + bot on one VPS and uses **BILLmanager** for optional node auto-provisioning.

## 1) Prepare FirstVDS account

1. Log in to `https://my.firstvds.ru`.
2. In profile settings, add the backend VPS public IP to **API access**.
3. In the BILLmanager UI, open the VDS order form and use the built-in **API** helper to discover:
   - `FIRSTVDS_ORDER_DATACENTER`
   - `FIRSTVDS_ORDER_PRICELIST`
   - `FIRSTVDS_ORDER_OSTEMPL`
   - any `addon_*` parameters that belong to your tariff
4. Make sure your account balance is positive if you want provisioning with `skipbasket=on`.

## 2) Server preparation

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip nginx git
sudo adduser --system --group --home /opt/emery_vpn_orchestrator emery
```

## 3) Deploy app code

```bash
sudo -u emery git clone <YOUR_REPO_URL> /opt/emery_vpn_orchestrator
cd /opt/emery_vpn_orchestrator
sudo -u emery python3 -m venv .venv
sudo -u emery .venv/bin/pip install -r requirements.txt
```

## 4) Configure environment

```bash
cd /opt/emery_vpn_orchestrator
sudo -u emery cp .env.example .env
```

Set required values in `.env`:
- `BOT_TOKEN`
- `INTERNAL_API_KEY`
- `ADMIN_API_KEY`
- `ADMIN_IDS`
- `BACKEND_BASE_URL`
- `FIRSTVDS_BILLMGR_URL`
- `FIRSTVDS_LOGIN`
- `FIRSTVDS_PASSWORD`
- `FIRSTVDS_ALLOWED_IP`
- `FIRSTVDS_ORDER_DATACENTER`
- `FIRSTVDS_ORDER_PRICELIST`
- `FIRSTVDS_ORDER_OSTEMPL`
- `FIRSTVDS_ORDER_ADDONS_JSON` if your tariff needs addon parameters

Keep `NODE_*_SCRIPT` only as a fallback path. If BILLmanager credentials are configured, the backend prefers automated provisioning via FirstVDS.

## 5) Run migrations

```bash
cd /opt/emery_vpn_orchestrator
sudo -u emery .venv/bin/alembic upgrade head
```

## 6) systemd services

```bash
sudo cp deploy/systemd/emery-backend.service.example /etc/systemd/system/emery-backend.service
sudo cp deploy/systemd/emery-bot.service.example /etc/systemd/system/emery-bot.service
sudo systemctl daemon-reload
sudo systemctl enable --now emery-backend
sudo systemctl enable --now emery-bot
```

Check logs:

```bash
sudo journalctl -u emery-backend -f
sudo journalctl -u emery-bot -f
```

## 7) Nginx reverse proxy

```bash
sudo cp deploy/nginx/emery-backend.conf.example /etc/nginx/sites-available/emery-backend.conf
sudo ln -s /etc/nginx/sites-available/emery-backend.conf /etc/nginx/sites-enabled/emery-backend.conf
sudo nginx -t
sudo systemctl reload nginx
```

## 8) Health checks

```bash
curl -fsS http://127.0.0.1:9330/api/v1/health
curl -fsS http://127.0.0.1:9330/api/v1/ready
```

## 9) FirstVDS integration notes

- This project now prefers **BILLmanager auth flow** over a fictional token-only API.
- Requests go to `FIRSTVDS_BILLMGR_URL` with account credentials.
- `skipbasket=on` is used to pay from the FirstVDS account balance when auto-ordering a node.
- If the backend cannot authenticate or the balance is insufficient, provisioning returns a structured failure result and the node stays unprovisioned.
- Keep node secrets (Xray private keys, Reality secrets) on server side only.

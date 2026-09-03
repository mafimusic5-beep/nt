"""Operator CLI. plan/status/images/preflight never purchase or modify resources."""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict

from sqlalchemy import select

from src.backend.services.ionos_cloud_api import IonosApiError, IonosCloudApi
from src.backend.services.ionos_cloud_bootstrap import bundle_digest
from src.backend.services.ionos_cloud_config import IonosConfigurationError, ordering_profile
from src.backend.services.ionos_cloud_provisioning import IonosCloudProvisioningService
from src.backend.services.order_service import OrderService
from src.backend.services.provisioning_guard_service import ProvisioningGuardService
from src.common.config import settings
from src.common.db import SessionLocal
from src.common.models import IonosProvisionJob, VpnNode


def plan(db, region: str) -> dict:
    result = {
        "mode": "plan", "no_changes_made": True, "region": region,
        "provider": settings.auto_provision_provider,
        "apply_enabled": settings.ionos_cloud_apply_enabled,
        "automation_enabled": settings.auto_provision_enabled,
        "monthly_cost_estimate_eur": settings.auto_provision_server_monthly_cost_eur,
        "monthly_budget_eur": settings.auto_provision_monthly_budget_eur,
    }
    try:
        # This profile is explicitly secret-free; never dump Settings or job ORM.
        result["profile"] = ordering_profile(region)
        result["bundle_sha256"] = bundle_digest()
        result["configuration_ready"] = True
    except (IonosConfigurationError, IonosApiError) as exc:
        result["configuration_ready"] = False
        result["configuration_error"] = str(exc)
    result["guard"] = asdict(ProvisioningGuardService().evaluate(
        region_code=region, nodes=list(db.scalars(select(VpnNode)))))
    return result


def status(db) -> dict:
    return {"mode": "status", "no_changes_made": True, "jobs": [{
        "operation_id": job.id, "node_id": job.node_id, "phase": job.phase,
        "datacenter_id": job.datacenter_id, "server_id": job.server_id,
        "last_error": job.last_error, "bootstrap_attempts": job.bootstrap_attempts,
        "updated_at": job.updated_at.isoformat(),
    } for job in db.scalars(select(IonosProvisionJob).order_by(IonosProvisionJob.created_at))]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="Read the durable journal; does not expose credentials")
    for name in ("plan", "preflight"):
        command = sub.add_parser(name, help="Read-only configuration check" if name == "plan" else "Read-only IONOS image/DNS API check")
        command.add_argument("--region", default=settings.default_region_code)
    images = sub.add_parser("images", help="List compatible public Debian image IDs (GET only)")
    images.add_argument("--location", required=True)
    for name in ("scale-once", "advance"):
        command = sub.add_parser(name, help="May create chargeable resources; all configured guards still apply")
        command.add_argument("--allow-paid", action="store_true", required=True,
                             help="Explicitly acknowledge that this command may create chargeable resources")
        if name == "advance":
            command.add_argument("--node-id", type=int, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "images":
            if not re.fullmatch(r"[a-z]{2}/[a-z0-9-]{2,16}", args.location):
                raise IonosConfigurationError("ionos_invalid_location")
            rows = IonosCloudApi().items("/images")
            result = {"mode": "images", "no_changes_made": True, "images": [{
                "id": row["id"], "name": row["properties"]["name"], "size_gb": row["properties"].get("size"),
                "location": row["properties"].get("location"),
            } for row in rows if (row.get("properties", {}).get("location") == args.location
                                 and row["properties"].get("public") is True and row["properties"].get("cloudInit") == "V1"
                                 and row["properties"].get("licenceType") == "LINUX"
                                 and "debian" in row["properties"].get("name", "").lower())]}
        elif args.command == "preflight":
            IonosCloudApi().preflight(ordering_profile(args.region))
            result = {"mode": "preflight", "no_changes_made": True, "image_and_dns_access": "ok"}
        else:
            with SessionLocal() as db:
                if args.command == "plan":
                    result = plan(db, args.region)
                elif args.command == "status":
                    result = status(db)
                elif args.command == "advance":
                    result = IonosCloudProvisioningService(db).advance(args.node_id)
                else:
                    if settings.auto_provision_provider != "ionos_cloud":
                        raise IonosConfigurationError("ionos_provider_not_selected")
                    result = OrderService(db).ensure_capacity_allocation()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (IonosApiError, IonosConfigurationError) as exc:
        print(json.dumps({"status": "blocked", "detail": str(exc)}))
        return 1
    except Exception as exc:
        print(json.dumps({"status": "failed", "detail": "ionos_cli_" + type(exc).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

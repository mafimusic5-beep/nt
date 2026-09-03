"""Operator CLI: check, configure and register a purchased VPS; never buy one."""
from __future__ import annotations

import argparse
import json
import time

from src.backend.services.manual_vps_config import node_spec
from src.backend.services.manual_vps_setup import ManualVpsSetupService, _safe_error
from src.common.db import SessionLocal


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="Read the private setup journal without showing credentials")
    check = commands.add_parser("check", help="Read-only local, DNS and pinned-SSH preflight")
    check.add_argument("--node-file", required=True)
    setup = commands.add_parser("setup", help="Set up an explicitly selected, already purchased clean VPS")
    setup.add_argument("--node-file", required=True)
    setup.add_argument("--apply", action="store_true", required=True,
                       help="Permit installation and firewall changes ONLY on this new VPS")
    setup.add_argument("--wait", action="store_true", help="Wait for completion; background worker may also advance")
    resume = commands.add_parser("resume", help="Advance the same registered node; never replace or repurchase it")
    resume.add_argument("--node-id", type=int, required=True)
    resume.add_argument("--apply", action="store_true", required=True)
    resume.add_argument("--retry", action="store_true", help="Retry a paused step after reviewing and fixing its error")
    resume.add_argument("--wait", action="store_true")
    args = parser.parse_args(argv)
    try:
        with SessionLocal() as db:
            service = ManualVpsSetupService(db)
            if args.command == "status":
                result = service.status()
            elif args.command == "check":
                result = service.check(node_spec(args.node_file))
            elif args.command == "setup":
                result = service.register(node_spec(args.node_file))
            else:
                result = service.advance(args.node_id, retry=args.retry)
            print(json.dumps(result, ensure_ascii=False), flush=True)
            if getattr(args, "wait", False):
                # Each iteration is a bounded, leased operation. No long SSH
                # installer runs on the controller; systemd runs it on the VPS.
                deadline = time.monotonic() + 15000
                previous = result
                while result.get("status") == "pending" and time.monotonic() < deadline:
                    time.sleep(5)
                    result = service.advance(result["node_id"])
                    if result != previous:
                        print(json.dumps(result, ensure_ascii=False), flush=True)
                    previous = result
            return 1 if result.get("status") == "blocked" else 2 if getattr(args, "wait", False) and result.get("status") == "pending" else 0
    except KeyboardInterrupt:
        # Do not delete a node, stop a remote installer or clear the journal.
        print(json.dumps({"status": "detached", "detail": "manual_vps_use_status_or_resume"}), flush=True)
        return 130
    except Exception as exc:
        print(json.dumps({"status": "blocked", "detail": _safe_error(exc)}), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

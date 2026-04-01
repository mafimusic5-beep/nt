from __future__ import annotations

import io
import json
import logging
import socket
import re
import secrets
import subprocess
import time
import uuid
from datetime import datetime, timezone

from src.backend.services.firstvds_billmanager import (
    BillManagerAuthError,
    BillManagerError,
    BillManagerInsufficientFundsError,
    FirstVdsBillManagerClient,
)
from src.backend.services.node_interfaces import NodeConfigService, NodeProvisioningService
from src.common.config import settings
from src.common.models import Device, Subscription, VpnNode

logger = logging.getLogger(__name__)

_PLACEHOLDER_MARKERS = re.compile(r"<uuid>|<public_key>|<short_id>|<private_key>")


# #region agent log
def _debug_log(hypothesis_id: str, location: str, message: str, data: dict | None = None) -> None:
    import json as _json, time as _time, uuid as _uuid, pathlib as _pathlib
    try:
        entry = _json.dumps({
            "sessionId": "723bf4", "id": f"log_{_uuid.uuid4()}", "runId": "run1",
            "hypothesisId": hypothesis_id, "location": location,
            "message": message, "data": data or {}, "timestamp": int(_time.time() * 1000),
        }, ensure_ascii=False)
        _pathlib.Path("debug-723bf4.log").open("a", encoding="utf-8").write(entry + "\n")
    except Exception:
        pass
# #endregion


class ShellScriptNodeProvisioningService(NodeProvisioningService):
    """
    Shell-based adapter for node lifecycle operations.
    Uses external scripts because no official/public FirstVDS provisioning API is assumed.
    """

    def _run_script(self, script_path: str, payload: dict) -> dict:
        if not script_path:
            return {"status": "stub", "detail": "script_path_not_configured"}
        try:
            result = subprocess.run(
                [script_path, json.dumps(payload, ensure_ascii=False)],
                capture_output=True,
                text=True,
                check=False,
            )
            return {
                "status": "ok" if result.returncode == 0 else "failed",
                "returncode": result.returncode,
                "stdout": result.stdout.strip(),
                "stderr": result.stderr.strip(),
                "detail": result.stderr.strip() or result.stdout.strip() or None,
            }
        except FileNotFoundError:
            return {"status": "failed", "detail": "script_not_found"}

    def provision_node(self, node: VpnNode) -> dict:
        payload = {"node_id": node.id, "name": node.name, "region_code": node.region_code}
        out = self._run_script(settings.node_provision_script, payload)
        logger.info("provision_node node_id=%s result=%s", node.id, out.get("status"))
        return out

    def deprovision_node(self, node: VpnNode) -> dict:
        payload = {"node_id": node.id, "name": node.name, "region_code": node.region_code}
        out = self._run_script(settings.node_deprovision_script, payload)
        logger.info("deprovision_node node_id=%s result=%s", node.id, out.get("status"))
        return out

    def healthcheck_nodes(self, nodes: list[VpnNode]) -> list[dict]:
        results: list[dict] = []
        now = datetime.now(timezone.utc).isoformat()
        for node in nodes:
            payload = {"node_id": node.id, "endpoint": node.endpoint, "time": now}
            out = self._run_script(settings.node_healthcheck_script, payload)
            if out.get("status") == "ok":
                health = "healthy"
            elif out.get("status") == "stub":
                health = "unknown"
            else:
                health = "down"
            results.append({"node_id": node.id, "health_status": health, "load_score": node.load_score})
        logger.info("healthcheck_nodes checked %d shell-script nodes", len(nodes))
        return results


class FirstVdsBillManagerProvisioningService(NodeProvisioningService):
    def __init__(self, client: FirstVdsBillManagerClient | None = None) -> None:
        self.client = client or FirstVdsBillManagerClient()

    def provision_node(self, node: VpnNode) -> dict:
        if not settings.firstvds_enabled:
            return {"status": "failed", "detail": "firstvds_credentials_not_configured"}
        if not settings.firstvds_order_pricelist or not settings.firstvds_order_datacenter or not settings.firstvds_order_ostempl:
            return {"status": "failed", "detail": "firstvds_order_profile_incomplete"}
        domain = self._node_domain(node)
        self._ensure_node_ssh_keypair(node)
        try:
            ordered = self.client.order_vds(
                domain=domain,
                datacenter=settings.firstvds_order_datacenter,
                pricelist=settings.firstvds_order_pricelist,
                ostempl=settings.firstvds_order_ostempl,
                period=settings.firstvds_order_period,
                recipe=settings.firstvds_order_recipe,
                itemtype=settings.firstvds_order_itemtype,
                skipbasket=settings.firstvds_order_skipbasket,
                addons=settings.firstvds_order_addons,
            )
            if ordered.vps_id:
                node.firstvds_vps_id = ordered.vps_id
            if ordered.endpoint:
                node.endpoint = ordered.endpoint
            node.provider = "firstvds"
            bootstrap_result = self._bootstrap_xray(node)
            if bootstrap_result.get("status") == "ok":
                node.config_payload = bootstrap_result.get("config_payload", "")
                node.ssh_key_status = "installed" if node.ssh_public_key else node.ssh_key_status
            return {
                "status": "ok",
                "detail": "ordered_via_billmanager",
                "firstvds_vps_id": ordered.vps_id,
                "endpoint": ordered.endpoint,
                "domain": domain,
                "ssh_key_status": node.ssh_key_status,
                "ssh_key_fingerprint": node.ssh_key_fingerprint,
                "bootstrap": bootstrap_result,
            }
        except BillManagerInsufficientFundsError as exc:
            return {"status": "failed", "detail": f"insufficient_funds:{exc}", "domain": domain}
        except BillManagerAuthError as exc:
            return {"status": "failed", "detail": f"auth_error:{exc}", "domain": domain}
        except BillManagerError as exc:
            return {"status": "failed", "detail": str(exc), "domain": domain}

    def deprovision_node(self, node: VpnNode) -> dict:
        if not settings.firstvds_enabled:
            return {"status": "failed", "detail": "firstvds_credentials_not_configured"}
        if not node.firstvds_vps_id:
            return {"status": "failed", "detail": "node_has_no_firstvds_vps_id"}
        try:
            self.client.delete_vds(node.firstvds_vps_id)
            return {"status": "ok", "detail": "deleted_via_billmanager", "firstvds_vps_id": node.firstvds_vps_id}
        except BillManagerError as exc:
            return {"status": "failed", "detail": str(exc), "firstvds_vps_id": node.firstvds_vps_id}

    def healthcheck_nodes(self, nodes: list[VpnNode]) -> list[dict]:
        # #region agent log
        _debug_log("H1", "node_adapters.py:healthcheck_nodes", "healthcheck_start", {"node_count": len(nodes), "uses_billmanager": False})
        # #endregion
        results: list[dict] = []
        for node in nodes:
            try:
                result = self._check_single_node(node)
            except Exception:
                logger.warning("healthcheck node=%s unexpected error", node.id, exc_info=True)
                result = {"node_id": node.id, "health_status": "unknown", "load_score": node.load_score, "reason": "check_error"}
            results.append(result)
        # #region agent log
        _debug_log("H1", "node_adapters.py:healthcheck_nodes", "healthcheck_done", {"results": {r["node_id"]: r.get("health_status") for r in results}})
        # #endregion
        logger.info(
            "healthcheck_nodes checked %d nodes (no billmanager auth): %s",
            len(results),
            {r["node_id"]: r["health_status"] for r in results},
        )
        return results

    def _check_single_node(self, node: VpnNode) -> dict:
        endpoint = (node.endpoint or "").strip()
        if not endpoint:
            return {"node_id": node.id, "health_status": "down", "load_score": node.load_score, "reason": "no_endpoint"}

        port_open = self._probe_tcp(endpoint, settings.firstvds_vless_port)

        xray_active: bool | None = None
        if node.ssh_key_status == "installed" and node.ssh_private_key.strip():
            xray_active = self._check_xray_via_ssh(endpoint, node.ssh_private_key)

        if port_open and xray_active is True:
            health, reason = "healthy", "port_open_xray_active"
        elif port_open and xray_active is None:
            health, reason = "healthy", "port_open"
        elif port_open and xray_active is False:
            health, reason = "degraded", "port_open_xray_inactive"
        else:
            health, reason = "down", "port_closed"

        return {"node_id": node.id, "health_status": health, "load_score": node.load_score, "reason": reason}

    def _check_xray_via_ssh(self, endpoint: str, private_key_data: str) -> bool | None:
        try:
            import paramiko
        except ImportError:
            return None
        pkey = self._load_private_key(private_key_data)
        if pkey is None:
            return None
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(
                hostname=endpoint,
                username=settings.firstvds_ssh_user,
                pkey=pkey,
                timeout=5,
                banner_timeout=5,
                auth_timeout=5,
                look_for_keys=False,
                allow_agent=False,
            )
            _, stdout, _ = client.exec_command("systemctl is-active xray", timeout=5)
            output = stdout.read().decode(errors="ignore").strip()
            return output == "active"
        except Exception:
            return None
        finally:
            client.close()

    @staticmethod
    def _load_private_key(private_key_data: str):
        try:
            import paramiko
        except ImportError:
            return None
        loaders = (
            getattr(paramiko, "RSAKey", None),
            getattr(paramiko, "Ed25519Key", None),
            getattr(paramiko, "ECDSAKey", None),
            getattr(paramiko, "DSSKey", None),
        )
        for loader in loaders:
            if loader is None:
                continue
            try:
                return loader.from_private_key(io.StringIO(private_key_data))
            except Exception:
                continue
        return None

    def bootstrap_existing_node(self, node: VpnNode) -> dict:
        if node.provider != "firstvds":
            return {"status": "skipped", "detail": "not_firstvds_provider"}
        self._ensure_node_ssh_keypair(node)
        result = self._bootstrap_xray(node)
        if result.get("status") == "ok":
            node.config_payload = result.get("config_payload", "")
            node.ssh_key_status = "installed" if node.ssh_public_key else node.ssh_key_status
        return result

    @staticmethod
    def _node_domain(node: VpnNode) -> str:
        base = re.sub(r"[^a-z0-9-]+", "-", node.name.lower()).strip("-") or f"node-{node.id}"
        suffix = settings.firstvds_order_domain_suffix.lstrip(".")
        return f"{base}.{suffix}" if suffix else base

    @staticmethod
    def _format_fingerprint(raw: bytes) -> str:
        return "md5:" + ":".join(f"{part:02x}" for part in raw)

    def _ensure_node_ssh_keypair(self, node: VpnNode) -> None:
        if node.ssh_private_key.strip() and node.ssh_public_key.strip():
            if not node.ssh_key_fingerprint:
                try:
                    import paramiko  # type: ignore
                    pkey = paramiko.RSAKey.from_private_key(io.StringIO(node.ssh_private_key))
                    node.ssh_key_fingerprint = self._format_fingerprint(pkey.get_fingerprint())
                except Exception:
                    node.ssh_key_fingerprint = node.ssh_key_fingerprint or ""
            if not node.ssh_key_status or node.ssh_key_status == "missing":
                node.ssh_key_status = "generated"
            return
        if not settings.firstvds_node_ssh_key_autogenerate:
            return
        try:
            import paramiko  # type: ignore
        except ImportError:
            return
        key = paramiko.RSAKey.generate(bits=settings.firstvds_node_ssh_key_bits)
        buffer = io.StringIO()
        key.write_private_key(buffer)
        comment = f"{settings.firstvds_node_ssh_key_comment_prefix}-{node.id or 'new'}"
        node.ssh_private_key = buffer.getvalue().strip() + "\n"
        node.ssh_public_key = f"{key.get_name()} {key.get_base64()} {comment}"
        node.ssh_key_fingerprint = self._format_fingerprint(key.get_fingerprint())
        node.ssh_key_status = "generated"

    @staticmethod
    def _bootstrap_script(port: int, server_name: str, node_public_key: str) -> str:
        escaped_public_key = node_public_key.replace("'", "'\''") if node_public_key else ""
        authorized_keys_block = ""
        if escaped_public_key:
            authorized_keys_block = f"""
mkdir -p /root/.ssh
chmod 700 /root/.ssh
AUTH_KEY='{escaped_public_key}'
touch /root/.ssh/authorized_keys
grep -qxF \"$AUTH_KEY\" /root/.ssh/authorized_keys || printf '%s\n' \"$AUTH_KEY\" >> /root/.ssh/authorized_keys
chmod 600 /root/.ssh/authorized_keys
"""
        return f"""#!/usr/bin/env bash
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
if command -v apt-get >/dev/null 2>&1; then
  apt-get update -y
  apt-get install -y curl unzip openssl ca-certificates
fi
{authorized_keys_block}
bash <(curl -Ls https://github.com/XTLS/Xray-install/raw/main/install-release.sh) install
UUID=\"$(cat /proc/sys/kernel/random/uuid)\"
KEYS=\"$(xray x25519)\"
PRIVATE_KEY=\"$(printf '%s\n' \"$KEYS\" | awk -F': *' '/^Private[Kk]ey:/{{print $2; exit}}')\"
PUBLIC_KEY=\"$(printf '%s\n' \"$KEYS\" | awk -F': *' '/^Public key:/{{print $2; exit}} /^Password( \\(PublicKey\\))?:/{{print $2; exit}}')\"
SHORT_ID=\"$(openssl rand -hex 8)\"
mkdir -p /usr/local/etc/xray
cat >/usr/local/etc/xray/config.json <<EOF
{{
  \"log\": {{\"loglevel\": \"warning\"}},
  \"inbounds\": [
    {{
      \"listen\": \"0.0.0.0\",
      \"port\": {port},
      \"protocol\": \"vless\",
      \"settings\": {{
        \"clients\": [{{\"id\": \"$UUID\", \"flow\": \"xtls-rprx-vision\"}}],
        \"decryption\": \"none\"
      }},
      \"streamSettings\": {{
        \"network\": \"tcp\",
        \"security\": \"reality\",
        \"realitySettings\": {{
          \"show\": false,
          \"dest\": \"{server_name}:443\",
          \"xver\": 0,
          \"serverNames\": [\"{server_name}\"],
          \"privateKey\": \"$PRIVATE_KEY\",
          \"shortIds\": [\"$SHORT_ID\"]
        }}
      }}
    }}
  ],
  \"outbounds\": [{{\"protocol\": \"freedom\"}}]
}}
EOF
systemctl enable xray >/dev/null 2>&1 || true
systemctl restart xray
systemctl is-active xray >/dev/null 2>&1
printf 'XRAY_UUID=%s\n' \"$UUID\"
printf 'XRAY_PUBLIC_KEY=%s\n' \"$PUBLIC_KEY\"
printf 'XRAY_SHORT_ID=%s\n' \"$SHORT_ID\"
"""

    @staticmethod
    def normalize_config_payload(payload: str) -> str:
        def fix_line(line: str) -> str:
            stripped = line.strip()
            if not stripped.startswith("vless://") or "security=reality" not in stripped or "flow=" in stripped:
                return stripped or line
            if "?" not in stripped:
                return stripped
            base, rest = stripped.split("?", 1)
            if "#" in rest:
                query, fragment = rest.split("#", 1)
            else:
                query, fragment = rest, ""
            params = [part for part in query.split("&") if part]
            insert_at = 0
            for idx, item in enumerate(params):
                if item.startswith("encryption="):
                    insert_at = idx + 1
                    break
            params.insert(insert_at, "flow=xtls-rprx-vision")
            rebuilt = f"{base}?{'&'.join(params)}"
            if fragment:
                rebuilt += f"#{fragment}"
            return rebuilt
        return "\n".join(fix_line(line) for line in payload.splitlines())

    @staticmethod
    def is_config_payload_valid(payload: str) -> bool:
        """Check that a config payload has no placeholder markers and looks
        like a real VLESS Reality URI."""
        stripped = payload.strip()
        if not stripped:
            return False
        for line in stripped.splitlines():
            line = line.strip()
            if not line:
                continue
            if not line.startswith("vless://"):
                continue
            if _PLACEHOLDER_MARKERS.search(line):
                return False
            required = ("security=reality", "pbk=", "sid=", "type=tcp")
            if not all(tok in line for tok in required):
                return False
        return True

    def _bootstrap_xray(self, node: VpnNode) -> dict:
        if not settings.firstvds_auto_configure_xray:
            logger.info("bootstrap_xray skipped for node %s — auto_configure_disabled", node.id)
            return {"status": "skipped", "detail": "auto_configure_disabled"}
        endpoint = (node.endpoint or "").strip()
        if not endpoint:
            logger.warning("bootstrap_xray failed for node %s — missing endpoint", node.id)
            return {"status": "failed", "detail": "missing_endpoint"}
        self._ensure_node_ssh_keypair(node)
        script = self._bootstrap_script(settings.firstvds_vless_port, settings.firstvds_reality_sni, node.ssh_public_key)
        attempts: list[tuple[str, callable]] = []
        if node.ssh_private_key.strip() and node.ssh_key_status == "installed":
            attempts.append(("node_db_key", lambda: self._run_script_via_private_key_data(endpoint, node.ssh_private_key, script)))
        key_path = (settings.firstvds_ssh_private_key_path or "").strip()
        if key_path:
            attempts.append(("global_fs_key", lambda: self._run_script_via_ssh_key(endpoint, key_path, script)))
        logger.info(
            "bootstrap_xray node=%s endpoint=%s ssh_key_status=%s strategies=%s",
            node.id, endpoint, node.ssh_key_status,
            [name for name, _ in attempts] + (["password_fallback"] if settings.firstvds_password_bootstrap_enabled else []),
        )
        result: dict = {"status": "failed", "detail": "no_ssh_strategy_available"}
        for strategy_name, runner in attempts:
            result = runner()
            logger.info("bootstrap_xray node=%s strategy=%s result=%s", node.id, strategy_name, result.get("status"))
            if result.get("status") == "ok":
                result["auth_strategy"] = strategy_name
                break
        if result.get("status") != "ok" and settings.firstvds_password_bootstrap_enabled:
            result = self._run_script_via_password_fallback(node, endpoint, script)
            logger.info("bootstrap_xray node=%s strategy=password_fallback result=%s", node.id, result.get("status"))
            if result.get("status") == "ok":
                result["auth_strategy"] = "firstvds_password_fallback"
        if result.get("status") != "ok":
            logger.error("bootstrap_xray node=%s FAILED detail=%s", node.id, result.get("detail"))
            return {
                "status": "failed",
                "detail": str(result.get("detail", "ssh_bootstrap_failed")),
                "stderr": (str(result.get("stderr", "")) or "").strip()[:200],
            }
        raw_stdout = str(result.get("stdout", ""))
        values: dict[str, str] = {}
        for line in raw_stdout.splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                if key in {"XRAY_UUID", "XRAY_PUBLIC_KEY", "XRAY_SHORT_ID"}:
                    values[key] = value.strip()
        if not all(values.get(k) for k in ("XRAY_UUID", "XRAY_PUBLIC_KEY", "XRAY_SHORT_ID")):
            return {"status": "failed", "detail": "bootstrap_values_missing"}
        tag = f"{node.region_code or 'node'}-{node.id}-{uuid.uuid4().hex[:6]}"
        config_payload = self.normalize_config_payload(
            f"vless://{values['XRAY_UUID']}@{endpoint}:{settings.firstvds_vless_port}"
            f"?encryption=none&security=reality&sni={settings.firstvds_reality_sni}"
            f"&fp=chrome&pbk={values['XRAY_PUBLIC_KEY']}&sid={values['XRAY_SHORT_ID']}&type=tcp#{tag}"
        )
        node.ssh_key_status = "installed" if node.ssh_public_key else node.ssh_key_status
        logger.info(
            "bootstrap_xray node=%s OK auth=%s config_valid=%s",
            node.id, result.get("auth_strategy"),
            self.is_config_payload_valid(config_payload),
        )
        return {
            "status": "ok",
            "detail": "xray_bootstrapped",
            "auth_strategy": result.get("auth_strategy"),
            "config_payload": config_payload,
        }

    @staticmethod
    def _probe_tcp(endpoint: str, port: int) -> bool:
        try:
            with socket.create_connection((endpoint, port), timeout=3):
                return True
        except OSError:
            return False

    def _run_script_via_private_key_data(self, endpoint: str, private_key_data: str, script: str) -> dict:
        try:
            import paramiko  # type: ignore
        except ImportError:
            return {"status": "failed", "detail": "paramiko_not_installed"}
        pkey = self._load_private_key(private_key_data)
        if pkey is None:
            return {"status": "failed", "detail": "invalid_node_ssh_private_key"}
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(
                hostname=endpoint,
                username=settings.firstvds_ssh_user,
                pkey=pkey,
                timeout=settings.firstvds_ssh_connect_timeout_seconds,
                banner_timeout=settings.firstvds_ssh_connect_timeout_seconds,
                auth_timeout=settings.firstvds_ssh_connect_timeout_seconds,
                look_for_keys=False,
                allow_agent=False,
            )
            return self._run_remote_script_via_paramiko_client(client, script, ok_detail="ssh_db_key_bootstrap_ok", fail_detail="ssh_db_key_bootstrap_failed")
        except Exception as exc:  # noqa: BLE001
            return {"status": "failed", "detail": f"ssh_db_key_connect_failed:{type(exc).__name__}"}
        finally:
            client.close()

    def _run_script_via_ssh_key(self, endpoint: str, key_path: str, script: str) -> dict:
        ssh_target = f"{settings.firstvds_ssh_user}@{endpoint}"
        cmd = [
            "ssh",
            "-i",
            key_path,
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-o",
            f"ConnectTimeout={settings.firstvds_ssh_connect_timeout_seconds}",
            ssh_target,
            "bash -s --",
        ]
        try:
            result = subprocess.run(
                cmd,
                input=script,
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError:
            return {"status": "failed", "detail": "ssh_binary_not_found"}
        if result.returncode != 0:
            return {"status": "failed", "detail": "ssh_key_bootstrap_failed", "stderr": (result.stderr or "").strip()[:200]}
        return {"status": "ok", "detail": "ssh_key_bootstrap_ok", "stdout": result.stdout}

    def _run_remote_script_via_paramiko_client(self, client, script: str, *, ok_detail: str, fail_detail: str) -> dict:
        stdin, stdout, stderr = client.exec_command("bash -s --")
        stdin.write(script)
        stdin.flush()
        stdin.channel.shutdown_write()
        out = stdout.read().decode(errors="ignore")
        err = stderr.read().decode(errors="ignore")
        rc = stdout.channel.recv_exit_status()
        if rc != 0:
            return {"status": "failed", "detail": fail_detail, "stderr": err[:200]}
        return {"status": "ok", "detail": ok_detail, "stdout": out}

    def _run_script_via_password_fallback(self, node: VpnNode, endpoint: str, script: str) -> dict:
        if not node.firstvds_vps_id:
            return {"status": "failed", "detail": "missing_firstvds_vps_id_for_password_bootstrap"}
        temp_password = f"Emery{secrets.token_hex(10)}Aa1!"
        try:
            self.client.set_service_password(node.firstvds_vps_id, temp_password)
        except BillManagerError as exc:
            return {"status": "failed", "detail": f"service_set_password_failed:{str(exc)[:120]}"}
        state = "unknown"
        for _ in range(20):
            try:
                payload = self.client.get_service_password_state(node.firstvds_vps_id)
                doc = payload.get("doc", {})
                value = doc.get("value")
                if isinstance(value, dict):
                    state = str(value.get("$", "")).strip()
                else:
                    state = str(value or "").strip()
                if state == "success":
                    break
                if state == "fail":
                    return {"status": "failed", "detail": "service_set_password_state_fail"}
            except BillManagerError:
                pass
            time.sleep(2)
        if state != "success":
            return {"status": "failed", "detail": f"service_set_password_state_timeout:{state}"}
        try:
            import paramiko  # type: ignore
        except ImportError:
            return {"status": "failed", "detail": "paramiko_not_installed"}
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(
                hostname=endpoint,
                username=settings.firstvds_ssh_user,
                password=temp_password,
                timeout=settings.firstvds_ssh_connect_timeout_seconds,
                banner_timeout=settings.firstvds_ssh_connect_timeout_seconds,
                auth_timeout=settings.firstvds_ssh_connect_timeout_seconds,
                look_for_keys=False,
                allow_agent=False,
            )
            return self._run_remote_script_via_paramiko_client(client, script, ok_detail="ssh_password_bootstrap_ok", fail_detail="ssh_password_bootstrap_failed")
        except Exception as exc:  # noqa: BLE001
            return {"status": "failed", "detail": f"ssh_password_connect_failed:{type(exc).__name__}"}
        finally:
            client.close()


class XrayVlessRealityConfigService(NodeConfigService):
    """Returns the persisted VLESS Reality config for a node.

    If config_payload is empty (node not yet bootstrapped), returns an empty
    string so that the caller can skip this node instead of handing the user
    an invalid placeholder link.
    """

    def build_import_text(self, node: VpnNode, subscription: Subscription, device: Device | None) -> str:
        raw = (node.config_payload or "").strip()
        # #region agent log
        _debug_log(
            "H3",
            "node_adapters.py:build_import_text",
            "build_import_text_enter",
            {
                "node_id": node.id,
                "subscription_id": subscription.id,
                "device_present": bool(device),
                "device_fp_prefix": (device.device_fingerprint[:10] if device else ""),
                "raw_len": len(raw),
            },
        )
        # #endregion
        if not raw:
            logger.warning("node %s has no config_payload — skipping", node.id)
            # #region agent log
            _debug_log(
                "H3",
                "node_adapters.py:build_import_text",
                "config_payload_empty",
                {"node_id": node.id},
            )
            # #endregion
            return ""
        normalized = FirstVdsBillManagerProvisioningService.normalize_config_payload(raw)
        if not FirstVdsBillManagerProvisioningService.is_config_payload_valid(normalized):
            logger.warning("node %s has invalid/placeholder config_payload — skipping", node.id)
            # #region agent log
            _debug_log(
                "H3",
                "node_adapters.py:build_import_text",
                "config_payload_invalid",
                {"node_id": node.id},
            )
            # #endregion
            return ""
        # #region agent log
        _debug_log(
            "H3",
            "node_adapters.py:build_import_text",
            "build_import_text_exit",
            {
                "node_id": node.id,
                "contains_vless": normalized.startswith("vless://"),
                "contains_dns_hint": ("dns=" in normalized.lower() or "doh" in normalized.lower()),
            },
        )
        # #endregion
        return normalized

"""Pinned-SSH deployment and independent readiness checks for new IONOS nodes."""
from __future__ import annotations

import base64
import hashlib
import json
import socket
import ssl
import stat
from pathlib import Path

from src.backend.services.ionos_cloud_api import IonosApiError
from src.backend.services.node_recovery_service import SshAndProviderRecoveryTransport
from src.backend.services.xray_credential_service import ScriptOrSshXrayCredentialTransport
from src.common.config import settings
from src.common.models import IonosProvisionJob, VpnAssignment, VpnNode

DEPLOY_ROOT = Path(__file__).resolve().parents[3] / "deploy"
REMOTE_ROOT = "/opt/emery/ionos-bootstrap"
STATE_ROOT = "/var/lib/emery-ionos"
BUNDLE_FILES = (
    "ionos-cloud/bootstrap_node.py",
    "device-gate/emery_device_gate.py",
    "regional-policy/regional_policy.py",
    "regional-policy/install.sh",
    "regional-policy/regional-policy.env.example",
    "regional-policy/emery-regional-xray.service",
    "regional-policy/emery-regional-policy-update.service",
    "regional-policy/emery-regional-policy-update.timer",
)


def initialize_ssh_keys(node: VpnNode, job: IonosProvisionJob) -> None:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    def pair() -> tuple[str, str]:
        key = Ed25519PrivateKey.generate()
        private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH,
                                    serialization.NoEncryption()).decode()
        public = key.public_key().public_bytes(serialization.Encoding.OpenSSH,
                                               serialization.PublicFormat.OpenSSH).decode()
        return private, public

    node.ssh_private_key, node.ssh_public_key = pair()
    job.bootstrap_host_private_key, node.ssh_host_key = pair()
    blob = base64.b64decode(node.ssh_public_key.split()[1])
    node.ssh_key_fingerprint = "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")
    node.ssh_key_status = "generated"


def cloud_init(node: VpnNode, job: IonosProvisionJob) -> str:
    # cloud-config accepts JSON (a YAML subset). Only this node's host key and
    # public login key are sent to IONOS; never the cloud/control API tokens.
    value = {
        "disable_root": False, "ssh_pwauth": False, "ssh_deletekeys": True,
        "users": [{"name": "root", "lock_passwd": True, "ssh_authorized_keys": [node.ssh_public_key]}],
        "ssh_keys": {"ed25519_private": job.bootstrap_host_private_key,
                     "ed25519_public": node.ssh_host_key},
        "write_files": [{
            "path": "/etc/ssh/sshd_config.d/00-emery-ionos.conf", "owner": "root:root", "permissions": "0600",
            "content": "PasswordAuthentication no\nKbdInteractiveAuthentication no\nPermitRootLogin prohibit-password\nAllowTcpForwarding no\nX11Forwarding no\n",
        }],
        "runcmd": [["systemctl", "restart", "ssh"]],
    }
    return base64.b64encode(("#cloud-config\n" + json.dumps(value)).encode()).decode()


def bundle_digest() -> str:
    checksum = hashlib.sha256()
    for name in BUNDLE_FILES:
        path = DEPLOY_ROOT / name
        if not path.is_file():
            raise IonosApiError("ionos_bootstrap_bundle_missing")
        checksum.update(name.encode() + b"\0" + path.read_bytes())
    return checksum.hexdigest()


class IonosSshBootstrap:
    def __init__(self):
        self.ssh = SshAndProviderRecoveryTransport()

    @staticmethod
    def _exec(client, command: str, *, data: str | None = None, timeout: int = 20) -> str:
        stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
        if data is not None:
            stdin.write(data)
            stdin.flush()
            stdin.channel.shutdown_write()
        out = stdout.read(65537)
        # Drain bounded diagnostics but never return them (they may contain secrets).
        stderr.read(8192)
        if len(out) > 65536 or stdout.channel.recv_exit_status() != 0:
            raise IonosApiError("ionos_remote_command_failed")
        return out.decode("utf-8")

    def start(self, node: VpnNode, job: IonosProvisionJob, profile: dict) -> None:
        if bundle_digest() != profile["bundle_sha256"]:
            raise IonosApiError("ionos_bootstrap_bundle_changed")
        client = self.ssh._connect(node)
        try:
            # Reconcile an SSH response lost after systemd accepted the job.
            # Never rewrite a running install's configuration or start it twice.
            with client.open_sftp() as sftp:
                sftp.get_channel().settimeout(20)
                try:
                    sftp.lstat(STATE_ROOT + "/ready.json")
                    return
                except FileNotFoundError:
                    pass
            state = self._exec(client, "systemctl show emery-ionos-bootstrap.service --property=ActiveState --value").strip()
            if state in {"active", "activating", "reloading"}:
                return
            if state == "failed":
                raise IonosApiError("ionos_bootstrap_failed_operator_review_required")
            # Only immutable, new node jobs may call this deployment helper.
            self._exec(client, "install -d -m 700 /opt/emery/ionos-bootstrap /opt/emery/ionos-bootstrap/ionos-cloud /opt/emery/ionos-bootstrap/device-gate /opt/emery/ionos-bootstrap/regional-policy /var/lib/emery-ionos")
            with client.open_sftp() as sftp:
                sftp.get_channel().settimeout(20)
                for name in BUNDLE_FILES:
                    remote = REMOTE_ROOT + "/" + name
                    with sftp.file(remote, "wb") as handle:
                        handle.write((DEPLOY_ROOT / name).read_bytes())
                    sftp.chmod(remote, 0o600)
                config = dict(profile, operation_id=job.id, node_id=node.id, endpoint=node.endpoint,
                              authorize_key=settings.ionos_cloud_gate_authorize_key.get_secret_value())
                with sftp.file(REMOTE_ROOT + "/config.json", "w") as handle:
                    handle.write(json.dumps(config))
                sftp.chmod(REMOTE_ROOT + "/config.json", 0o600)
                timeout = int(profile["bootstrap_timeout_seconds"])
                unit = f"""[Unit]
Description=Prepare a new Skryon IONOS node
After=network-online.target
Wants=network-online.target
ConditionPathExists=!/var/lib/emery-ionos/ready.json
[Service]
Type=oneshot
RemainAfterExit=yes
TimeoutStartSec={timeout}
UMask=0077
ExecStart=/usr/bin/python3 /opt/emery/ionos-bootstrap/ionos-cloud/bootstrap_node.py /opt/emery/ionos-bootstrap/config.json
[Install]
WantedBy=multi-user.target
"""
                with sftp.file("/etc/systemd/system/emery-ionos-bootstrap.service", "w") as handle:
                    handle.write(unit)
                sftp.chmod("/etc/systemd/system/emery-ionos-bootstrap.service", 0o644)
            # The long installation runs on the node, not in an API request or
            # in the Android connection path. A scheduler tick only starts it.
            self._exec(client, "systemctl daemon-reload && systemctl enable emery-ionos-bootstrap.service && systemctl start --no-block emery-ionos-bootstrap.service")
        finally:
            client.close()

    def inspect(self, node: VpnNode, job: IonosProvisionJob) -> dict | None:
        client = self.ssh._connect(node)
        try:
            with client.open_sftp() as sftp:
                sftp.get_channel().settimeout(20)
                try:
                    info = sftp.lstat(STATE_ROOT + "/ready.json")
                except FileNotFoundError:
                    state = self._exec(client, "systemctl show emery-ionos-bootstrap.service --property=ActiveState --value").strip()
                    if state in {"failed", "inactive"}:
                        raise IonosApiError("ionos_bootstrap_failed")
                    return None
                if info.st_uid != 0 or not stat.S_ISREG(info.st_mode) or info.st_mode & 0o022 or info.st_size > 16384:
                    raise IonosApiError("ionos_readiness_file_unsafe")
                with sftp.file(STATE_ROOT + "/ready.json") as handle:
                    result = json.loads(handle.read(16385))
            if not isinstance(result, dict) or result.get("operation_id") != job.id:
                raise IonosApiError("ionos_readiness_operation_mismatch")
            return result
        finally:
            client.close()

    @staticmethod
    def check_tls(node: VpnNode) -> None:
        from cryptography import x509
        from cryptography.hazmat.primitives import serialization

        # Same public-CA + hostname + SPKI checks used by the existing Android
        # client. No self-signed exception or certificate verification bypass.
        with socket.create_connection((node.endpoint, node.device_gate_port), timeout=10) as raw:
            with ssl.create_default_context().wrap_socket(raw, server_hostname=node.device_gate_server_name) as secured:
                secured.settimeout(5)
                cert = x509.load_der_x509_certificate(secured.getpeercert(binary_form=True))
                spki = cert.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
                if hashlib.sha256(spki).hexdigest() != node.device_gate_spki_sha256:
                    raise IonosApiError("ionos_gateway_pin_mismatch")
                with secured.makefile("rb") as stream:
                    line = stream.readline(8193)
                    challenge = json.loads(line)
                    if len(line) > 8192 or not isinstance(challenge, dict) or challenge.get("version") != 1:
                        raise IonosApiError("ionos_gateway_challenge_invalid")
                    secured.sendall(b"{}\n")
                    response = stream.readline(8193)
                    if len(response) > 8192:
                        raise IonosApiError("ionos_gateway_denial_invalid")
                    if response:
                        denied = json.loads(response)
                        if not isinstance(denied, dict) or denied.get("ok") is not False:
                            raise IonosApiError("ionos_gateway_accepted_unsigned_probe")

    def verify_data_plane(self, node: VpnNode, job: IonosProvisionJob, profile: dict) -> None:
        import uuid

        # The node has not been published and has no real assignments. Exercise
        # the existing per-device installer, then always remove the canary.
        canary = VpnAssignment(id=2147483000, node_id=node.id, client_uuid=str(uuid.uuid4()),
                               client_port=profile["assignment_port_start"], speed_limit_mbps=30)
        credentials = ScriptOrSshXrayCredentialTransport()
        try:
            installed = credentials.install(node, canary)
            if not (installed.ok and installed.rate_limit_enforced and installed.smtp_block_enforced
                    and installed.shared_credential_disabled and installed.direct_ingress_blocked and installed.device_gate_ready):
                raise IonosApiError("ionos_credential_canary_failed")
            client = self.ssh._connect(node)
            try:
                output = self._exec(client,
                    "/usr/bin/python3 /opt/emery/ionos-bootstrap/ionos-cloud/bootstrap_node.py --smoke /opt/emery/ionos-bootstrap/config.json",
                    data=json.dumps({"uuid": canary.client_uuid, "port": canary.client_port}), timeout=40)
                if json.loads(output).get("ok") is not True:
                    raise IonosApiError("ionos_vpn_canary_failed")
            finally:
                client.close()
        finally:
            removed = credentials.remove(node, canary)
            if not removed.ok:
                raise IonosApiError("ionos_canary_cleanup_failed")
        self.check_tls(node)

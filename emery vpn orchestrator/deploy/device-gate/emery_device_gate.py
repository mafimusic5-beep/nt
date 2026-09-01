#!/usr/bin/env python3
"""TLS proof-of-possession gateway for device-bound VLESS connections.

The public listener never forwards bytes based on a VLESS UUID alone. For
every TCP connection it issues a fresh challenge, asks the control plane to
verify the Android Keystore signature, and only then connects to the
assignment's loopback-only Xray inbound.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import secrets
import ssl
import stat
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PROTOCOL_VERSION = 1
MAX_CONTROL_LINE_BYTES = 8192
LOGGER = logging.getLogger("emery-device-gate")
SAFE_SERVER_NAME = re.compile(r"^[A-Za-z0-9.-]{1,255}$")
REGIONAL_POLICY_MAX_AGE = 48 * 60 * 60


class GateError(Exception):
    pass


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise GateError(f"missing required environment variable: {name}")
    return value


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise GateError(f"invalid integer environment variable: {name}") from exc
    if value < minimum or value > maximum:
        raise GateError(f"environment variable out of range: {name}")
    return value


@dataclass(frozen=True)
class Config:
    bind_host: str
    bind_port: int
    node_id: int
    server_name: str
    spki_sha256: str
    tls_cert_file: str
    tls_key_file: str
    authorize_url: str
    authorize_key: str
    control_timeout_seconds: int
    connect_timeout_seconds: int
    max_connections: int
    regional_policy_state_file: str = "/var/lib/emery-regional-policy/ready.json"

    @classmethod
    def from_env(cls) -> "Config":
        authorize_url = _required_env("EMERY_GATE_AUTHORIZE_URL")
        parsed = urllib.parse.urlparse(authorize_url)
        loopback_hosts = {"127.0.0.1", "::1", "localhost"}
        if parsed.scheme != "https" and not (
            parsed.scheme == "http" and parsed.hostname in loopback_hosts
        ):
            raise GateError(
                "EMERY_GATE_AUTHORIZE_URL must use HTTPS (loopback HTTP is allowed)"
            )
        if parsed.path != "/internal/device-gate/authorize":
            raise GateError("EMERY_GATE_AUTHORIZE_URL has an unexpected path")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise GateError("EMERY_GATE_AUTHORIZE_URL contains forbidden components")
        server_name = _required_env("EMERY_GATE_SERVER_NAME").lower()
        if (
            not SAFE_SERVER_NAME.fullmatch(server_name)
            or server_name.startswith(".")
            or server_name.endswith(".")
        ):
            raise GateError("EMERY_GATE_SERVER_NAME is invalid")
        authorize_key = _required_env("EMERY_GATE_AUTHORIZE_KEY")
        if len(authorize_key) < 32:
            raise GateError("EMERY_GATE_AUTHORIZE_KEY must contain at least 32 characters")
        spki_sha256 = _required_env("EMERY_GATE_SPKI_SHA256").lower()
        if not re.fullmatch(r"[a-f0-9]{64}", spki_sha256):
            raise GateError("EMERY_GATE_SPKI_SHA256 must be a lowercase SHA-256 hex digest")
        return cls(
            bind_host=os.getenv("EMERY_GATE_BIND_HOST", "0.0.0.0").strip(),
            bind_port=_env_int("EMERY_GATE_BIND_PORT", 24443, 1, 65535),
            node_id=_env_int("EMERY_GATE_NODE_ID", 0, 1, 2_147_483_647),
            server_name=server_name,
            spki_sha256=spki_sha256,
            tls_cert_file=_required_env("EMERY_GATE_TLS_CERT_FILE"),
            tls_key_file=_required_env("EMERY_GATE_TLS_KEY_FILE"),
            authorize_url=authorize_url,
            authorize_key=authorize_key,
            control_timeout_seconds=_env_int(
                "EMERY_GATE_CONTROL_TIMEOUT_SECONDS", 10, 2, 30
            ),
            connect_timeout_seconds=_env_int(
                "EMERY_GATE_CONNECT_TIMEOUT_SECONDS", 5, 1, 30
            ),
            max_connections=_env_int(
                "EMERY_GATE_MAX_CONNECTIONS", 2048, 1, 100_000
            ),
            regional_policy_state_file=os.getenv(
                "EMERY_GATE_REGIONAL_POLICY_STATE_FILE",
                "/var/lib/emery-regional-policy/ready.json",
            ),
        )


def _tls_context(config: Config) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.options |= ssl.OP_NO_COMPRESSION
    context.load_cert_chain(config.tls_cert_file, config.tls_key_file)
    return context


def _json_line(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")


async def _read_json_line(
    reader: asyncio.StreamReader,
    timeout_seconds: int,
) -> dict[str, Any]:
    try:
        raw = await asyncio.wait_for(reader.readline(), timeout_seconds)
    except (asyncio.TimeoutError, ValueError) as exc:
        raise GateError("control message timeout or too large") from exc
    if not raw or len(raw) > MAX_CONTROL_LINE_BYTES or not raw.endswith(b"\n"):
        raise GateError("invalid control message framing")
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GateError("invalid control message") from exc
    if not isinstance(payload, dict):
        raise GateError("invalid control message")
    return payload


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, ANN201
        return None


def _authorize_sync(config: Config, proof: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        config.authorize_url,
        data=json.dumps(proof, separators=(",", ":")).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-Device-Gate-Key": config.authorize_key,
        },
        method="POST",
    )
    try:
        opener = urllib.request.build_opener(_NoRedirect)
        with opener.open(request, timeout=config.control_timeout_seconds) as response:
            if response.status != 200:
                raise GateError("authorization denied")
            raw = response.read(MAX_CONTROL_LINE_BYTES + 1)
    except (urllib.error.URLError, TimeoutError) as exc:
        raise GateError("authorization unavailable") from exc
    if len(raw) > MAX_CONTROL_LINE_BYTES:
        raise GateError("authorization response too large")
    try:
        result = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GateError("invalid authorization response") from exc
    if not isinstance(result, dict):
        raise GateError("invalid authorization response")
    return result


def _validated_proof(
    config: Config,
    payload: dict[str, Any],
    server_issued_at: str,
    server_nonce: str,
) -> dict[str, Any]:
    expected_keys = {
        "version",
        "assignment_id",
        "node_id",
        "gate_server_name",
        "gate_spki_sha256",
        "device_id",
        "server_issued_at",
        "timestamp",
        "server_nonce",
        "client_nonce",
        "signature",
        "signature_algorithm",
    }
    version = payload.get("version")
    if type(version) is not int or version not in (1, 2):
        raise GateError("unsupported protocol version")
    if version == 2:
        expected_keys |= {"regional_policy", "operation"}
        if payload.get("regional_policy") != "russia" or payload.get("operation") not in (
            "connect", "check"
        ):
            raise GateError("unsupported regional policy or operation")
    if set(payload) != expected_keys:
        raise GateError("invalid proof fields")
    if payload.get("node_id") != config.node_id:
        raise GateError("wrong node")
    if payload.get("gate_server_name") != config.server_name:
        raise GateError("wrong gateway identity")
    if payload.get("gate_spki_sha256") != config.spki_sha256:
        raise GateError("wrong gateway certificate pin")
    if payload.get("server_issued_at") != server_issued_at:
        raise GateError("challenge mismatch")
    if not secrets.compare_digest(str(payload.get("server_nonce", "")), server_nonce):
        raise GateError("challenge mismatch")
    proof = {key: value for key, value in payload.items() if key != "version"}
    if version == 2:
        proof["protocol_version"] = 2
    return proof


def _target_host(proof: dict[str, Any]) -> str:
    return "127.0.0.2" if proof.get("regional_policy") == "russia" else "127.0.0.1"


def _validated_target(config: Config, result: dict[str, Any], proof: dict[str, Any]) -> int:
    if result.get("allowed") is not True:
        raise GateError("authorization denied")
    if result.get("target_host") != _target_host(proof):
        raise GateError("wrong policy target denied")
    if proof.get("protocol_version") == 2 and any(
        result.get(key) != proof.get(key)
        for key in ("protocol_version", "regional_policy", "operation")
    ):
        raise GateError("regional policy downgrade denied")
    if result.get("assignment_id") != proof.get("assignment_id"):
        raise GateError("assignment mismatch")
    if result.get("node_id") != config.node_id:
        raise GateError("node mismatch")
    target_port = result.get("target_port")
    if type(target_port) is not int or target_port < 1 or target_port > 65535:
        raise GateError("invalid target")
    return target_port


def _regional_policy_deadline(config: Config, target_port: int, assignment_id: int) -> float:
    """Small, root-owned readiness record; never downloads or reads the datasets."""
    path = Path(config.regional_policy_state_file)
    try:
        parent = path.parent.stat()
        if parent.st_uid != 0 or parent.st_mode & 0o022:
            raise ValueError("unsafe state directory")
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
                raise ValueError("unsafe state file")
            raw = handle.read(65_537)
        if len(raw) > 65_536:
            raise ValueError("oversized state file")
        state = json.loads(raw)
        if not isinstance(state, dict) or not isinstance(state.get("assignments"), dict):
            raise ValueError("invalid policy state")
        if not isinstance(state.get("ports"), list):
            raise ValueError("invalid policy ports")
        updated = float(state["updated_at"])
        now = time.time()
        if (
            state["schema"] != 1
            or state["policy"] != "russia"
            or state["listen_host"] != "127.0.0.2"
            or target_port not in state["ports"]
            or state["assignments"].get(str(target_port)) != assignment_id
            or not math.isfinite(updated)
            or not 0 <= now - updated < REGIONAL_POLICY_MAX_AGE
        ):
            raise ValueError("policy not ready or expired")
        return updated + REGIONAL_POLICY_MAX_AGE
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise GateError("regional policy unavailable") from exc


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    while True:
        chunk = await reader.read(64 * 1024)
        if not chunk:
            return
        writer.write(chunk)
        await writer.drain()


async def _proxy_bidirectional(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    target_reader: asyncio.StreamReader,
    target_writer: asyncio.StreamWriter,
) -> None:
    tasks = {
        asyncio.create_task(_pipe(client_reader, target_writer)),
        asyncio.create_task(_pipe(target_reader, client_writer)),
    }
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


class DeviceGate:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.slots = asyncio.Semaphore(config.max_connections)

    async def handle(
        self,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
    ) -> None:
        if self.slots.locked():
            client_writer.close()
            try:
                await client_writer.wait_closed()
            except (ConnectionError, OSError, ssl.SSLError):
                pass
            return
        await self.slots.acquire()
        target_writer: asyncio.StreamWriter | None = None
        control_complete = False
        try:
            server_issued_at = str(int(time.time() * 1000))
            server_nonce = secrets.token_urlsafe(32)
            client_writer.write(
                _json_line(
                    {
                        "version": PROTOCOL_VERSION,
                        "server_issued_at": server_issued_at,
                        "server_nonce": server_nonce,
                    }
                )
            )
            await client_writer.drain()
            payload = await _read_json_line(
                client_reader, self.config.control_timeout_seconds
            )
            proof = _validated_proof(
                self.config, payload, server_issued_at, server_nonce
            )
            result = await asyncio.wait_for(
                asyncio.to_thread(_authorize_sync, self.config, proof),
                self.config.control_timeout_seconds + 1,
            )
            target_port = _validated_target(self.config, result, proof)
            deadline = None
            if proof.get("regional_policy") == "russia":
                deadline = _regional_policy_deadline(self.config, target_port, proof["assignment_id"])
            target_reader, target_writer = await asyncio.wait_for(
                asyncio.open_connection(_target_host(proof), target_port),
                self.config.connect_timeout_seconds,
            )
            response = {"ok": True}
            if deadline is not None:
                response.update(protocol_version=2, regional_policy="russia", operation=proof["operation"])
            client_writer.write(_json_line(response))
            await client_writer.drain()
            control_complete = True
            if proof.get("operation") != "check":
                await asyncio.wait_for(
                    _proxy_bidirectional(client_reader, client_writer, target_reader, target_writer),
                    timeout=max(0, deadline - time.time()) if deadline is not None else None,
                )
        except (GateError, asyncio.TimeoutError, OSError) as exc:
            if control_complete:
                LOGGER.info("authorized connection closed: %s", type(exc).__name__)
            else:
                LOGGER.warning("connection rejected: %s", exc)
            if not control_complete:
                try:
                    client_writer.write(_json_line({"ok": False}))
                    await client_writer.drain()
                except (ConnectionError, OSError):
                    pass
        finally:
            if target_writer is not None:
                target_writer.close()
                try:
                    await target_writer.wait_closed()
                except (ConnectionError, OSError):
                    pass
            client_writer.close()
            try:
                await client_writer.wait_closed()
            except (ConnectionError, OSError, ssl.SSLError):
                pass
            finally:
                self.slots.release()


async def _main() -> None:
    config = Config.from_env()
    gate = DeviceGate(config)
    server = await asyncio.start_server(
        gate.handle,
        config.bind_host,
        config.bind_port,
        ssl=_tls_context(config),
        limit=MAX_CONTROL_LINE_BYTES,
    )
    LOGGER.info(
        "device gate listening on %s:%d for node %d",
        config.bind_host,
        config.bind_port,
        config.node_id,
    )
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    logging.basicConfig(
        level=os.getenv("EMERY_GATE_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        asyncio.run(_main())
    except (GateError, KeyboardInterrupt) as exc:
        raise SystemExit(str(exc)) from exc

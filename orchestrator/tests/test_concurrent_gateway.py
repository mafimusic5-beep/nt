"""Exercise actual TCP forwarding/close behavior, independent of Android UI."""
import asyncio
from dataclasses import replace
import importlib.util
import json
from pathlib import Path
import sys

GATE_PATH = Path(__file__).resolve().parents[2] / 'emery vpn orchestrator/deploy/device-gate/emery_device_gate.py'
spec = importlib.util.spec_from_file_location('concurrent_gate_test', GATE_PATH)
gate = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = gate
spec.loader.exec_module(gate)


def test_gate_keepalive_release_and_revocation(monkeypatch):
    async def run():
        released = asyncio.Event()
        revocation = asyncio.Event()
        seen = []
        conf = gate.Config('127.0.0.1', 0, 4, 'gate.example.com', 'a' * 64,
            '', '', 'http://127.0.0.1/internal/device-gate/authorize', 's' * 32,
            2, 2, 10, concurrent_sessions=True)
        server_gate = gate.DeviceGate(conf)
        def control(config, proof, *, lease=False):
            if lease:
                assert proof['release']
                seen.append('released')
                return {'ok': True}
            assert proof['lease_protocol'] == 1
            return {'allowed': True, 'assignment_id': 17, 'node_id': 4,
                'target_host': '127.0.0.1', 'target_port': 20000,
                'lease_token': 'x' * 43, 'lease_seconds': 60, 'renew_seconds': 15}
        monkeypatch.setattr(gate, '_authorize_sync', control)
        async def renewal(token, started):
            await revocation.wait()
            raise gate.GateError('revoked')
        monkeypatch.setattr(server_gate, '_renew', renewal)
        async def handle(reader, writer):
            try:
                await server_gate.handle(reader, writer)
            finally:
                released.set()
        server = await asyncio.start_server(handle, '127.0.0.1', 0)
        try:
            reader, writer = await asyncio.open_connection('127.0.0.1', server.sockets[0].getsockname()[1])
            challenge = json.loads(await reader.readline())
            assert challenge['version'] == 2
            proof = {**challenge, 'session_only': True, 'assignment_id': 17, 'node_id': 4,
                'gate_server_name': conf.server_name, 'gate_spki_sha256': conf.spki_sha256,
                'device_id': 'test-installation', 'timestamp': challenge['server_issued_at'],
                'client_nonce': 'c' * 32, 'signature': 'test-signature', 'signature_algorithm': 'SHA256withECDSA'}
            writer.write(gate._json_line(proof))
            await writer.drain()
            assert json.loads(await reader.readline()) == {'ok': True}
            writer.write(gate._json_line({'ping': True}))
            await writer.drain()
            assert json.loads(await reader.readline()) == {'ok': True}
            revocation.set()
            assert await asyncio.wait_for(reader.read(), 2) == b''
            await asyncio.wait_for(released.wait(), 2)
            assert seen == ['released']
            assert server_gate.slots._value == 10
            writer.close()
            await writer.wait_closed()
        finally:
            server.close()
            await server.wait_closed()
    asyncio.run(run())


def test_cancel_proxy_cancels_both_forwarding_tasks():
    async def run():
        stopped = []
        async def pipe(reader, writer):
            try:
                await asyncio.Event().wait()
            finally:
                stopped.append(True)
        old = gate._pipe
        gate._pipe = pipe
        try:
            task = asyncio.create_task(gate._proxy_bidirectional(None, None, None, None))
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            assert len(stopped) == 2
        finally:
            gate._pipe = old
    asyncio.run(run())


def test_authority_unavailable_stops_renewal(monkeypatch):
    async def run():
        conf = gate.Config('127.0.0.1', 0, 4, 'gate.example.com', 'a' * 64,
            '', '', 'http://127.0.0.1/internal/device-gate/authorize', 's' * 32, 2, 2, 10)
        original_sleep = asyncio.sleep
        async def immediate(_):
            await original_sleep(0)
        def unavailable(*args, **kwargs):
            raise gate.GateError('authorization unavailable')
        monkeypatch.setattr(gate.asyncio, 'sleep', immediate)
        monkeypatch.setattr(gate, '_authorize_sync', unavailable)
        import pytest
        with pytest.raises(gate.GateError, match='authorization unavailable'):
            await gate.DeviceGate(conf)._renew('token', gate.time.monotonic())
    asyncio.run(run())

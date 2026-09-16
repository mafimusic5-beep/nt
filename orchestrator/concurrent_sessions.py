"""Short-lived admission leases; no IP, traffic, DNS or session history.

All gateways must use the same SQLite authority. Gateways stop forwarding
before their lease expires, even when the control plane is unavailable.
"""
from __future__ import annotations

import hashlib
import secrets
import sqlite3
import time

import config

LEASE_SECONDS = 60
RENEW_SECONDS = 15


def enabled() -> bool:
    return config.CONCURRENT_SESSIONS_ENABLED


def ensure_storage(con: sqlite3.Connection) -> None:
    con.execute('''CREATE TABLE IF NOT EXISTS vpn_live_leases (
        token_hash TEXT PRIMARY KEY,
        device_row_id INTEGER NOT NULL REFERENCES code_devices(id) ON DELETE CASCADE,
        key_hash TEXT NOT NULL,
        assignment_id INTEGER NOT NULL,
        node_id INTEGER NOT NULL,
        expires_at REAL NOT NULL
    )''')
    con.execute('CREATE INDEX IF NOT EXISTS vpn_live_expiry ON vpn_live_leases(expires_at)')
    con.execute('CREATE INDEX IF NOT EXISTS vpn_live_device ON vpn_live_leases(device_row_id)')


def count(con: sqlite3.Connection, code: str) -> int:
    return con.execute('''SELECT COUNT(*) FROM (SELECT l.device_row_id, l.key_hash
        FROM vpn_live_leases l JOIN code_devices d ON d.id = l.device_row_id
        WHERE d.code = ? AND l.expires_at > ? GROUP BY l.device_row_id, l.key_hash)''', (code, time.time())).fetchone()[0]


def acquire(con: sqlite3.Connection, row, *, limit: int, assignment_id: int, node_id: int) -> dict:
    # Caller holds BEGIN IMMEDIATE across proof validation and admission.
    from device_auth import DeviceAuthError
    ensure_storage(con)
    now = time.time()
    con.execute('DELETE FROM vpn_live_leases WHERE expires_at <= ?', (now,))
    key_hash = hashlib.sha256(row['public_key'].encode()).hexdigest()
    present = con.execute('SELECT 1 FROM vpn_live_leases WHERE device_row_id = ? AND key_hash = ?',
                          (row['device_row_id'], key_hash)).fetchone()
    if not present and count(con, row['code']) >= limit:
        raise DeviceAuthError('concurrent_limit_reached', 409)
    token = secrets.token_urlsafe(32)
    con.execute('INSERT INTO vpn_live_leases VALUES (?, ?, ?, ?, ?, ?)', (
        hashlib.sha256(token.encode()).hexdigest(), row['device_row_id'],
        hashlib.sha256(row['public_key'].encode()).hexdigest(), assignment_id,
        node_id, now + LEASE_SECONDS))
    return {'lease_token': token, 'lease_seconds': LEASE_SECONDS, 'renew_seconds': RENEW_SECONDS}


def update(token: str, *, release: bool = False) -> dict:
    from device_auth import DeviceAuthError, _connect, _activation_row
    con = _connect()
    try:
        con.execute('BEGIN IMMEDIATE')
        ensure_storage(con)
        now = time.time()
        con.execute('DELETE FROM vpn_live_leases WHERE expires_at <= ?', (now,))
        digest = hashlib.sha256(token.encode()).hexdigest()
        if release:
            con.execute('DELETE FROM vpn_live_leases WHERE token_hash = ?', (digest,))
            con.commit()
            return {'ok': True}
        row = con.execute('''SELECT l.*, d.code, d.public_key, d.active,
            d.pool_assignment_id, d.pool_node_id, d.pool_status
            FROM vpn_live_leases l JOIN code_devices d ON d.id = l.device_row_id
            WHERE l.token_hash = ?''', (digest,)).fetchone()
        if row is None:
            con.commit()
            raise DeviceAuthError('session_expired', 403)
        _activation_row(con, row['code'])
        if (not row['active'] or row['pool_status'] != 'active'
                or row['assignment_id'] != row['pool_assignment_id']
                or row['node_id'] != row['pool_node_id']
                or row['key_hash'] != hashlib.sha256(row['public_key'].encode()).hexdigest()):
            con.execute('DELETE FROM vpn_live_leases WHERE token_hash = ?', (digest,))
            con.commit()
            raise DeviceAuthError('session_revoked', 403)
        con.execute('UPDATE vpn_live_leases SET expires_at = ? WHERE token_hash = ?',
                    (now + LEASE_SECONDS, digest))
        con.commit()
        return {'ok': True, 'lease_seconds': LEASE_SECONDS}
    finally:
        con.close()


def purge() -> None:
    from device_auth import _connect
    with _connect() as con:
        ensure_storage(con)
        con.execute('DELETE FROM vpn_live_leases WHERE expires_at <= ?', (time.time(),))
    con.close()

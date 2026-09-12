from __future__ import annotations

import sqlite3

from config import DATABASE_PATH


def _connect() -> sqlite3.Connection:
    con = sqlite3.connect(DATABASE_PATH, timeout=30.0)
    con.row_factory = sqlite3.Row
    return con


def ensure_alias_storage(con: sqlite3.Connection | None = None) -> None:
    owns_connection = con is None
    connection = con or _connect()
    try:
        connection.execute(
            '''
            CREATE TABLE IF NOT EXISTS device_pool_subject_aliases (
                code TEXT NOT NULL,
                device_id TEXT NOT NULL,
                subject_key TEXT NOT NULL,
                created_at_epoch INTEGER NOT NULL,
                PRIMARY KEY(code, device_id)
            )
            '''
        )
        if owns_connection:
            connection.commit()
    finally:
        if owns_connection:
            connection.close()


def get_pool_subject_alias(code: str, device_id: str) -> str:
    connection = _connect()
    try:
        ensure_alias_storage(connection)
        row = connection.execute(
            'SELECT subject_key FROM device_pool_subject_aliases WHERE code = ? AND device_id = ?',
            (code.strip(), device_id.strip()),
        ).fetchone()
        return str(row['subject_key'] or '').strip() if row else ''
    finally:
        connection.close()


def save_pool_subject_alias(
    con: sqlite3.Connection,
    *,
    code: str,
    device_id: str,
    subject_key: str,
    created_at_epoch: int,
) -> None:
    ensure_alias_storage(con)
    con.execute(
        '''
        INSERT INTO device_pool_subject_aliases(code, device_id, subject_key, created_at_epoch)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(code, device_id) DO UPDATE SET subject_key = excluded.subject_key
        ''',
        (code.strip(), device_id.strip(), subject_key.strip(), int(created_at_epoch)),
    )

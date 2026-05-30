import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Iterator

from config import DATABASE_PATH


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def future_iso(days: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).replace(microsecond=0).isoformat()


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    con = sqlite3.connect(DATABASE_PATH)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


def init_storage() -> None:
    with connect() as con:
        con.execute('CREATE TABLE IF NOT EXISTS activation_codes (id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT NOT NULL UNIQUE, status TEXT NOT NULL DEFAULT "active", device_id TEXT, note TEXT, created_at TEXT NOT NULL, expires_at TEXT, used_at TEXT)')
        con.execute('CREATE TABLE IF NOT EXISTS servers (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, region TEXT NOT NULL, config TEXT NOT NULL, is_active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL)')

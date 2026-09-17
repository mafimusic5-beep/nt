import sqlite3

import concurrent_sessions


def _connection() -> sqlite3.Connection:
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute(
        '''
        CREATE TABLE code_devices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_name TEXT NOT NULL DEFAULT '',
            platform TEXT NOT NULL DEFAULT 'android',
            app_version TEXT NOT NULL DEFAULT '',
            first_seen_at TEXT,
            last_seen_at TEXT
        )
        '''
    )
    return con


def _assert_minimized(row: sqlite3.Row) -> None:
    assert row["device_name"] == "Android-устройство"
    assert row["platform"] == "android"
    assert row["app_version"] == ""
    assert row["first_seen_at"] is None
    assert row["last_seen_at"] is None


def test_existing_legacy_device_metadata_is_scrubbed():
    con = _connection()
    con.execute(
        "INSERT INTO code_devices(device_name, platform, app_version, first_seen_at, last_seen_at) VALUES (?, ?, ?, ?, ?)",
        ("Samsung SM-S918B", "android", "718", "2026-09-01T10:00:00Z", "2026-09-17T06:00:00Z"),
    )

    concurrent_sessions.ensure_storage(con)
    _assert_minimized(con.execute("SELECT * FROM code_devices").fetchone())


def test_legacy_metadata_stays_minimized_after_writes():
    con = _connection()
    concurrent_sessions.ensure_storage(con)

    con.execute(
        "INSERT INTO code_devices(device_name, platform, app_version, first_seen_at, last_seen_at) VALUES (?, ?, ?, ?, ?)",
        ("Pixel 9", "android", "999", "first", "last"),
    )
    row = con.execute("SELECT * FROM code_devices").fetchone()
    _assert_minimized(row)

    con.execute(
        "UPDATE code_devices SET device_name=?, platform=?, app_version=?, first_seen_at=?, last_seen_at=? WHERE id=?",
        ("Another phone", "custom", "1000", "first2", "last2", row["id"]),
    )
    _assert_minimized(con.execute("SELECT * FROM code_devices WHERE id=?", (row["id"],)).fetchone())

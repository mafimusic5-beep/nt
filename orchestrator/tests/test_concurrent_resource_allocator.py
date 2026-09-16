import sqlite3

import pytest

from concurrent_resource_allocator import choose_recyclable_assignment, resource_state


@pytest.fixture()
def db():
    con = sqlite3.connect(':memory:')
    con.row_factory = sqlite3.Row
    con.executescript('''
        CREATE TABLE code_devices (
            id INTEGER PRIMARY KEY,
            code TEXT NOT NULL,
            device_id TEXT NOT NULL,
            activated_at TEXT,
            last_seen_at TEXT,
            active INTEGER NOT NULL DEFAULT 1,
            pool_assignment_id INTEGER,
            pool_status TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE vpn_live_leases (
            token_hash TEXT PRIMARY KEY,
            device_row_id INTEGER NOT NULL,
            key_hash TEXT NOT NULL,
            assignment_id INTEGER NOT NULL,
            node_id INTEGER NOT NULL,
            expires_at REAL NOT NULL
        );
    ''')
    yield con
    con.close()


def add_owner(con, row_id, assignment, *, seen, online=False, now=1000.0):
    con.execute(
        '''INSERT INTO code_devices
           (id, code, device_id, activated_at, last_seen_at, active,
            pool_assignment_id, pool_status)
           VALUES (?, 'CODE', ?, ?, ?, 1, ?, 'active')''',
        (row_id, f'device-{row_id}', seen, seen, assignment),
    )
    if online:
        con.execute(
            '''INSERT INTO vpn_live_leases
               VALUES (?, ?, 'key', ?, 1, ?)''',
            (f'token-{row_id}', row_id, assignment, now + 60),
        )
    con.commit()


def test_free_capacity_does_not_recycle(db):
    add_owner(db, 1, 101, seen='2026-01-01T00:00:00Z')
    assert resource_state(db, code='CODE', requesting_device_row_id=2, limit=2, now=1000) == 'free'
    assert choose_recyclable_assignment(
        db, code='CODE', requesting_device_row_id=2, limit=2, now=1000
    ) is None


def test_existing_owner_is_stable(db):
    add_owner(db, 1, 101, seen='2026-01-01T00:00:00Z', online=True)
    assert resource_state(db, code='CODE', requesting_device_row_id=1, limit=1, now=1000) == 'owned'
    assert choose_recyclable_assignment(
        db, code='CODE', requesting_device_row_id=1, limit=1, now=1000
    ) is None


def test_live_assignment_is_never_recycled(db):
    add_owner(db, 1, 101, seen='2026-01-01T00:00:00Z', online=True)
    assert resource_state(db, code='CODE', requesting_device_row_id=2, limit=1, now=1000) == 'busy'
    assert choose_recyclable_assignment(
        db, code='CODE', requesting_device_row_id=2, limit=1, now=1000
    ) is None


def test_expired_lease_makes_assignment_recyclable(db):
    add_owner(db, 1, 101, seen='2026-01-01T00:00:00Z')
    db.execute(
        "INSERT INTO vpn_live_leases VALUES ('expired', 1, 'key', 101, 1, 999)"
    )
    db.commit()
    assert resource_state(db, code='CODE', requesting_device_row_id=2, limit=1, now=1000) == 'recyclable'
    owner = choose_recyclable_assignment(
        db, code='CODE', requesting_device_row_id=2, limit=1, now=1000
    )
    assert owner is not None
    assert owner.assignment_id == 101


def test_oldest_offline_owner_is_recycled_first(db):
    add_owner(db, 1, 101, seen='2026-01-01T00:00:00Z')
    add_owner(db, 2, 102, seen='2026-02-01T00:00:00Z')
    owner = choose_recyclable_assignment(
        db, code='CODE', requesting_device_row_id=3, limit=2, now=1000
    )
    assert owner is not None
    assert owner.device_row_id == 1


@pytest.mark.parametrize('limit', [0, 3, 4, 6])
def test_rejects_unknown_tariff_capacity(db, limit):
    with pytest.raises(ValueError, match='unsupported_concurrent_limit'):
        resource_state(db, code='CODE', requesting_device_row_id=1, limit=limit, now=1000)

import sqlite3

import pytest

from concurrent_resource_allocator import (
    assignment_for_device,
    choose_recyclable_assignment,
    resource_state,
    release_candidate,
    claim_assignment_release,
    clear_assignment,
    restore_assignment_release,
    transfer_assignment,
)


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
            pool_status TEXT NOT NULL DEFAULT '',
            pool_confirmation_token TEXT NOT NULL DEFAULT '',
            pool_node_id INTEGER,
            pool_node_name TEXT NOT NULL DEFAULT '',
            pool_region TEXT NOT NULL DEFAULT '',
            pool_config TEXT NOT NULL DEFAULT '',
            pool_config_revision INTEGER NOT NULL DEFAULT 0,
            pool_speed_limit_mbps INTEGER NOT NULL DEFAULT 0,
            pool_client_port INTEGER,
            pool_gate_host TEXT NOT NULL DEFAULT '',
            pool_gate_port INTEGER,
            pool_gate_server_name TEXT NOT NULL DEFAULT '',
            pool_gate_spki_sha256 TEXT NOT NULL DEFAULT '',
            pool_entitlement_hash TEXT NOT NULL DEFAULT '',
            pool_entitlement_expires_at TEXT NOT NULL DEFAULT '',
            pool_updated_at TEXT
        );
        CREATE UNIQUE INDEX idx_assignment ON code_devices(pool_assignment_id)
        WHERE pool_assignment_id IS NOT NULL;
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
            pool_assignment_id, pool_status, pool_node_id, pool_node_name,
            pool_region, pool_config, pool_config_revision,
            pool_speed_limit_mbps, pool_client_port, pool_gate_host,
            pool_gate_port, pool_gate_server_name, pool_gate_spki_sha256,
            pool_entitlement_hash, pool_entitlement_expires_at)
           VALUES (?, 'CODE', ?, ?, ?, 1, ?, 'active', 1, 'Node', 'de', ?, 7,
                   50, 20000, 'gate.example', 443, 'gate.example', ?, 'entitlement',
                   '2026-12-31T00:00:00+00:00')''',
        (
            row_id,
            f'device-{row_id}',
            seen,
            seen,
            assignment,
            f'vless://uuid@127.0.0.1:20000?eg_assignment={assignment}',
            'a' * 64,
        ),
    )
    if online:
        con.execute(
            '''INSERT INTO vpn_live_leases
               VALUES (?, ?, 'key', ?, 1, ?)''',
            (f'token-{row_id}', row_id, assignment, now + 60),
        )
    con.commit()


def add_registration(con, row_id, *, seen='2026-03-01T00:00:00Z'):
    con.execute(
        '''INSERT INTO code_devices
           (id, code, device_id, activated_at, last_seen_at, active)
           VALUES (?, 'CODE', ?, ?, ?, 1)''',
        (row_id, f'device-{row_id}', seen, seen),
    )
    con.commit()


def test_free_capacity_does_not_recycle(db):
    add_owner(db, 1, 101, seen='2026-01-01T00:00:00Z')
    add_registration(db, 2)
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
    add_registration(db, 2)
    assert resource_state(db, code='CODE', requesting_device_row_id=2, limit=1, now=1000) == 'busy'
    assert choose_recyclable_assignment(
        db, code='CODE', requesting_device_row_id=2, limit=1, now=1000
    ) is None


def test_expired_lease_makes_assignment_recyclable(db):
    add_owner(db, 1, 101, seen='2026-01-01T00:00:00Z')
    add_registration(db, 2)
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
    add_registration(db, 3)
    owner = choose_recyclable_assignment(
        db, code='CODE', requesting_device_row_id=3, limit=2, now=1000
    )
    assert owner is not None
    assert owner.device_row_id == 1


def test_transfer_moves_assignment_without_duplication(db):
    add_owner(db, 1, 101, seen='2026-01-01T00:00:00Z')
    add_registration(db, 2)

    moved = transfer_assignment(
        db,
        from_device_row_id=1,
        to_device_row_id=2,
        now=1000,
    )
    db.commit()

    assert moved['pool_assignment_id'] == 101
    assert assignment_for_device(db, device_row_id=1) is None
    current = assignment_for_device(db, device_row_id=2)
    assert current is not None
    assert current['pool_assignment_id'] == 101
    assert current['pool_client_port'] == 20000


def test_transfer_refuses_live_owner(db):
    add_owner(db, 1, 101, seen='2026-01-01T00:00:00Z', online=True)
    add_registration(db, 2)

    with pytest.raises(RuntimeError, match='resource_owner_online'):
        transfer_assignment(
            db,
            from_device_row_id=1,
            to_device_row_id=2,
            now=1000,
        )

    assert assignment_for_device(db, device_row_id=1) is not None
    assert assignment_for_device(db, device_row_id=2) is None


@pytest.mark.parametrize('limit', [0, 3, 4, 6])
def test_rejects_unknown_tariff_capacity(db, limit):
    with pytest.raises(ValueError, match='unsupported_concurrent_limit'):
        resource_state(db, code='CODE', requesting_device_row_id=1, limit=limit, now=1000)

def test_downgrade_release_keeps_live_owner(db):
    add_owner(db, 1, 101, seen='2026-01-01T00:00:00Z', online=True)
    add_owner(db, 2, 102, seen='2026-02-01T00:00:00Z')
    add_owner(db, 3, 103, seen='2026-03-01T00:00:00Z')
    victim = release_candidate(db, code='CODE', limit=1, now=1000)
    assert victim is not None
    assert victim.device_row_id in {2, 3}
    assert victim.device_row_id != 1


def test_clear_assignment_refuses_live_owner(db):
    add_owner(db, 1, 101, seen='2026-01-01T00:00:00Z', online=True)
    assert clear_assignment(db, device_row_id=1, assignment_id=101, now=1000) is False
    assert assignment_for_device(db, device_row_id=1) is not None


def test_downgrade_five_to_two_has_three_release_candidates(db):
    for row_id in range(1, 6):
        add_owner(db, row_id, 100 + row_id, seen=f'2026-01-0{row_id}T00:00:00Z')
    released = []
    while True:
        victim = release_candidate(db, code='CODE', limit=2, now=1000)
        if victim is None:
            break
        assert clear_assignment(
            db,
            device_row_id=victim.device_row_id,
            assignment_id=victim.assignment_id,
            now=1000,
        )
        db.commit()
        released.append(victim.assignment_id)
    assert len(released) == 3

def test_release_claim_hides_resource_until_restore(db):
    add_owner(db, 1, 101, seen='2026-01-01T00:00:00Z')
    add_owner(db, 2, 102, seen='2026-02-01T00:00:00Z')
    victim = release_candidate(db, code='CODE', limit=1, now=1000)
    assert victim is not None
    previous = claim_assignment_release(
        db,
        device_row_id=victim.device_row_id,
        assignment_id=victim.assignment_id,
        now=1000,
    )
    assert previous == 'active'
    db.commit()
    assert release_candidate(db, code='CODE', limit=1, now=1000) is None
    assert restore_assignment_release(
        db,
        device_row_id=victim.device_row_id,
        assignment_id=victim.assignment_id,
        previous_status=previous,
    )
    db.commit()
    assert release_candidate(db, code='CODE', limit=1, now=1000) is not None


def test_release_claim_refuses_live_assignment(db):
    add_owner(db, 1, 101, seen='2026-01-01T00:00:00Z', online=True)
    assert claim_assignment_release(
        db, device_row_id=1, assignment_id=101, now=1000
    ) is None

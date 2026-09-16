from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    p = Path(path)
    value = p.read_text()
    if old not in value:
        raise SystemExit(f'anchor missing: {path}: {old[:100]!r}')
    if value.count(old) != 1:
        raise SystemExit(f'anchor not unique: {path}: {old[:100]!r}')
    p.write_text(value.replace(old, new, 1))


def replace_between(path: str, start: str, end: str, new: str) -> None:
    p = Path(path)
    value = p.read_text()
    a = value.find(start)
    if a < 0:
        raise SystemExit(f'start missing: {path}')
    b = value.find(end, a)
    if b < 0:
        raise SystemExit(f'end missing: {path}')
    p.write_text(value[:a] + new + value[b:])


def append_once(path: str, marker: str, block: str) -> None:
    p = Path(path)
    value = p.read_text()
    if marker in value:
        return
    p.write_text(value.rstrip() + '\n\n' + block.strip() + '\n')


allocator = 'orchestrator/concurrent_resource_allocator.py'
if 'def claim_assignment_release(' not in Path(allocator).read_text():
    insert = """def claim_assignment_release(
    con: sqlite3.Connection,
    *,
    device_row_id: int,
    assignment_id: int,
    now: float | None = None,
) -> str | None:
    \"\"\"Hide one exact offline owner from allocation while remote Xray release runs.\"\"\"
    now_epoch = time.time() if now is None else float(now)
    live = con.execute(
        'SELECT 1 FROM vpn_live_leases WHERE device_row_id = ? AND expires_at > ? LIMIT 1',
        (int(device_row_id), now_epoch),
    ).fetchone()
    if live:
        return None
    row = con.execute(
        '''SELECT pool_status FROM code_devices
           WHERE id = ? AND active = 1 AND pool_assignment_id = ?''',
        (int(device_row_id), int(assignment_id)),
    ).fetchone()
    if row is None or str(row['pool_status']) not in {'active', 'pending'}:
        return None
    previous_status = str(row['pool_status'])
    cursor = con.execute(
        '''UPDATE code_devices
           SET pool_status = 'release_pending', pool_updated_at = ?
           WHERE id = ? AND active = 1 AND pool_assignment_id = ?
             AND pool_status = ?''',
        (
            datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            int(device_row_id), int(assignment_id), previous_status,
        ),
    )
    return previous_status if cursor.rowcount == 1 else None


def restore_assignment_release(
    con: sqlite3.Connection,
    *,
    device_row_id: int,
    assignment_id: int,
    previous_status: str,
) -> bool:
    if previous_status not in {'active', 'pending'}:
        raise ValueError('invalid_previous_pool_status')
    cursor = con.execute(
        '''UPDATE code_devices
           SET pool_status = ?, pool_updated_at = ?
           WHERE id = ? AND active = 1 AND pool_assignment_id = ?
             AND pool_status = 'release_pending' ''',
        (
            previous_status,
            datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            int(device_row_id), int(assignment_id),
        ),
    )
    return cursor.rowcount == 1


"""
    replace_once(allocator, 'def clear_assignment(\n', insert + 'def clear_assignment(\n')

bridge = 'orchestrator/pool_reservation_bridge.py'
replace_once(
    bridge,
    "from concurrent_resource_allocator import (\n    assignment_for_device, clear_assignment, plan_resource,\n    release_candidate, transfer_assignment,\n)\n",
    "from concurrent_resource_allocator import (\n    assignment_for_device, claim_assignment_release, clear_assignment, plan_resource,\n    release_candidate, restore_assignment_release, transfer_assignment,\n)\n",
)

new_reconcile = """def reconcile_concurrent_resources() -> int:
    if not concurrent_sessions.enabled() or not is_enabled():
        return 0
    released = 0
    while True:
        con = sqlite3.connect(DATABASE_PATH, timeout=30.0)
        con.row_factory = sqlite3.Row
        con.execute('PRAGMA foreign_keys = ON')
        candidate = None
        code = ''
        previous_status = ''
        try:
            con.execute('BEGIN IMMEDIATE')
            concurrent_sessions.ensure_storage(con)
            rows = con.execute(
                '''
                SELECT code, max_devices
                FROM activation_codes
                WHERE status = 'active' AND max_devices IN (1, 2, 5)
                ORDER BY id ASC
                '''
            ).fetchall()
            for row in rows:
                maybe = release_candidate(
                    con, code=str(row['code']), limit=int(row['max_devices'])
                )
                if maybe is None:
                    continue
                claimed_status = claim_assignment_release(
                    con,
                    device_row_id=maybe.device_row_id,
                    assignment_id=maybe.assignment_id,
                )
                if claimed_status is None:
                    continue
                candidate = maybe
                code = str(row['code'])
                previous_status = claimed_status
                break
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()
        if candidate is None:
            return released

        try:
            release_assignment(
                assignment_id=candidate.assignment_id,
                formatted_code=code,
                device_id=candidate.device_id,
            )
        except PoolBridgeError:
            con = sqlite3.connect(DATABASE_PATH, timeout=30.0)
            con.row_factory = sqlite3.Row
            con.execute('PRAGMA foreign_keys = ON')
            try:
                con.execute('BEGIN IMMEDIATE')
                restore_assignment_release(
                    con,
                    device_row_id=candidate.device_row_id,
                    assignment_id=candidate.assignment_id,
                    previous_status=previous_status,
                )
                con.commit()
            except Exception:
                con.rollback()
                raise
            finally:
                con.close()
            return released

        con = sqlite3.connect(DATABASE_PATH, timeout=30.0)
        con.row_factory = sqlite3.Row
        con.execute('PRAGMA foreign_keys = ON')
        try:
            con.execute('BEGIN IMMEDIATE')
            concurrent_sessions.ensure_storage(con)
            if not clear_assignment(
                con,
                device_row_id=candidate.device_row_id,
                assignment_id=candidate.assignment_id,
            ):
                raise RuntimeError('released_pool_assignment_not_clearable')
            released += 1
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()


"""
replace_between(
    bridge,
    'def reconcile_concurrent_resources() -> int:\n',
    'def confirm_assignment(assignment_id: int, confirmation_token: str) -> dict[str, Any]:\n',
    new_reconcile,
)

replace_once(
    bridge,
    '        SELECT c.code, c.plan, c.expires_at, c.max_devices, d.id AS device_row_id\n',
    '        SELECT c.code, c.plan, c.expires_at, c.max_devices, d.id AS device_row_id,\n               d.pool_status AS pool_status\n',
)
replace_once(
    bridge,
    "        if row is None:\n            raise PoolBridgeError('device_not_registered', 403)\n        code = str(row['code'])\n",
    "        if row is None:\n            raise PoolBridgeError('device_not_registered', 403)\n        if str(row['pool_status'] or '') == 'release_pending':\n            raise PoolBridgeError('pool_assignment_maintenance_in_progress', 409)\n        code = str(row['code'])\n",
)

allocator_tests = 'orchestrator/tests/test_concurrent_resource_allocator.py'
replace_once(
    allocator_tests,
    '    release_candidate,\n    clear_assignment,\n    transfer_assignment,\n)',
    '    release_candidate,\n    claim_assignment_release,\n    clear_assignment,\n    restore_assignment_release,\n    transfer_assignment,\n)',
)
append_once(
    allocator_tests,
    'def test_release_claim_hides_resource_until_restore',
    """
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
""",
)

pool_tests = 'emery vpn orchestrator/tests/test_pool_assignments.py'
append_once(
    pool_tests,
    'def test_release_failure_preserves_capacity_for_retry',
    """
def test_release_failure_preserves_capacity_for_retry(db_session):
    class FailingRemoveTransport(FakeCredentialTransport):
        def remove(self, node, assignment):
            self.removed.append(assignment.id)
            return CredentialMutationResult(False, 'remove_failed')

    node = add_node(db_session)
    transport = FailingRemoveTransport()
    service = PoolAssignmentService(db_session, transport)
    prepared = service.prepare(request('a'))
    service.confirm(PoolReservationConfirmRequest(
        assignment_id=prepared.assignment_id,
        confirmation_token=prepared.confirmation_token,
    ))
    with pytest.raises(HTTPException) as error:
        service.release(PoolReservationReleaseRequest(
            assignment_id=prepared.assignment_id,
            subject_key='a' * 64,
        ))
    assignment = db_session.get(VpnAssignment, prepared.assignment_id)
    assert error.value.status_code == 503
    assert assignment.status == 'revocation_pending'
    assert db_session.get(VpnNode, node.id).current_clients == 1


def test_released_port_is_reusable(db_session):
    node = add_node(db_session)
    transport = FakeCredentialTransport()
    service = PoolAssignmentService(db_session, transport)
    first = service.prepare(request('a'))
    first_port = db_session.get(VpnAssignment, first.assignment_id).client_port
    service.confirm(PoolReservationConfirmRequest(
        assignment_id=first.assignment_id,
        confirmation_token=first.confirmation_token,
    ))
    service.release(PoolReservationReleaseRequest(
        assignment_id=first.assignment_id,
        subject_key='a' * 64,
    ))
    second = service.prepare(request('b'))
    second_port = db_session.get(VpnAssignment, second.assignment_id).client_port
    assert second_port == first_port
    assert db_session.get(VpnNode, node.id).current_clients == 1
""",
)

print('release claim race fix applied')

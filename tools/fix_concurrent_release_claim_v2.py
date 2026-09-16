from pathlib import Path
import runpy


runpy.run_path('tools/fix_concurrent_release_claim.py', run_name='__main__')


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


allocator = 'orchestrator/concurrent_resource_allocator.py'
replace_once(
    allocator,
    """    cursor = con.execute(
        '''UPDATE code_devices
           SET pool_status = 'release_pending', pool_updated_at = ?
           WHERE id = ? AND active = 1 AND pool_assignment_id = ?
             AND pool_status = ?''',
        (
            datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            int(device_row_id), int(assignment_id), previous_status,
        ),
    )
""",
    """    pending_status = f'release_pending_{previous_status}'
    cursor = con.execute(
        '''UPDATE code_devices
           SET pool_status = ?, pool_updated_at = ?
           WHERE id = ? AND active = 1 AND pool_assignment_id = ?
             AND pool_status = ?''',
        (
            pending_status,
            datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            int(device_row_id), int(assignment_id), previous_status,
        ),
    )
""",
)
replace_once(
    allocator,
    """    cursor = con.execute(
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
""",
    """    cursor = con.execute(
        '''UPDATE code_devices
           SET pool_status = ?, pool_updated_at = ?
           WHERE id = ? AND active = 1 AND pool_assignment_id = ?
             AND pool_status = ?''',
        (
            previous_status,
            datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            int(device_row_id), int(assignment_id),
            f'release_pending_{previous_status}',
        ),
    )
""",
)

bridge = 'orchestrator/pool_reservation_bridge.py'
replace_once(
    bridge,
    "        if str(row['pool_status'] or '') == 'release_pending':\n            raise PoolBridgeError('pool_assignment_maintenance_in_progress', 409)\n",
    "        if str(row['pool_status'] or '').startswith('release_pending_'):\n            raise PoolBridgeError('pool_assignment_maintenance_in_progress', 409)\n",
)

reconcile = """def reconcile_concurrent_resources() -> int:
    if not concurrent_sessions.enabled() or not is_enabled():
        return 0
    released = 0
    while True:
        con = sqlite3.connect(DATABASE_PATH, timeout=30.0)
        con.row_factory = sqlite3.Row
        con.execute('PRAGMA foreign_keys = ON')
        device_row_id = 0
        assignment_id = 0
        device_id = ''
        code = ''
        previous_status = ''
        try:
            con.execute('BEGIN IMMEDIATE')
            concurrent_sessions.ensure_storage(con)

            pending = con.execute(
                '''
                SELECT id, code, device_id, pool_assignment_id, pool_status
                FROM code_devices
                WHERE active = 1
                  AND pool_assignment_id IS NOT NULL
                  AND pool_status IN ('release_pending_active', 'release_pending_pending')
                ORDER BY id ASC
                LIMIT 1
                '''
            ).fetchone()
            if pending is not None:
                device_row_id = int(pending['id'])
                assignment_id = int(pending['pool_assignment_id'])
                device_id = str(pending['device_id'])
                code = str(pending['code'])
                previous_status = str(pending['pool_status']).removeprefix('release_pending_')
            else:
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
                    device_row_id = maybe.device_row_id
                    assignment_id = maybe.assignment_id
                    device_id = maybe.device_id
                    code = str(row['code'])
                    previous_status = claimed_status
                    break
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

        if assignment_id <= 0:
            return released

        try:
            release_assignment(
                assignment_id=assignment_id,
                formatted_code=code,
                device_id=device_id,
            )
        except PoolBridgeError as error:
            if error.reason == 'assignment_owner_changed':
                con = sqlite3.connect(DATABASE_PATH, timeout=30.0)
                con.row_factory = sqlite3.Row
                con.execute('PRAGMA foreign_keys = ON')
                try:
                    con.execute('BEGIN IMMEDIATE')
                    restore_assignment_release(
                        con,
                        device_row_id=device_row_id,
                        assignment_id=assignment_id,
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
                device_row_id=device_row_id,
                assignment_id=assignment_id,
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
    reconcile,
)

conftest = 'emery vpn orchestrator/tests/conftest.py'
replace_once(
    conftest,
    'from src.backend.core.bootstrap import seed_plans\n',
    'from src.backend.core.assignment_port_lifecycle import install_assignment_port_lifecycle\nfrom src.backend.core.bootstrap import seed_plans\n',
)
replace_once(
    conftest,
    'def db_session(tmp_path: Path) -> Session:\n    db_file = tmp_path / "test.db"\n',
    'def db_session(tmp_path: Path) -> Session:\n    install_assignment_port_lifecycle()\n    db_file = tmp_path / "test.db"\n',
)

print('durable release claim fix applied')

from pathlib import Path


def text(path: str) -> str:
    return Path(path).read_text()


def write(path: str, value: str) -> None:
    Path(path).write_text(value)


def replace_once(path: str, old: str, new: str) -> None:
    value = text(path)
    if old not in value:
        raise SystemExit(f'anchor missing: {path}: {old[:100]!r}')
    if value.count(old) != 1:
        raise SystemExit(f'anchor not unique: {path}: {old[:100]!r}')
    write(path, value.replace(old, new, 1))


def replace_between(path: str, start: str, end: str, new: str) -> None:
    value = text(path)
    a = value.find(start)
    if a < 0:
        raise SystemExit(f'start missing: {path}')
    b = value.find(end, a)
    if b < 0:
        raise SystemExit(f'end missing: {path}')
    write(path, value[:a] + new + value[b:])


def append_once(path: str, marker: str, block: str) -> None:
    value = text(path)
    if marker in value:
        return
    write(path, value.rstrip() + '\n\n' + block.strip() + '\n')


schema = 'emery vpn orchestrator/src/backend/schemas/pool_bridge.py'
if 'class PoolReservationReleaseRequest' not in text(schema):
    replace_once(
        schema,
        'class PoolReservationConfirmRequest(BaseModel):\n',
        """class PoolReservationReleaseRequest(BaseModel):
    assignment_id: int = Field(gt=0)
    subject_type: str = Field(default='legacy_device', pattern=r'^(legacy_device|native_device)$')
    subject_key: str = Field(pattern=r'^[a-f0-9]{64}$')


class PoolReservationReleaseResponse(BaseModel):
    ok: bool = True
    assignment_id: int
    status: str


class PoolReservationConfirmRequest(BaseModel):
""",
    )

routes = 'emery vpn orchestrator/src/backend/api/routes.py'
if 'PoolReservationReleaseRequest' not in text(routes):
    replace_once(
        routes,
        '    PoolReservationPrepareRequest,\n    PoolReservationRebindRequest,\n    PoolReservationResponse,\n',
        '    PoolReservationPrepareRequest,\n    PoolReservationRebindRequest,\n    PoolReservationReleaseRequest,\n    PoolReservationReleaseResponse,\n    PoolReservationResponse,\n',
    )
if '/internal/pool/assignments/release' not in text(routes):
    anchor = '@router.post(\n    "/internal/pool/assignments/confirm",'
    insert = """@router.post(
    '/internal/pool/assignments/release',
    response_model=PoolReservationReleaseResponse,
    dependencies=[Depends(require_pool_bridge_api_key)],
)
def internal_release_pool_assignment(
    payload: PoolReservationReleaseRequest,
    db: Session = Depends(get_db),
):
    return PoolAssignmentService(db).release(payload)


"""
    replace_once(routes, anchor, insert + anchor)

service = 'emery vpn orchestrator/src/backend/services/pool_assignment_service.py'
if 'PoolReservationReleaseRequest' not in text(service):
    replace_once(
        service,
        '    PoolReservationPrepareRequest,\n    PoolReservationRebindRequest,\n    PoolReservationResponse,\n',
        '    PoolReservationPrepareRequest,\n    PoolReservationRebindRequest,\n    PoolReservationReleaseRequest,\n    PoolReservationReleaseResponse,\n    PoolReservationResponse,\n',
    )
if '    def release(self, req: PoolReservationReleaseRequest)' not in text(service):
    replacement = """    def rebind(self, req: PoolReservationRebindRequest) -> PoolReservationResponse:
        self._require_enabled()
        now = self._now()
        requested_expiry = self._as_utc(req.entitlement_expires_at)
        if requested_expiry <= now:
            raise HTTPException(status_code=403, detail='entitlement_expired')

        assignment = self.db.get(VpnAssignment, req.assignment_id)
        if assignment is None:
            raise HTTPException(status_code=404, detail='assignment_not_found')
        if assignment.status != 'active' or not assignment.device_gate_enforced:
            raise HTTPException(status_code=409, detail='assignment_not_active')

        conflict = self.db.scalar(
            select(VpnAssignment).where(
                VpnAssignment.subject_type == req.subject_type,
                VpnAssignment.subject_key == req.subject_key,
                VpnAssignment.id != assignment.id,
            )
        )
        if conflict is not None:
            raise HTTPException(status_code=409, detail='assignment_subject_conflict')

        stored_expiry = self._as_utc(assignment.entitlement_expires_at)
        values = {'subject_type': req.subject_type, 'subject_key': req.subject_key}
        if requested_expiry >= stored_expiry:
            values['entitlement_hash'] = req.entitlement_hash
            values['entitlement_expires_at'] = req.entitlement_expires_at
        try:
            changed = self.db.execute(
                update(VpnAssignment)
                .where(
                    VpnAssignment.id == assignment.id,
                    VpnAssignment.status == 'active',
                    VpnAssignment.device_gate_enforced.is_(True),
                )
                .values(**values)
            )
            if changed.rowcount != 1:
                self.db.rollback()
                raise HTTPException(status_code=409, detail='assignment_state_changed_retry')
            self.audit.write(
                'system', 'pool_bridge', 'vpn_assignment_rebound',
                'vpn_assignment', str(assignment.id), {'node_id': assignment.node_id},
            )
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise HTTPException(status_code=409, detail='assignment_subject_conflict') from exc
        assignment = self.db.get(VpnAssignment, req.assignment_id)
        return self._response(assignment)

    def release(self, req: PoolReservationReleaseRequest) -> PoolReservationReleaseResponse:
        self._require_enabled()
        assignment = self.db.get(VpnAssignment, req.assignment_id)
        if assignment is None:
            raise HTTPException(status_code=404, detail='assignment_not_found')
        if assignment.subject_type != req.subject_type or assignment.subject_key != req.subject_key:
            raise HTTPException(status_code=409, detail='assignment_owner_changed')
        if assignment.status == 'revoked':
            return PoolReservationReleaseResponse(
                assignment_id=assignment.id, status='revoked'
            )

        now = self._now()
        previous_status = assignment.status
        if previous_status == 'revoking':
            if assignment.prepare_expires_at is not None and self._as_utc(assignment.prepare_expires_at) > now:
                raise HTTPException(status_code=409, detail='assignment_release_in_progress')
        elif previous_status not in {'active', 'pending', 'revocation_pending'}:
            raise HTTPException(status_code=409, detail='assignment_not_releasable')

        claim = update(VpnAssignment).where(
            VpnAssignment.id == assignment.id,
            VpnAssignment.status == previous_status,
            VpnAssignment.subject_type == req.subject_type,
            VpnAssignment.subject_key == req.subject_key,
        )
        if previous_status == 'revoking':
            claim = claim.where(
                or_(VpnAssignment.prepare_expires_at.is_(None), VpnAssignment.prepare_expires_at <= now)
            )
        claimed = self.db.execute(
            claim.values(
                status='revoking',
                last_error='',
                prepare_expires_at=now + timedelta(
                    seconds=max(int(settings.pool_assignment_prepare_ttl_seconds), 60)
                ),
            )
        )
        self.db.commit()
        if claimed.rowcount != 1:
            raise HTTPException(status_code=409, detail='assignment_owner_or_state_changed')
        self.db.refresh(assignment)

        node = self.db.get(VpnNode, assignment.node_id)
        if node is None:
            assignment.status = 'revocation_pending'
            assignment.last_error = 'assigned_node_missing'
            self.db.commit()
            raise HTTPException(status_code=503, detail='assignment_release_failed')
        try:
            result = self.transport.remove(node, assignment)
        except Exception as exc:  # noqa: BLE001
            result = CredentialMutationResult(False, f'release_failed:{type(exc).__name__}')
        if not result.ok:
            assignment.status = 'revocation_pending'
            assignment.last_error = result.detail[:500]
            self.db.commit()
            raise HTTPException(status_code=503, detail='assignment_release_failed')

        assignment.status = 'revoked'
        assignment.last_error = ''
        assignment.prepare_expires_at = None
        self.db.execute(
            update(VpnNode)
            .where(VpnNode.id == node.id, VpnNode.current_clients > 0)
            .values(current_clients=VpnNode.current_clients - 1)
        )
        self.audit.write(
            'system', 'pool_bridge', 'vpn_assignment_revoked',
            'vpn_assignment', str(assignment.id),
            {'node_id': node.id, 'reason': 'capacity_reduced'},
        )
        self.db.commit()
        return PoolReservationReleaseResponse(
            assignment_id=assignment.id, status=assignment.status
        )

"""
    replace_between(
        service,
        '    def rebind(self, req: PoolReservationRebindRequest) -> PoolReservationResponse:\n',
        '    def confirm(self, req: PoolReservationConfirmRequest) -> PoolReservationConfirmResponse:\n',
        replacement,
    )

allocator = 'orchestrator/concurrent_resource_allocator.py'
if 'def release_candidate(' not in text(allocator):
    insert = """def release_candidate(
    con: sqlite3.Connection,
    *,
    code: str,
    limit: int,
    now: float | None = None,
) -> ResourceOwner | None:
    if limit not in (1, 2, 5):
        raise ValueError('unsupported_concurrent_limit')
    now_epoch = time.time() if now is None else float(now)
    owners = _owners(con, code, now_epoch)
    if len(owners) <= limit:
        return None
    online_count = sum(1 for owner in owners if owner.online)
    offline = [owner for owner in owners if not owner.online]
    keep_offline = max(limit - online_count, 0)
    protected = {
        owner.device_row_id
        for owner in sorted(
            offline,
            key=lambda owner: (owner.last_seen_at, owner.device_row_id),
            reverse=True,
        )[:keep_offline]
    }
    releasable = [owner for owner in offline if owner.device_row_id not in protected]
    if not releasable:
        return None
    return min(releasable, key=lambda owner: (owner.last_seen_at, owner.device_row_id))


def clear_assignment(
    con: sqlite3.Connection,
    *,
    device_row_id: int,
    assignment_id: int,
    now: float | None = None,
) -> bool:
    now_epoch = time.time() if now is None else float(now)
    live = con.execute(
        'SELECT 1 FROM vpn_live_leases WHERE device_row_id = ? AND expires_at > ? LIMIT 1',
        (int(device_row_id), now_epoch),
    ).fetchone()
    if live:
        return False
    cursor = con.execute(
        '''
        UPDATE code_devices
        SET pool_assignment_id = NULL,
            pool_status = '', pool_confirmation_token = '', pool_node_id = NULL,
            pool_node_name = '', pool_region = '', pool_config = '',
            pool_config_revision = 0, pool_speed_limit_mbps = 0,
            pool_client_port = NULL, pool_gate_host = '', pool_gate_port = NULL,
            pool_gate_server_name = '', pool_gate_spki_sha256 = '',
            pool_entitlement_hash = '', pool_entitlement_expires_at = '',
            pool_updated_at = ?
        WHERE id = ? AND active = 1 AND pool_assignment_id = ?
        ''',
        (
            datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            int(device_row_id), int(assignment_id),
        ),
    )
    return cursor.rowcount == 1


"""
    replace_once(allocator, 'def choose_recyclable_assignment(\n', insert + 'def choose_recyclable_assignment(\n')

bridge = 'orchestrator/pool_reservation_bridge.py'
if 'release_candidate' not in text(bridge).split('\n', 30)[0:30]:
    replace_once(
        bridge,
        'from concurrent_resource_allocator import assignment_for_device, plan_resource, transfer_assignment\n',
        'from concurrent_resource_allocator import (\n    assignment_for_device, clear_assignment, plan_resource,\n    release_candidate, transfer_assignment,\n)\n',
    )
if 'def release_assignment(' not in text(bridge):
    insert = """def release_assignment(*, assignment_id: int, formatted_code: str, device_id: str) -> dict[str, Any]:
    if assignment_id <= 0:
        raise PoolBridgeError('invalid_pool_assignment', 409)
    response = _request(
        '/api/v1/internal/pool/assignments/release',
        {
            'assignment_id': assignment_id,
            'subject_type': 'legacy_device',
            'subject_key': _subject_key(formatted_code, device_id),
        },
    )
    if (
        response.get('ok') is not True
        or int(response.get('assignment_id') or 0) != assignment_id
        or str(response.get('status') or '') != 'revoked'
    ):
        raise PoolBridgeError('pool_release_failed', 503)
    return response


def reconcile_concurrent_resources() -> int:
    if not concurrent_sessions.enabled() or not is_enabled():
        return 0
    released = 0
    while True:
        con = sqlite3.connect(DATABASE_PATH, timeout=30.0)
        con.row_factory = sqlite3.Row
        con.execute('PRAGMA foreign_keys = ON')
        candidate = None
        code = ''
        limit = 0
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
                if maybe is not None:
                    candidate = maybe
                    code = str(row['code'])
                    limit = int(row['max_devices'])
                    break
            con.commit()
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
            return released

        con = sqlite3.connect(DATABASE_PATH, timeout=30.0)
        con.row_factory = sqlite3.Row
        con.execute('PRAGMA foreign_keys = ON')
        try:
            con.execute('BEGIN IMMEDIATE')
            concurrent_sessions.ensure_storage(con)
            current = release_candidate(con, code=code, limit=limit)
            if (
                current is not None
                and current.device_row_id == candidate.device_row_id
                and current.assignment_id == candidate.assignment_id
                and clear_assignment(
                    con,
                    device_row_id=candidate.device_row_id,
                    assignment_id=candidate.assignment_id,
                )
            ):
                released += 1
            con.commit()
        finally:
            con.close()


"""
    replace_once(
        bridge,
        'def confirm_assignment(assignment_id: int, confirmation_token: str) -> dict[str, Any]:\n',
        insert + 'def confirm_assignment(assignment_id: int, confirmation_token: str) -> dict[str, Any]:\n',
    )

api = 'orchestrator/api.py'
if 'reconcile_concurrent_resources' not in text(api).split('from storage', 1)[0]:
    replace_once(
        api,
        '    refresh_stored_assignment,\n)',
        '    refresh_stored_assignment,\n    reconcile_concurrent_resources,\n)',
    )
if 'await asyncio.to_thread(reconcile_concurrent_resources)' not in text(api):
    replace_once(
        api,
        '                    await asyncio.to_thread(concurrent_sessions.purge)\n',
        '                    await asyncio.to_thread(concurrent_sessions.purge)\n                    await asyncio.to_thread(reconcile_concurrent_resources)\n',
    )

pool_tests = 'emery vpn orchestrator/tests/test_pool_assignments.py'
if 'PoolReservationReleaseRequest' not in text(pool_tests):
    replace_once(
        pool_tests,
        '    PoolReservationPrepareRequest,\n    PoolReservationRebindRequest,\n)',
        '    PoolReservationPrepareRequest,\n    PoolReservationRebindRequest,\n    PoolReservationReleaseRequest,\n)',
    )
append_once(
    pool_tests,
    'def test_release_is_idempotent_and_frees_capacity',
    """
def test_release_is_idempotent_and_frees_capacity(db_session):
    node = add_node(db_session)
    transport = FakeCredentialTransport()
    service = PoolAssignmentService(db_session, transport)
    prepared = service.prepare(request('a'))
    service.confirm(PoolReservationConfirmRequest(
        assignment_id=prepared.assignment_id,
        confirmation_token=prepared.confirmation_token,
    ))
    req = PoolReservationReleaseRequest(
        assignment_id=prepared.assignment_id,
        subject_key='a' * 64,
    )
    first = service.release(req)
    second = service.release(req)
    assert first.status == second.status == 'revoked'
    assert db_session.get(VpnNode, node.id).current_clients == 0
    assert transport.removed.count(prepared.assignment_id) == 1


def test_release_refuses_stale_owner_after_rebind(db_session):
    add_node(db_session)
    service = PoolAssignmentService(db_session, FakeCredentialTransport())
    prepared = service.prepare(request('a'))
    service.confirm(PoolReservationConfirmRequest(
        assignment_id=prepared.assignment_id,
        confirmation_token=prepared.confirmation_token,
    ))
    service.rebind(PoolReservationRebindRequest(
        assignment_id=prepared.assignment_id,
        subject_key='b' * 64,
        entitlement_hash='c' * 64,
        entitlement_expires_at=datetime.now(timezone.utc) + timedelta(days=60),
    ))
    with pytest.raises(HTTPException) as error:
        service.release(PoolReservationReleaseRequest(
            assignment_id=prepared.assignment_id,
            subject_key='a' * 64,
        ))
    assert error.value.detail == 'assignment_owner_changed'
""",
)

allocator_tests = 'orchestrator/tests/test_concurrent_resource_allocator.py'
if 'release_candidate' not in text(allocator_tests).split(')', 1)[0]:
    replace_once(
        allocator_tests,
        '    resource_state,\n    transfer_assignment,\n)',
        '    resource_state,\n    release_candidate,\n    clear_assignment,\n    transfer_assignment,\n)',
    )
append_once(
    allocator_tests,
    'def test_downgrade_release_keeps_live_owner',
    """
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
""",
)

print('patch applied')

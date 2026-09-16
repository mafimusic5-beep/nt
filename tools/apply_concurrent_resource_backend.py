from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return (ROOT / path).read_text(encoding='utf-8')


def write(path, text):
    (ROOT / path).write_text(text, encoding='utf-8')


def replace_once(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f'{label}: expected exactly one match, got {count}')
    return text.replace(old, new, 1)


# 1) Registration must not reserve Xray capacity in concurrent mode.
p = 'orchestrator/device_auth.py'
s = read(p)
s = replace_once(
    s,
    "        if pool_bridge_enabled():\n            expires_at = str(activation['expires_at'] or '').strip()",
    "        if pool_bridge_enabled() and not concurrent_sessions.enabled():\n            expires_at = str(activation['expires_at'] or '').strip()",
    'device_auth legacy allocation guard',
)
write(p, s)

# 2) Concurrent-aware bridge: allocation/recycle stays under the same SQLite writer lock.
p = 'orchestrator/pool_reservation_bridge.py'
s = read(p)
s = replace_once(s, 'import hashlib\nimport hmac\nimport re\nimport uuid\n',
                 'import hashlib\nimport hmac\nimport re\nimport sqlite3\nimport uuid\n', 'bridge imports')
s = replace_once(
    s,
    'from config import (\n    POOL_BRIDGE_API_KEY,',
    'import concurrent_sessions\nfrom concurrent_resource_allocator import assignment_for_device, plan_resource, transfer_assignment\n\nfrom config import (\n    DATABASE_PATH,\n    POOL_BRIDGE_API_KEY,',
    'bridge concurrent imports',
)

insert_after = '''def prepare_assignment(\n    *,\n    formatted_code: str,\n    device_id: str,\n    plan: str,\n    expires_at: str,\n) -> dict[str, Any]:\n    region = POOL_BRIDGE_REGION_CODE if _REGION_RE.fullmatch(POOL_BRIDGE_REGION_CODE) else 'auto'\n    entitlement = _entitlement_hash(formatted_code, plan, expires_at)\n    response = _request(\n        '/api/v1/internal/pool/assignments/prepare',\n        {\n            'subject_type': 'legacy_device',\n            'subject_key': _subject_key(formatted_code, device_id),\n            'entitlement_hash': entitlement,\n            'entitlement_expires_at': expires_at,\n            'region_code': region,\n        },\n    )\n    result = _validated_assignment(response)\n    result['pool_entitlement_hash'] = entitlement\n    return result\n'''
addition = insert_after + '''\n\ndef rebind_assignment(\n    *,\n    assignment_id: int,\n    formatted_code: str,\n    device_id: str,\n    plan: str,\n    expires_at: str,\n) -> dict[str, Any]:\n    \"\"\"Move pool ownership metadata without creating a new Xray resource.\"\"\"\n    if assignment_id <= 0:\n        raise PoolBridgeError('invalid_pool_assignment', 409)\n    entitlement = _entitlement_hash(formatted_code, plan, expires_at)\n    response = _request(\n        '/api/v1/internal/pool/assignments/rebind',\n        {\n            'assignment_id': assignment_id,\n            'subject_type': 'legacy_device',\n            'subject_key': _subject_key(formatted_code, device_id),\n            'entitlement_hash': entitlement,\n            'entitlement_expires_at': expires_at,\n        },\n    )\n    result = _validated_assignment(response)\n    if int(result.get('pool_assignment_id') or 0) != assignment_id:\n        raise PoolBridgeError('pool_assignment_identity_changed', 503)\n    result['pool_entitlement_hash'] = entitlement\n    return result\n'''
s = replace_once(s, insert_after, addition, 'bridge rebind function')

start = s.index('def refresh_stored_assignment(raw_code: str, device_id: str) -> dict[str, Any]:')
end = s.index('\n\ndef apply_stored_assignment_policy(', start)
new_refresh = '''def _concurrent_entitlement_row(con: sqlite3.Connection, raw_code: str, device_id: str):\n    from storage import format_code\n\n    return con.execute(\n        \"\"\"\n        SELECT c.code, c.plan, c.expires_at, c.max_devices, d.id AS device_row_id\n        FROM activation_codes c\n        JOIN code_devices d ON d.code = c.code\n        WHERE c.code = ?\n          AND c.status = 'active'\n          AND d.device_id = ?\n          AND d.active = 1\n        \"\"\",\n        (format_code(raw_code), device_id.strip()[:128]),\n    ).fetchone()\n\n\ndef _refresh_concurrent_assignment(raw_code: str, device_id: str) -> dict[str, Any]:\n    from storage import save_device_pool_assignment, save_device_pool_assignment_in_connection\n\n    con = sqlite3.connect(DATABASE_PATH, timeout=30.0)\n    con.row_factory = sqlite3.Row\n    con.execute('PRAGMA foreign_keys = ON')\n    assignment: dict[str, Any] | None = None\n    code = ''\n    plan = ''\n    expires_at = ''\n    safe_device_id = device_id.strip()[:128]\n    try:\n        con.execute('BEGIN IMMEDIATE')\n        concurrent_sessions.ensure_storage(con)\n        row = _concurrent_entitlement_row(con, raw_code, safe_device_id)\n        if row is None:\n            raise PoolBridgeError('device_not_registered', 403)\n        code = str(row['code'])\n        plan = str(row['plan'] or '')\n        expires_at = str(row['expires_at'] or '').strip()\n        if not expires_at:\n            raise PoolBridgeError('entitlement_expiry_missing', 503)\n        limit = int(row['max_devices'] or 0)\n        if limit not in (1, 2, 5):\n            raise PoolBridgeError('plan_limit_mismatch', 403)\n\n        decision = plan_resource(\n            con,\n            code=code,\n            requesting_device_row_id=int(row['device_row_id']),\n            limit=limit,\n        )\n        if decision.action == 'busy':\n            raise PoolBridgeError('concurrent_limit_reached', 409)\n        if decision.action == 'owned':\n            assignment = assignment_for_device(con, device_row_id=int(row['device_row_id']))\n            if assignment is None:\n                raise PoolBridgeError('pool_assignment_missing', 409)\n        elif decision.action == 'recycle':\n            if decision.recyclable is None:\n                raise PoolBridgeError('pool_assignment_race', 409)\n            assignment = transfer_assignment(\n                con,\n                from_device_row_id=decision.recyclable.device_row_id,\n                to_device_row_id=int(row['device_row_id']),\n            )\n        elif decision.action == 'allocate':\n            assignment = prepare_assignment(\n                formatted_code=code,\n                device_id=safe_device_id,\n                plan=plan,\n                expires_at=expires_at,\n            )\n            try:\n                save_device_pool_assignment_in_connection(\n                    con, code, safe_device_id, assignment\n                )\n            except sqlite3.IntegrityError as exc:\n                raise PoolBridgeError('pool_assignment_race', 409) from exc\n        else:\n            raise PoolBridgeError('pool_assignment_race', 409)\n        con.commit()\n    except Exception:\n        con.rollback()\n        raise\n    finally:\n        con.close()\n\n    if assignment is None:\n        raise PoolBridgeError('pool_assignment_missing', 409)\n    if assignment.get('pool_status') == 'pending' and assignment.get('pool_confirmation_token'):\n        assignment['confirmation_required'] = True\n        assignment = confirm_persisted_assignment(assignment)\n    if assignment.get('pool_status') != 'active':\n        raise PoolBridgeError('pool_assignment_not_active', 409)\n\n    # Rebind is capacity-neutral and idempotent. It also repairs a previous\n    # partial transfer where local ownership committed but the pool was briefly\n    # unreachable before its subject metadata could be updated.\n    assignment = rebind_assignment(\n        assignment_id=int(assignment.get('pool_assignment_id') or 0),\n        formatted_code=code,\n        device_id=safe_device_id,\n        plan=plan,\n        expires_at=expires_at,\n    )\n    save_device_pool_assignment(code, safe_device_id, assignment)\n    return assignment\n\n\ndef refresh_stored_assignment(raw_code: str, device_id: str) -> dict[str, Any]:\n    \"\"\"Refresh or acquire the resource currently needed by this installation.\"\"\"\n    if concurrent_sessions.enabled():\n        return _refresh_concurrent_assignment(raw_code, device_id)\n\n    from storage import (\n        get_device_pool_assignment,\n        get_device_pool_entitlement,\n        save_device_pool_assignment,\n    )\n\n    entitlement = get_device_pool_entitlement(raw_code, device_id)\n    if not entitlement:\n        raise PoolBridgeError('device_not_registered', 403)\n\n    stored = get_device_pool_assignment(entitlement['code'], device_id)\n    if stored and stored.get('pool_status') == 'pending' and stored.get('pool_confirmation_token'):\n        try:\n            stored['confirmation_required'] = True\n            return confirm_persisted_assignment(stored)\n        except PoolBridgeError:\n            pass\n\n    prepared = prepare_assignment(\n        formatted_code=entitlement['code'],\n        device_id=device_id,\n        plan=entitlement['plan'],\n        expires_at=entitlement['expires_at'],\n    )\n    save_device_pool_assignment(entitlement['code'], device_id, prepared)\n    return confirm_persisted_assignment(prepared)\n'''
s = s[:start] + new_refresh + s[end:]
write(p, s)

# 3) Activation obtains a resource only after durable registration commits.
p = 'orchestrator/api.py'
s = read(p)
old = '''    if pool_bridge_enabled():\n        assignment = access.get('vpn_assignment')\n        if not isinstance(assignment, dict) or assignment.get('pool_status') != 'active':\n            return JSONResponse(status_code=503, content={'ok': False, 'reason': 'pool_assignment_unconfirmed'})\n        server = _pool_assignment_server(assignment)\n        revision = int(assignment.get('pool_config_revision') or 0)\n    else:\n'''
new = '''    if pool_bridge_enabled():\n        assignment = access.get('vpn_assignment')\n        if concurrent_sessions.enabled():\n            try:\n                assignment = refresh_stored_assignment(payload.code, header_device_id)\n            except PoolBridgeError as error:\n                return JSONResponse(\n                    status_code=error.status_code,\n                    content={'ok': False, 'reason': error.reason},\n                )\n        if not isinstance(assignment, dict) or assignment.get('pool_status') != 'active':\n            return JSONResponse(status_code=503, content={'ok': False, 'reason': 'pool_assignment_unconfirmed'})\n        server = _pool_assignment_server(assignment)\n        revision = int(assignment.get('pool_config_revision') or 0)\n    else:\n'''
s = replace_once(s, old, new, 'activation concurrent resource acquisition')
write(p, s)

# 4) Pool API supports capacity-neutral assignment rebind.
p = 'emery vpn orchestrator/src/backend/schemas/pool_bridge.py'
s = read(p)
marker = '''class PoolReservationConfirmRequest(BaseModel):\n'''
addition = '''class PoolReservationRebindRequest(BaseModel):\n    assignment_id: int = Field(gt=0)\n    subject_type: str = Field(default=\"legacy_device\", pattern=r\"^(legacy_device|native_device)$\")\n    subject_key: str = Field(pattern=r\"^[a-f0-9]{64}$\")\n    entitlement_hash: str = Field(pattern=r\"^[a-f0-9]{64}$\")\n    entitlement_expires_at: datetime\n\n\n'''
s = replace_once(s, marker, addition + marker, 'pool rebind schema')
write(p, s)

p = 'emery vpn orchestrator/src/backend/api/routes.py'
s = read(p)
s = replace_once(
    s,
    '    PoolReservationPrepareRequest,\n    PoolReservationResponse,',
    '    PoolReservationPrepareRequest,\n    PoolReservationRebindRequest,\n    PoolReservationResponse,',
    'route schema import',
)
route_marker = '''@router.post(\n    \"/internal/pool/assignments/confirm\",\n'''
route = '''@router.post(\n    \"/internal/pool/assignments/rebind\",\n    response_model=PoolReservationResponse,\n    dependencies=[Depends(require_pool_bridge_api_key)],\n)\ndef internal_rebind_pool_assignment(\n    payload: PoolReservationRebindRequest,\n    db: Session = Depends(get_db),\n):\n    return PoolAssignmentService(db).rebind(payload)\n\n\n'''
s = replace_once(s, route_marker, route + route_marker, 'pool rebind route')
write(p, s)

p = 'emery vpn orchestrator/src/backend/services/pool_assignment_service.py'
s = read(p)
s = replace_once(
    s,
    '    PoolReservationPrepareRequest,\n    PoolReservationResponse,',
    '    PoolReservationPrepareRequest,\n    PoolReservationRebindRequest,\n    PoolReservationResponse,',
    'service schema import',
)
method_marker = '''    def confirm(self, req: PoolReservationConfirmRequest) -> PoolReservationConfirmResponse:\n'''
method = '''    def rebind(self, req: PoolReservationRebindRequest) -> PoolReservationResponse:\n        \"\"\"Change the current resource owner without allocating or reinstalling Xray.\"\"\"\n        self._require_enabled()\n        now = self._now()\n        requested_expiry = self._as_utc(req.entitlement_expires_at)\n        if requested_expiry <= now:\n            raise HTTPException(status_code=403, detail=\"entitlement_expired\")\n\n        assignment = self.db.get(VpnAssignment, req.assignment_id)\n        if assignment is None:\n            raise HTTPException(status_code=404, detail=\"assignment_not_found\")\n        if assignment.status != \"active\" or not assignment.device_gate_enforced:\n            raise HTTPException(status_code=409, detail=\"assignment_not_active\")\n\n        conflict = self.db.scalar(\n            select(VpnAssignment).where(\n                VpnAssignment.subject_type == req.subject_type,\n                VpnAssignment.subject_key == req.subject_key,\n                VpnAssignment.id != assignment.id,\n            )\n        )\n        if conflict is not None:\n            raise HTTPException(status_code=409, detail=\"assignment_subject_conflict\")\n\n        assignment.subject_type = req.subject_type\n        assignment.subject_key = req.subject_key\n        stored_expiry = self._as_utc(assignment.entitlement_expires_at)\n        if requested_expiry >= stored_expiry:\n            assignment.entitlement_hash = req.entitlement_hash\n            assignment.entitlement_expires_at = req.entitlement_expires_at\n        self.audit.write(\n            \"system\",\n            \"pool_bridge\",\n            \"vpn_assignment_rebound\",\n            \"vpn_assignment\",\n            str(assignment.id),\n            {\"node_id\": assignment.node_id},\n        )\n        self.db.commit()\n        self.db.refresh(assignment)\n        return self._response(assignment)\n\n'''
s = replace_once(s, method_marker, method + method_marker, 'pool rebind service')
write(p, s)

# 5) Add backend tests for no capacity/credential churn during rebind.
p = 'emery vpn orchestrator/tests/test_pool_assignments.py'
s = read(p)
s = replace_once(
    s,
    '    PoolReservationPrepareRequest,\n)',
    '    PoolReservationPrepareRequest,\n    PoolReservationRebindRequest,\n)',
    'pool test schema import',
)
test = '''\n\ndef test_rebind_keeps_same_capacity_and_xray_resource(db_session):\n    node = add_node(db_session)\n    transport = FakeCredentialTransport()\n    service = PoolAssignmentService(db_session, transport)\n    prepared = service.prepare(request(\"a\"))\n    service.confirm(\n        PoolReservationConfirmRequest(\n            assignment_id=prepared.assignment_id,\n            confirmation_token=prepared.confirmation_token,\n        )\n    )\n    before = db_session.get(VpnAssignment, prepared.assignment_id)\n    before_uuid = before.client_uuid\n    before_port = before.client_port\n    before_revision = before.config_revision\n    before_clients = db_session.get(VpnNode, node.id).current_clients\n\n    rebound = service.rebind(\n        PoolReservationRebindRequest(\n            assignment_id=prepared.assignment_id,\n            subject_key=\"b\" * 64,\n            entitlement_hash=\"c\" * 64,\n            entitlement_expires_at=datetime.now(timezone.utc) + timedelta(days=60),\n        )\n    )\n\n    current = db_session.get(VpnAssignment, prepared.assignment_id)\n    assert rebound.assignment_id == prepared.assignment_id\n    assert rebound.config == prepared.config\n    assert current.subject_key == \"b\" * 64\n    assert current.client_uuid == before_uuid\n    assert current.client_port == before_port\n    assert current.config_revision == before_revision\n    assert db_session.get(VpnNode, node.id).current_clients == before_clients\n    assert transport.installed.count(prepared.assignment_id) == 1\n\n\ndef test_rebind_refuses_inactive_assignment(db_session):\n    add_node(db_session)\n    service = PoolAssignmentService(db_session, FakeCredentialTransport())\n    prepared = service.prepare(request(\"a\"))\n\n    with pytest.raises(HTTPException) as error:\n        service.rebind(\n            PoolReservationRebindRequest(\n                assignment_id=prepared.assignment_id,\n                subject_key=\"b\" * 64,\n                entitlement_hash=\"c\" * 64,\n                entitlement_expires_at=datetime.now(timezone.utc) + timedelta(days=60),\n            )\n        )\n\n    assert error.value.status_code == 409\n    assert error.value.detail == \"assignment_not_active\"\n'''
s += test
write(p, s)

# 6) Update draft notes: resource ownership blocker is implemented but rollout still blocked.
p = 'docs/concurrent-vpn-sessions.md'
s = read(p)
s = s.replace(
    '1. Pool allocation is still tied to installation registration. Removing the\n   registration limit allows repeated installs to reserve additional Xray ports\n   and node capacity. Redesign allocation to reserve at most the paid concurrent\n   capacity, with safe reassignment only after the old lease is closed/expired;\n   preserve regional policy and per-device speed limits. Do not enable this\n   patch alone: it would regress capacity use despite correct admission limits.\n',
    '1. Pool allocation is now separated from registration: at most the paid 1/2/5\n   concurrent resources are owned per code, and an assignment may move only from\n   a device with no live lease. Pool rebind keeps the same Xray UUID/port and does\n   not increment node capacity. This still requires integration/production\n   validation before the feature flags may be enabled.\n',
    1,
)
write(p, s)

# Syntax-only validation is intentionally part of this patch helper; full tests run in CI.
for path in [
    'orchestrator/concurrent_resource_allocator.py',
    'orchestrator/pool_reservation_bridge.py',
    'orchestrator/device_auth.py',
    'orchestrator/api.py',
    'emery vpn orchestrator/src/backend/schemas/pool_bridge.py',
    'emery vpn orchestrator/src/backend/api/routes.py',
    'emery vpn orchestrator/src/backend/services/pool_assignment_service.py',
]:
    compile(read(path), path, 'exec')

print('concurrent resource backend patch applied')
